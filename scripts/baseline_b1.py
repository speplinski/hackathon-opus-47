"""Baseline B1 — single-shot naive "redesign the paywall" prompt.

The full pipeline (L1→L8+loop) treats a product complaint corpus as
evidence, clusters it, audits each cluster under six design
perspectives, reconciles cross-skill tensions, prioritises, decides,
and iteratively refines. B1 is the **strawman**: one Claude call
with the raw cluster quotes and the instruction "redesign this
surface." No audit skills, no reconciliation, no loop, no Pareto.

Purpose. The pitch differentiator for Auditable Design is that the
same model, handed raw complaints with a naive prompt, produces
output that scores **worse on the same heuristic list** than the
full pipeline's final parent. B1 quantifies that delta.

Method.
1. Single Claude call (Opus 4.7, same model the pipeline's strong-
   model baseline uses) with a minimal system prompt ("You are a
   product designer redesigning a paywall surface") and a user
   message containing the cluster's representative quotes +
   member-review metadata + the `before_snapshot` from L7 for
   comparability.
2. The model emits a prose `after_snapshot` of the redesigned
   surface.
3. The baseline heuristic list is loaded from L5 reconciled (the
   same list the full pipeline's L8 uses).
4. A second Claude call under `design-optimize` re-audits B1's
   `after_snapshot` against that heuristic list. This is the
   apples-to-apples comparison — same skill, same list, different
   input.
5. Output: B1 `after_snapshot`, per-heuristic severity scores,
   severity sum, Pareto + Tchebycheff verdict vs L5 baseline.

The re-audit is invoked as a helper function from
:mod:`auditable_design.layers.l8_optimize` — no duplicate code.

Output
------
* ``data/derived/baseline_b1/baseline_b1_cluster{NN}_{modelshort}.jsonl``
  (1 row: the re-audit outcome structured like an OptimizationIteration)
* ``…native.jsonl`` (raw Claude responses for both calls)
* ``…provenance.json``
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anthropic

# Make `src/` importable without requiring `uv run` wrapping.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.claude_client import _omits_sampling_params  # noqa: E402
from auditable_design.evaluators.pareto import (  # noqa: E402
    DEFAULT_MAX_REGRESSION,
    verdict as pareto_verdict,
)
from auditable_design.evaluators.tchebycheff import (  # noqa: E402
    DEFAULT_MIN_IMPROVEMENT_PCT,
    verdict as tchebycheff_verdict,
)
from auditable_design.layers.l6_weight import (  # noqa: E402
    load_reconciled_verdicts,
)
from auditable_design.layers.l8_optimize import (  # noqa: E402
    BASELINE_SKILL_ID,
    MAX_TOKENS as REAUDIT_MAX_TOKENS,
    SYSTEM_PROMPT as REAUDIT_SYSTEM_PROMPT,
    TEMPERATURE as REAUDIT_TEMPERATURE,
    load_decisions,
    parse_optimize_response,
    reconciled_heuristic_list,
    skill_hash as reaudit_skill_hash,
)
from auditable_design.schemas import InsightCluster  # noqa: E402


DEFAULT_RECONCILED = (
    _REPO_ROOT
    / "data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl"
)
DEFAULT_DECISIONS = (
    _REPO_ROOT
    / "data/derived/l7_decide/l7_design_decisions_cluster02_opus46.jsonl"
)
DEFAULT_CLUSTERS = (
    _REPO_ROOT
    / "data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl"
)
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/baseline_b1"
DEFAULT_MODEL = "claude-opus-4-7"


_MODEL_SHORT = {
    "claude-opus-4-6": "opus46",
    "claude-sonnet-4-6": "sonnet46",
    "claude-opus-4-7": "opus47",
}


def _short_model(model: str) -> str:
    for full, short in _MODEL_SHORT.items():
        if model.startswith(full):
            return short
    return model.replace("/", "_")


B1_SYSTEM_PROMPT = """You are a senior product designer. You redesign \
user-facing surfaces to address user complaints. Given a cluster of \
real user reviews about a mobile app feature plus a description of \
the current surface, emit a concrete redesign of that surface.

Output format. Respond with a single JSON object containing:

- "after_snapshot": a single paragraph (150-350 words) describing \
the redesigned surface in concrete UI terms. Name specific elements \
(modals, buttons, screens), placement, copy, visual weight, tap \
targets. Write as though a designer will implement it verbatim.
- "reasoning": 2-3 sentences explaining the main structural change \
and why the user complaints warrant it.

