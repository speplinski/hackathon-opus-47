"""One-shot smoke for the L8 design-optimize on one cluster.

Companion to :mod:`auditable_design.layers.l8_optimize`. Takes one
cluster's ReconciledVerdict (L5) + PriorityScore (L6) + DesignDecision
(L7) + cluster context, calls Claude once to re-audit the decision's
after_snapshot, and emits two OptimizationIteration records (baseline
+ proposed) plus Pareto verdict, native payload, provenance, and two
.md design artefact files.

Text-only. L8 consumes structured before/after snapshots + a baseline
heuristic list; no screenshots.

Output
------
Per-(cluster, model) suffix:

* ``l8_optimization_iterations_{clusterNN}_<modelshort>.jsonl`` (2 rows: baseline + proposed)
* ``l8_optimization_iterations_{clusterNN}_<modelshort>.native.jsonl`` (1 row)
* ``l8_optimization_iterations_{clusterNN}_<modelshort>.provenance.json``
* ``artifacts/<modelshort>/{cluster_id}_iter00.md`` (before_snapshot + baseline scores)
* ``artifacts/<modelshort>/{cluster_id}_iter01.md`` (after_snapshot + re-audit + Pareto verdict)

Per-model artefact subdirectory prevents filename collisions across
the matched-model grid.
"""

from __future__ import annotations

import argparse
import hashlib
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
from auditable_design.layers.l6_weight import load_reconciled_verdicts  # noqa: E402
from auditable_design.layers.l7_decide import load_priority_scores  # noqa: E402
from auditable_design.layers.l8_optimize import (  # noqa: E402
    BASELINE_SKILL_ID,
    MAX_TOKENS,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    OptimizeParseError,
    build_baseline_iteration,
    build_user_message,
    load_decisions,
    parse_optimize_response,
    reconciled_heuristic_list,
    skill_hash,
)
from auditable_design.schemas import (  # noqa: E402
    DesignDecision,
    InsightCluster,
    OptimizationIteration,
    PriorityScore,
    ReconciledVerdict,
)

DEFAULT_RECONCILED = (
    _REPO_ROOT
    / "data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl"
)
DEFAULT_PRIORITY = (
    _REPO_ROOT
    / "data/derived/l6_weight/l6_priority_cluster02_opus46.jsonl"
)
DEFAULT_DECISIONS = (
    _REPO_ROOT
    / "data/derived/l7_decide/l7_design_decisions_cluster02_opus46.jsonl"
)
DEFAULT_CLUSTERS = (
    _REPO_ROOT
    / "data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl"
)
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/l8_optimize"
DEFAULT_MODEL = "claude-opus-4-7"


def _load_cluster(path: Path, cluster_id: str) -> InsightCluster:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("cluster_id") == cluster_id:
            return InsightCluster.model_validate(row)
    raise RuntimeError(f"cluster_id={cluster_id!r} not found in {path}")


_MODEL_SHORT = {
    "claude-opus-4-6": "opus46",
    "claude-sonnet-4-6": "sonnet46",
    "claude-opus-4-7": "opus47",
    "claude-haiku-4-5": "haiku45",
}


def _short_model_name(model: str) -> str:
    for full, short in _MODEL_SHORT.items():
        if model.startswith(full):
            return short
    return model.replace("/", "_")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _call_once(
    *,
    cluster: InsightCluster,
    reconciled: ReconciledVerdict,
    decision: DesignDecision,
    model: str,
) -> tuple[anthropic.types.Message, str]:
    client = anthropic.Anthropic()
    user_text = build_user_message(cluster, reconciled, decision)
    kwargs: dict[str, object] = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_text}],
    }
    if not _omits_sampling_params(model):
        kwargs["temperature"] = TEMPERATURE
    message = client.messages.create(**kwargs)
    chunks: list[str] = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            chunks.append(block.text)
    return message, "".join(chunks)


