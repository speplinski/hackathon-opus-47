"""One-shot smoke for the L7 design-decide on one cluster.

Companion to :mod:`auditable_design.layers.l7_decide`. Takes one
cluster's ReconciledVerdict (L5) + PriorityScore (L6) + cluster
context, calls Claude once (single-pass generation), parses the
``{principle, decision}`` payload through the module's parser (which
cross-validates `derived_from_review_ids ⊆ cluster.member_review_ids`
and `resolves_heuristics ⊆ reconciled ranked heuristics`), and writes
principles + decisions + native + provenance. Zero cache interaction.

Text-only. L7 consumes structured reconciled + prioritised evidence.

Output
------
Per-(cluster, model) suffix:

* ``l7_design_principles_{clusterNN}_<modelshort>.jsonl``
* ``l7_design_decisions_{clusterNN}_<modelshort>.jsonl``
* ``l7_design_decisions_{clusterNN}_<modelshort>.native.jsonl``
* ``l7_design_principles_{clusterNN}_<modelshort>.provenance.json``

Cluster stem derived from loaded cluster's ``cluster_id``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import anthropic

# Make `src/` importable without requiring `uv run` wrapping.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.claude_client import _omits_sampling_params  # noqa: E402
from auditable_design.layers.l6_weight import load_reconciled_verdicts  # noqa: E402
from auditable_design.layers.l7_decide import (  # noqa: E402
    MAX_TOKENS,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    DecideParseError,
    _build_decision,
    _build_principle,
    _decision_id,
    _principle_id,
    build_user_message,
    load_priority_scores,
    parse_decide_response,
    skill_hash,
)
from auditable_design.schemas import (  # noqa: E402
    DesignDecision,
    DesignPrinciple,
    InsightCluster,
)

DEFAULT_RECONCILED = (
    _REPO_ROOT
    / "data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl"
)
DEFAULT_PRIORITY = (
    _REPO_ROOT
    / "data/derived/l6_weight/l6_priority_cluster02_opus46.jsonl"
)
DEFAULT_CLUSTERS = (
    _REPO_ROOT
    / "data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl"
)
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/l7_decide"
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
    reconciled,  # ReconciledVerdict
    priority,  # PriorityScore
    model: str,
) -> tuple[anthropic.types.Message, str]:
    client = anthropic.Anthropic()
    user_text = build_user_message(cluster, reconciled, priority)
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
    principle: DesignPrinciple | None,
    decision: DesignDecision | None,
    reason: str | None,
    input_tokens: int,
    output_tokens: int,
    sh: str,
    reconciled_sha256: str,
    priority_sha256: str,
) -> dict:
    decided = 1 if reason is None else 0
    fallback = 1 - decided
    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "mode": "text_direct_sdk",
        "modality": "text",
        "cluster_count": 1,
        "decided_count": decided,
        "fallback_count": fallback,
        "transport_failure_count": 0,
        "principle_name": principle.name if principle else None,
        "principle_statement": principle.statement if principle else None,
        "derived_from_review_ids_count": (
            len(principle.derived_from_review_ids) if principle else 0
        ),
        "resolves_heuristics_count": (
            len(decision.resolves_heuristics) if decision else 0
        ),
        "resolves_heuristics": (
            list(decision.resolves_heuristics) if decision else []
        ),
        "fallback_reasons": (
            [{"cluster_id": cluster_id, "reason": reason}]
            if reason is not None
            else []
        ),
        "transport_failures": [],
        "skill_hash": sh,
        "reconciled_sha256": reconciled_sha256,
        "priority_sha256": priority_sha256,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke for L7 design-decide on one cluster. Reads "
            "ReconciledVerdict + PriorityScore + cluster context; "
            "emits DesignPrinciple + DesignDecision."
        )
    )
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--priority", type=Path, default=DEFAULT_PRIORITY)
    parser.add_argument("--clusters", type=Path, default=DEFAULT_CLUSTERS)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--suffix", default=None)
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
            f"{args.priority} (have: {sorted(priority_map)})"
        )
    priority = priority_map[cluster_id]

    cluster = _load_cluster(args.clusters, cluster_id)

    sh = skill_hash()
    reconciled_sha256 = _sha256(args.reconciled)
    priority_sha256 = _sha256(args.priority)

    if args.suffix is None:
        short = _short_model_name(args.model)
        suffix = f"_{short}"
    else:
        suffix = args.suffix

    print(
        f"smoke: cluster={cluster_id} model={args.model} "
        f"reconciled_sha={reconciled_sha256[:16]}… "
        f"priority_sha={priority_sha256[:16]}… "
        f"skill_hash={sh[:16]}…",
        flush=True,
    )

    message, text = _call_once(
        cluster=cluster, reconciled=reconciled, priority=priority, model=args.model
    )
    usage = message.usage
    input_tokens = int(usage.input_tokens)
    output_tokens = int(usage.output_tokens)

    # Parse + cross-validate.
    principle: DesignPrinciple | None = None
    decision: DesignDecision | None = None
    reason: str | None = None
    try:
        payload = parse_decide_response(
            text, cluster=cluster, reconciled=reconciled
        )
        principle = _build_principle(payload, cluster.cluster_id)
        decision = _build_decision(payload, cluster.cluster_id)
    except DecideParseError as e:
        reason = str(e)
        payload = {"fallback": True, "reason": reason, "raw_response": text}

    # Write outputs.
    cluster_stem = cluster.cluster_id.replace("_", "")
    principles_path = args.out_dir / f"l7_design_principles_{cluster_stem}{suffix}.jsonl"
    decisions_path = args.out_dir / f"l7_design_decisions_{cluster_stem}{suffix}.jsonl"
    native_path = args.out_dir / f"l7_design_decisions_{cluster_stem}{suffix}.native.jsonl"
    prov_path = args.out_dir / f"l7_design_principles_{cluster_stem}{suffix}.provenance.json"

    args.out_dir.mkdir(parents=True, exist_ok=True)

    principles_path.write_text(
        (
            json.dumps(principle.model_dump(mode="json"), ensure_ascii=False) + "\n"
            if principle is not None
            else ""
        ),
        encoding="utf-8",
    )
    decisions_path.write_text(
        (
            json.dumps(decision.model_dump(mode="json"), ensure_ascii=False) + "\n"
            if decision is not None
            else ""
        ),
        encoding="utf-8",
    )
    native_row = {
        "cluster_id": cluster.cluster_id,
        "status": "decided" if reason is None else "fallback",
        "reason": reason,
        "payload": payload,
    }
    native_path.write_text(
        json.dumps(native_row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    prov_path.write_text(
        json.dumps(
            _build_provenance(
                cluster_id=cluster.cluster_id,
                model=args.model,
                principle=principle,
                decision=decision,
                reason=reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                sh=sh,
                reconciled_sha256=reconciled_sha256,
                priority_sha256=priority_sha256,
            ),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    if reason is None:
        print(
            f"done: status=decided\n"
            f"  principle: {principle.name!r}\n"
            f"  decision: {decision.description[:80]}…\n"
            f"  resolves: {decision.resolves_heuristics}\n"
            f"  input_tokens={input_tokens} output_tokens={output_tokens}\n"
            f"  principles: {principles_path.relative_to(_REPO_ROOT)}\n"
            f"  decisions:  {decisions_path.relative_to(_REPO_ROOT)}\n"
            f"  native:     {native_path.relative_to(_REPO_ROOT)}\n"
            f"  provenance: {prov_path.relative_to(_REPO_ROOT)}",
            flush=True,
        )
    else:
        print(
            f"done: status=fallback\n"
            f"  reason: {reason[:200]}\n"
            f"  input_tokens={input_tokens} output_tokens={output_tokens}",
            flush=True,
        )

    return 0 if reason is None else 1


if __name__ == "__main__":
    sys.exit(main())