Do not output markdown fences. Do not include other keys.
"""


def _load_cluster(path: Path, cluster_id: str) -> InsightCluster:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("cluster_id") == cluster_id:
            return InsightCluster.model_validate(row)
    raise RuntimeError(f"cluster_id={cluster_id!r} not found in {path}")


def _build_b1_user_message(
    cluster: InsightCluster, before_snapshot: str
) -> str:
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
    parts = [
        "<cluster>",
        f"  <label>{cluster.label.translate(escape)}</label>",
    ]
    if cluster.ui_context:
        parts.append(
            f"  <ui_context>{cluster.ui_context.translate(escape)}</ui_context>"
        )
    for i, q in enumerate(cluster.representative_quotes):
        parts.append(f'  <q idx="{i}">{q.translate(escape)}</q>')
    parts.append("</cluster>")
    parts.append(
        f"<before_snapshot>{before_snapshot.translate(escape)}</before_snapshot>"
    )
    parts.append(
        "<task>Redesign the described surface to resolve the "
        "complaints in the cluster. Emit the revised surface as "
        "after_snapshot per the output format.</task>"
    )
    return "\n".join(parts)


def _extract_json_object(text: str) -> dict[str, Any]:
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"no JSON object in B1 response: {text!r}")
    return json.loads(m.group(0))


def _call_model(
    *, system: str, user: str, model: str, max_tokens: int,
) -> tuple[anthropic.types.Message, str]:
    client = anthropic.Anthropic()
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if not _omits_sampling_params(model):
        kwargs["temperature"] = 0.0
    message = client.messages.create(**kwargs)
    chunks: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return message, "".join(chunks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Baseline B1 — naive single-shot prompt + design-optimize "
            "re-audit on the same baseline heuristic list. Apples-to-"
            "apples comparison vs L8 thin spine and L8 loop."
        )
    )
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--clusters", type=Path, default=DEFAULT_CLUSTERS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    reconciled_map = load_reconciled_verdicts(args.reconciled)
    if len(reconciled_map) != 1:
        raise RuntimeError(
            f"expected exactly one reconciled verdict in {args.reconciled}"
        )
    cluster_id, reconciled = next(iter(reconciled_map.items()))

    decision = load_decisions(args.decisions)[cluster_id]
    cluster = _load_cluster(args.clusters, cluster_id)

    short = _short_model(args.model)
    cluster_stem = cluster_id.replace("_", "")
    suffix = f"{cluster_stem}_{short}"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    iter_out = args.out_dir / f"baseline_b1_{suffix}.jsonl"
    native_out = args.out_dir / f"baseline_b1_{suffix}.native.jsonl"
    prov_out = args.out_dir / f"baseline_b1_{suffix}.provenance.json"

    # Step 1 — Naive single-shot generation.
    print(f"[b1] cluster={cluster_id} model={args.model}", flush=True)
    b1_user = _build_b1_user_message(cluster, decision.before_snapshot)
    b1_msg, b1_text = _call_model(
        system=B1_SYSTEM_PROMPT,
        user=b1_user,
        model=args.model,
        max_tokens=2048,
    )
    b1_payload = _extract_json_object(b1_text)
    b1_snapshot = str(b1_payload["after_snapshot"])
    b1_reasoning = str(b1_payload["reasoning"])
    b1_in = b1_msg.usage.input_tokens
    b1_out = b1_msg.usage.output_tokens
    print(f"[b1] generated after_snapshot ({len(b1_snapshot.split())} words)", flush=True)

    # Step 2 — Re-audit B1's snapshot under design-optimize.
    baseline_heuristics = reconciled_heuristic_list(reconciled)
    baseline_scores = {
        v.heuristic: int(v.severity) for v in reconciled.ranked_violations
    }

    # Build the re-audit user message — same XML envelope L8 uses.
    escape = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
    reaudit_parts = [
        "<cluster>",
        f"  <cluster_id>{cluster.cluster_id.translate(escape)}</cluster_id>",
        f"  <label>{cluster.label.translate(escape)}</label>",
    ]
    if cluster.ui_context:
        reaudit_parts.append(
            f"  <ui_context>{cluster.ui_context.translate(escape)}</ui_context>"
        )
    for i, q in enumerate(cluster.representative_quotes):
        reaudit_parts.append(
            f'  <q idx="{i}">{q.translate(escape)}</q>'
        )
    reaudit_parts.append("</cluster>")
    reaudit_parts.append(
        f"<before_snapshot>{decision.before_snapshot.translate(escape)}</before_snapshot>"
    )
    reaudit_parts.append(
        f"<after_snapshot>{b1_snapshot.translate(escape)}</after_snapshot>"
    )
    reaudit_parts.append("<baseline_heuristics>")
    for v in reconciled.ranked_violations:
        reaudit_parts.append(
            f'  <h slug="{v.heuristic.translate(escape)}" '
            f'severity="{v.severity}">'
            f"{v.violation.translate(escape)}"
            f"</h>"
        )
    reaudit_parts.append("</baseline_heuristics>")
    reaudit_user = "\n".join(reaudit_parts)

    reaudit_msg, reaudit_text = _call_model(
        system=REAUDIT_SYSTEM_PROMPT,
        user=reaudit_user,
        model=args.model,
        max_tokens=REAUDIT_MAX_TOKENS,
    )
    reaudit_payload = parse_optimize_response(
        reaudit_text, baseline_heuristics=baseline_heuristics
    )
    b1_scores = dict(reaudit_payload["scored_heuristics"])
    reaudit_reasoning = str(reaudit_payload["reasoning"])
    re_in = reaudit_msg.usage.input_tokens
    re_out = reaudit_msg.usage.output_tokens
    print(f"[b1] re-audit scored severity_sum={sum(b1_scores.values())}", flush=True)

    # Step 3 — Verdicts (both verifiers, for completeness).
    pv = pareto_verdict(
        parent=baseline_scores,
        child=b1_scores,
        max_regression=DEFAULT_MAX_REGRESSION,
    )
    tv = tchebycheff_verdict(
        parent=baseline_scores,
        child=b1_scores,
        min_improvement_pct=DEFAULT_MIN_IMPROVEMENT_PCT,
    )

    # Step 4 — Write outputs.
    iter_row = {
        "iteration_id": f"b1__{cluster_id}",
        "run_id": f"b1-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}",
        "iteration_index": 1,  # B1 is a "one-shot iteration" peer to L8 iter 1
        "parent_iteration_id": None,
        "design_artifact_ref": str(args.out_dir / f"baseline_b1_{suffix}.md"),
        "scores": {BASELINE_SKILL_ID: b1_scores},
        "reasoning": reaudit_reasoning,
        "b1_generation_reasoning": b1_reasoning,
        "baseline_severity_sum": sum(baseline_scores.values()),
        "b1_severity_sum": sum(b1_scores.values()),
        "delta_per_heuristic": dict(pv.delta_per_heuristic),
        "pareto_accepted": pv.accepted,
        "pareto_reason": pv.reason,
        "tchebycheff_accepted": tv.accepted,
        "tchebycheff_reason": tv.reason,
        "informing_review_ids": list(cluster.member_review_ids),
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    iter_out.write_text(
        json.dumps(iter_row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Write .md artifact for demo.
    art_path = args.out_dir / f"baseline_b1_{suffix}.md"
    art_body = (
        f"# {cluster_id} — baseline B1 (naive single-shot, {args.model})\n\n"
        f"## Cluster label\n{cluster.label}\n\n"
        f"## before_snapshot (from L7 for comparability)\n{decision.before_snapshot}\n\n"
        f"## B1 after_snapshot\n{b1_snapshot}\n\n"
        f"## B1 generation reasoning\n{b1_reasoning}\n\n"
        f"## Re-audit severities (design-optimize on baseline heuristic list)\n"
    )
    for h in baseline_heuristics:
        art_body += (
            f"- `{h}` — baseline {baseline_scores[h]} → B1 "
            f"{b1_scores[h]} (delta {pv.delta_per_heuristic.get(h, 0):+d})\n"
        )
    art_body += f"\n## Re-audit reasoning\n{reaudit_reasoning}\n\n"
    art_body += (
        f"## Pareto verdict\n{pv.reason}\n\n"
        f"## Tchebycheff verdict\n{tv.reason}\n"
    )
    art_path.write_text(art_body, encoding="utf-8")

    # Native payload (both Claude responses verbatim).
    native_out.write_text(
        json.dumps(
            {
                "b1_generation": {
                    "payload": b1_payload,
                    "raw_text": b1_text,
                    "input_tokens": b1_in,
                    "output_tokens": b1_out,
                },
                "reaudit": {
                    "payload": reaudit_payload,
                    "raw_text": reaudit_text,
                    "input_tokens": re_in,
                    "output_tokens": re_out,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    # Provenance.
    provenance = {
        "schema_version": 1,
        "layer": "baseline_b1",
        "baseline_kind": "single_shot_naive_prompt",
        "model": args.model,
        "cluster_id": cluster_id,
        "baseline_severity_sum": sum(baseline_scores.values()),
        "b1_severity_sum": sum(b1_scores.values()),
        "severity_reduction_pct": (
            100.0
            * (sum(baseline_scores.values()) - sum(b1_scores.values()))
            / max(1, sum(baseline_scores.values()))
        ),
        "pareto_accepted": pv.accepted,
        "tchebycheff_accepted": tv.accepted,
        "b1_generation": {
            "input_tokens": b1_in,
            "output_tokens": b1_out,
        },
        "reaudit": {
            "input_tokens": re_in,
            "output_tokens": re_out,
            "skill_hash": reaudit_skill_hash(),
        },
        "baseline_scores": baseline_scores,
        "b1_scores": b1_scores,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    prov_out.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"[b1] iteration  → {iter_out}", flush=True)
    print(f"[b1] artifact   → {art_path}", flush=True)
    print(f"[b1] native     → {native_out}", flush=True)
    print(f"[b1] provenance → {prov_out}", flush=True)
    print(
        f"[b1] baseline sum {sum(baseline_scores.values())} → "
        f"B1 sum {sum(b1_scores.values())} "
        f"({provenance['severity_reduction_pct']:.0f}% reduction) | "
        f"pareto_accepted={pv.accepted} tcheb_accepted={tv.accepted}",
        flush=True,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