def _build_provenance(
    *,
    cluster_id: str,
    model: str,
    baseline: OptimizationIteration,
    proposed: OptimizationIteration,
    verdict_obj,  # ParetoVerdict
    reason: str | None,
    input_tokens: int,
    output_tokens: int,
    sh: str,
    reconciled_sha256: str,
    priority_sha256: str,
    decisions_sha256: str,
) -> dict[str, Any]:
    optimized = 1 if reason is None else 0
    fallback = 1 - optimized

    baseline_scores = baseline.scores[BASELINE_SKILL_ID]
    proposed_scores = proposed.scores[BASELINE_SKILL_ID]

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "mode": "text_direct_sdk",
        "modality": "text",
        "cluster_count": 1,
        "optimized_count": optimized,
        "fallback_count": fallback,
        "transport_failure_count": 0,
        "accepted_count": 1 if verdict_obj.accepted else 0,
        "rejected_count": 0 if verdict_obj.accepted else 1,
        "pareto_dominance": verdict_obj.dominance,
        "pareto_accepted": verdict_obj.accepted,
        "pareto_regression_count": verdict_obj.regression_count,
        "pareto_reason": verdict_obj.reason,
        "delta_per_heuristic": dict(verdict_obj.delta_per_heuristic),
        "baseline_scores": dict(baseline_scores),
        "proposed_scores": dict(proposed_scores),
        "baseline_severity_sum": sum(baseline_scores.values()),
        "proposed_severity_sum": sum(proposed_scores.values()),
        "fallback_reasons": (
            [{"cluster_id": cluster_id, "reason": reason}]
            if reason is not None
            else []
        ),
        "transport_failures": [],
        "skill_hash": sh,
        "reconciled_sha256": reconciled_sha256,
        "priority_sha256": priority_sha256,
        "decisions_sha256": decisions_sha256,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke for L8 design-optimize on one cluster. Re-audits "
            "the L7 decision's after_snapshot against the L5 reconciled "
            "baseline heuristic list, applies Pareto + weighted-sum, "
            "emits baseline + proposed iteration records."
        )
    )
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--priority", type=Path, default=DEFAULT_PRIORITY)
    parser.add_argument("--decisions", type=Path, default=DEFAULT_DECISIONS)
    parser.add_argument("--clusters", type=Path, default=DEFAULT_CLUSTERS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--suffix", default=None)
    parser.add_argument(
        "--max-regression", type=int, default=DEFAULT_MAX_REGRESSION,
    )
    args = parser.parse_args(argv)

    reconciled_map = load_reconciled_verdicts(args.reconciled)
    if len(reconciled_map) != 1:
        raise RuntimeError(
            f"expected exactly one reconciled verdict in {args.reconciled}, "
            f"got {len(reconciled_map)}"
        )
    cluster_id, reconciled = next(iter(reconciled_map.items()))

    priority_map = load_priority_scores(args.priority)
    if cluster_id not in priority_map:
        raise RuntimeError(
            f"no priority score for cluster_id={cluster_id!r} in "
            f"{args.priority}"
        )
    priority = priority_map[cluster_id]

    decision_map = load_decisions(args.decisions)
    if cluster_id not in decision_map:
        raise RuntimeError(
            f"no decision for cluster_id={cluster_id!r} in {args.decisions}"
        )
    decision = decision_map[cluster_id]

    cluster = _load_cluster(args.clusters, cluster_id)

    sh = skill_hash()
    reconciled_sha256 = _sha256(args.reconciled)
    priority_sha256 = _sha256(args.priority)
    decisions_sha256 = _sha256(args.decisions)

    if args.suffix is None:
        short = _short_model_name(args.model)
        suffix = f"_{short}"
    else:
        suffix = args.suffix.lstrip("_")
        short = suffix  # for artifact subdir
        suffix = f"_{suffix}"

    # Per-model artifact subdirectory keeps iter00.md / iter01.md
    # filenames from colliding across the matched-model grid.
    artifacts_dir = args.out_dir / "artifacts" / short
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    run_id = f"l8-smoke-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"

    print(
        f"smoke: cluster={cluster_id} model={args.model} "
        f"reconciled_sha={reconciled_sha256[:16]}… "
        f"priority_sha={priority_sha256[:16]}… "
        f"decisions_sha={decisions_sha256[:16]}… "
        f"skill_hash={sh[:16]}…",
        flush=True,
    )

    # Iteration 0 — baseline.
    baseline = build_baseline_iteration(
        cluster, reconciled, decision, artifacts_dir, run_id=run_id
    )

    # Iteration 1 — Claude re-audit.
    baseline_heuristics = reconciled_heuristic_list(reconciled)
    message, text = _call_once(
        cluster=cluster, reconciled=reconciled, decision=decision, model=args.model,
    )
    usage = message.usage
    input_tokens = int(usage.input_tokens)
    output_tokens = int(usage.output_tokens)

    parse_error: str | None = None
    payload: dict[str, Any] | None = None
    try:
        payload = parse_optimize_response(
            text, baseline_heuristics=baseline_heuristics
        )
    except OptimizeParseError as e:
        parse_error = str(e)

    baseline_scores = baseline.scores[BASELINE_SKILL_ID]
    if payload is not None:
        proposed_scores = dict(payload["scored_heuristics"])
        reasoning_text = payload["reasoning"]
    else:
        proposed_scores = dict(baseline_scores)  # no-op fallback
        reasoning_text = (
            f"Fallback — parse failure on re-audit; proposed iteration "
            f"copies baseline scores. Parse error: {parse_error}"
        )

    v = pareto_verdict(
        parent=baseline_scores,
        child=proposed_scores,
        max_regression=args.max_regression,
    )

    # Write iteration 1 artifact.
    proposed_artifact = artifacts_dir / f"{cluster.cluster_id}_iter01.md"
    body = (
        f"# {cluster.cluster_id} — iteration 1 (proposed, model={args.model})\n\n"
        f"## Parent iteration\n{baseline.iteration_id}\n\n"
        f"## after_snapshot (from L7 decision {decision.decision_id})\n"
        f"{decision.after_snapshot}\n\n"
        f"## Re-audit severities\n"
    )
    for h in baseline_heuristics:
        body += (
            f"- `{h}` — baseline {baseline_scores[h]} → proposed "
            f"{proposed_scores[h]} (delta {v.delta_per_heuristic.get(h, 0):+d})\n"
        )
    body += f"\n## Model reasoning\n{reasoning_text}\n\n"
    body += f"## Pareto verdict\n{v.reason}\n"
    proposed_artifact.write_text(body, encoding="utf-8")

    proposed = OptimizationIteration(
        iteration_id=baseline.iteration_id.replace("__00", "__01"),
        run_id=run_id,
        iteration_index=1,
        parent_iteration_id=baseline.iteration_id,
        design_artifact_ref=str(proposed_artifact),
        scores={BASELINE_SKILL_ID: proposed_scores},
        reasoning=reasoning_text,
        accepted=v.accepted,
        regression_reason=v.reason if not v.accepted else None,
        delta_per_heuristic=dict(v.delta_per_heuristic),
        informing_review_ids=list(cluster.member_review_ids),
        recorded_at=datetime.now(UTC),
    )

    # Write outputs.
    cluster_stem = cluster.cluster_id.replace("_", "")
    iterations_path = args.out_dir / f"l8_optimization_iterations_{cluster_stem}{suffix}.jsonl"
    native_path = args.out_dir / f"l8_optimization_iterations_{cluster_stem}{suffix}.native.jsonl"
    prov_path = args.out_dir / f"l8_optimization_iterations_{cluster_stem}{suffix}.provenance.json"

    args.out_dir.mkdir(parents=True, exist_ok=True)
    iterations_path.write_text(
        "\n".join(
            json.dumps(it.model_dump(mode="json"), ensure_ascii=False)
            for it in (baseline, proposed)
        )
        + "\n",
        encoding="utf-8",
    )

    native_row = {
        "cluster_id": cluster.cluster_id,
        "status": "optimized" if parse_error is None else "fallback",
        "reason": parse_error,
        "pareto_accepted": v.accepted,
        "pareto_reason": v.reason,
        "pareto_dominance": v.dominance,
        "pareto_regression_count": v.regression_count,
        "payload": (
            payload if payload is not None
            else {
                "fallback": True,
                "reason": parse_error,
                "raw_response": text,
            }
        ),
    }
    native_path.write_text(
        json.dumps(native_row, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    prov_path.write_text(
        json.dumps(
            _build_provenance(
                cluster_id=cluster.cluster_id,
                model=args.model,
                baseline=baseline,
                proposed=proposed,
                verdict_obj=v,
                reason=parse_error,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                sh=sh,
                reconciled_sha256=reconciled_sha256,
                priority_sha256=priority_sha256,
                decisions_sha256=decisions_sha256,
            ),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    status = "optimized" if parse_error is None else "fallback"
    accept_tag = "ACCEPTED" if v.accepted else "REJECTED"
    print(
        f"done: status={status} {accept_tag}\n"
        f"  verdict: {v.reason[:150]}\n"
        f"  baseline sum={sum(baseline_scores.values())} → "
        f"proposed sum={sum(proposed_scores.values())}\n"
        f"  regression_count={v.regression_count} dominance={v.dominance}\n"
        f"  input_tokens={input_tokens} output_tokens={output_tokens}\n"
        f"  iterations: {iterations_path.relative_to(_REPO_ROOT)}\n"
        f"  native:     {native_path.relative_to(_REPO_ROOT)}\n"
        f"  provenance: {prov_path.relative_to(_REPO_ROOT)}\n"
        f"  artifacts:  {artifacts_dir.relative_to(_REPO_ROOT)}",
        flush=True,
    )
    return 0 if status == "optimized" else 1


if __name__ == "__main__":
    sys.exit(main())
