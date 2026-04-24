"""One-shot smoke for the L6 priority-weight on one reconciled verdict.

Companion to the production :mod:`auditable_design.layers.l6_weight`
module (priority-weight skill — 5-dim scoring + weighted total).
Takes one cluster's ReconciledVerdict (from L5 output) and cluster
context, calls Claude twice (double-pass discipline), optionally a
third time if dimensions drift by more than ``MAX_DIMENSION_DELTA``,
and writes priority / native-passes / provenance. Zero cache
interaction.

Text-only. L6 consumes structured reconciled evidence (ranked_violations
+ tensions + gaps); screenshot attachment would add tokens without
signal. The matched-model eval grid is therefore 3 models × 1 modality
= 3 cells per cluster.

Output
------
Mirrors the module's native contract, with a per-(cluster, model)
suffix:

``l6_priority_{clusterNN}_<modelshort>.{jsonl,native.jsonl,provenance.json}``

Cluster stem derived from the loaded cluster's ``cluster_id``.
Model-short mapping: opus46 / sonnet46 / opus47.
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
from auditable_design.layers.l6_weight import (  # noqa: E402
    DEFAULT_META_WEIGHTS,
    DIMENSION_KEYS,
    MAX_DIMENSION_DELTA,
    MAX_TOKENS,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    PriorityParseError,
    _aggregate_passes,
    _needs_third_pass,
    build_user_message,
    load_reconciled_verdicts,
    parse_priority_response,
    skill_hash,
    weighted_total,
)
from auditable_design.schemas import (  # noqa: E402
    InsightCluster,
    PriorityScore,
)

DEFAULT_RECONCILED = (
    _REPO_ROOT
    / "data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl"
)
DEFAULT_CLUSTERS = (
    _REPO_ROOT
    / "data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl"
)
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/l6_weight"
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


def _one_pass(
    *,
    cluster: InsightCluster,
    reconciled,  # ReconciledVerdict
    model: str,
) -> tuple[anthropic.types.Message, str]:
    """One Claude call. Returns (Message, response text)."""
    client = anthropic.Anthropic()
    user_text = build_user_message(cluster, reconciled)

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
    priority: PriorityScore | None,
    parsed_passes: list[dict],
    raw_passes: list[dict],
    reason: str | None,
    input_tokens_total: int,
    output_tokens_total: int,
    sh: str,
    reconciled_sha256: str,
) -> dict:
    """One-cluster smoke provenance, parallel to module build_provenance."""
    scored = 1 if priority is not None and reason is None else 0
    fallback = 1 - scored

    dim_values: dict[str, int] = {}
    if priority is not None:
        dim_values = dict(priority.dimensions)

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "mode": "text_direct_sdk",
        "modality": "text",
        "cluster_count": 1,
        "scored_count": scored,
        "fallback_count": fallback,
        "transport_failure_count": 0,
        "validation_passes": priority.validation_passes if priority else 0,
        "validation_delta": priority.validation_delta if priority else 0.0,
        "third_pass_triggered": 1 if (priority and priority.validation_passes == 3) else 0,
        "dimension_scores": dim_values,
        "meta_weights": dict(DEFAULT_META_WEIGHTS),
        "weighted_total": priority.weighted_total if priority else 0.0,
        "pass_count_raw": len(raw_passes),
        "pass_count_parsed": len(parsed_passes),
        "fallback_reasons": (
            [{"cluster_id": cluster_id, "reason": reason}] if reason is not None else []
        ),
        "transport_failures": [],
        "skill_hash": sh,
        "reconciled_sha256": reconciled_sha256,
        "input_tokens_total": input_tokens_total,
        "output_tokens_total": output_tokens_total,
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke for L6 priority-weight on one cluster's reconciled "
            "verdict. Text-only. Double-pass with optional third pass "
            "if dimensions drift."
        )
    )
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
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
    cluster = _load_cluster(args.clusters, cluster_id)

    sh = skill_hash()
    reconciled_sha256 = _sha256(args.reconciled)

    if args.suffix is None:
        short = _short_model_name(args.model)
        suffix = f"_{short}"
    else:
        suffix = args.suffix

    print(
        f"smoke: cluster={cluster_id} model={args.model} "
        f"reconciled_sha={reconciled_sha256[:16]}… "
        f"skill_hash={sh[:16]}…",
        flush=True,
    )

    # Pass 1
    raw_passes: list[dict] = []
    parsed_passes: list[dict] = []
    input_tokens_total = 0
    output_tokens_total = 0

    try:
        m1, t1 = _one_pass(cluster=cluster, reconciled=reconciled, model=args.model)
        input_tokens_total += int(m1.usage.input_tokens)
        output_tokens_total += int(m1.usage.output_tokens)
        try:
            p1 = parse_priority_response(t1)
            parsed_passes.append(p1)
            raw_passes.append(
                {"pass": 1, "status": "parsed", "payload": p1, "raw": t1}
            )
            print(
                f"  pass 1: parsed (sev={p1['dimensions']['severity']}, "
                f"reach={p1['dimensions']['reach']})",
                flush=True,
            )
        except PriorityParseError as e:
            raw_passes.append(
                {"pass": 1, "status": "fallback", "reason": str(e), "raw": t1}
            )
            print(f"  pass 1: FALLBACK — {e}", flush=True)
    except anthropic.APIError as e:
        print(f"  pass 1: TRANSPORT FAILURE — {e}", flush=True)
        raise

    # Pass 2
    try:
        m2, t2 = _one_pass(cluster=cluster, reconciled=reconciled, model=args.model)
        input_tokens_total += int(m2.usage.input_tokens)
        output_tokens_total += int(m2.usage.output_tokens)
        try:
            p2 = parse_priority_response(t2)
            parsed_passes.append(p2)
            raw_passes.append(
                {"pass": 2, "status": "parsed", "payload": p2, "raw": t2}
            )
            print(
                f"  pass 2: parsed (sev={p2['dimensions']['severity']}, "
                f"reach={p2['dimensions']['reach']})",
                flush=True,
            )
        except PriorityParseError as e:
            raw_passes.append(
                {"pass": 2, "status": "fallback", "reason": str(e), "raw": t2}
            )
            print(f"  pass 2: FALLBACK — {e}", flush=True)
    except anthropic.APIError as e:
        print(f"  pass 2: TRANSPORT FAILURE — {e}", flush=True)
        raise

    # Optional pass 3
    if len(parsed_passes) == 2:
        if _needs_third_pass(
            parsed_passes[0]["dimensions"], parsed_passes[1]["dimensions"]
        ):
            print(
                f"  drift > {MAX_DIMENSION_DELTA} detected — running pass 3",
                flush=True,
            )
            try:
                m3, t3 = _one_pass(
                    cluster=cluster, reconciled=reconciled, model=args.model
                )
                input_tokens_total += int(m3.usage.input_tokens)
                output_tokens_total += int(m3.usage.output_tokens)
                try:
                    p3 = parse_priority_response(t3)
                    parsed_passes.append(p3)
                    raw_passes.append(
                        {"pass": 3, "status": "parsed", "payload": p3, "raw": t3}
                    )
                    print(
                        f"  pass 3: parsed (sev={p3['dimensions']['severity']})",
                        flush=True,
                    )
                except PriorityParseError as e:
                    raw_passes.append(
                        {
                            "pass": 3,
                            "status": "fallback",
                            "reason": str(e),
                            "raw": t3,
                        }
                    )
                    print(f"  pass 3: FALLBACK — {e}", flush=True)
            except anthropic.APIError as e:
                print(f"  pass 3: TRANSPORT FAILURE — {e}", flush=True)
                raise

    # Aggregate
    priority: PriorityScore | None = None
    reason: str | None = None

    if not parsed_passes:
        reason = "both passes failed to parse"
    elif len(parsed_passes) == 1:
        reason = "only one of two passes parsed — no validation comparison"
        dims = parsed_passes[0]["dimensions"]
        priority = PriorityScore(
            cluster_id=cluster.cluster_id,
            dimensions=dims,
            meta_weights=dict(DEFAULT_META_WEIGHTS),
            weighted_total=weighted_total(dims, DEFAULT_META_WEIGHTS),
            validation_passes=2,
            validation_delta=0.0,
        )
    else:
        dim_lists = [pp["dimensions"] for pp in parsed_passes]
        aggregated, max_delta = _aggregate_passes(dim_lists)
        total = weighted_total(aggregated, DEFAULT_META_WEIGHTS)
        priority = PriorityScore(
            cluster_id=cluster.cluster_id,
            dimensions=aggregated,
            meta_weights=dict(DEFAULT_META_WEIGHTS),
            weighted_total=total,
            validation_passes=len(parsed_passes),
            validation_delta=max_delta,
        )

    if priority is None:
        # True fallback — cannot even construct a PriorityScore.
        fallback_dims = {k: 0 for k in DIMENSION_KEYS}
        priority = PriorityScore(
            cluster_id=cluster.cluster_id,
            dimensions=fallback_dims,
            meta_weights=dict(DEFAULT_META_WEIGHTS),
            weighted_total=0.0,
            validation_passes=2,
            validation_delta=0.0,
        )

    # Write outputs
    cluster_stem = cluster.cluster_id.replace("_", "")
    native_stem = f"l6_priority_{cluster_stem}{suffix}"
    out_path = args.out_dir / f"{native_stem}.jsonl"
    native_path = args.out_dir / f"{native_stem}.native.jsonl"
    prov_path = args.out_dir / f"{native_stem}.provenance.json"

    args.out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(priority.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    native_row = {
        "cluster_id": cluster.cluster_id,
        "status": "scored" if reason is None else "fallback",
        "reason": reason,
        "passes": raw_passes,
    }
    native_path.write_text(
        json.dumps(native_row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    prov_path.write_text(
        json.dumps(
            _build_provenance(
                cluster_id=cluster.cluster_id,
                model=args.model,
                priority=priority,
                parsed_passes=parsed_passes,
                raw_passes=raw_passes,
                reason=reason,
                input_tokens_total=input_tokens_total,
                output_tokens_total=output_tokens_total,
                sh=sh,
                reconciled_sha256=reconciled_sha256,
            ),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    status = "scored" if reason is None else "fallback"
    print(
        f"done: status={status} passes={priority.validation_passes} "
        f"delta={priority.validation_delta:.1f} "
        f"weighted_total={priority.weighted_total:.2f} "
        f"input_tokens={input_tokens_total} output_tokens={output_tokens_total}\n"
        f"  priority:   {out_path.relative_to(_REPO_ROOT)}\n"
        f"  native:     {native_path.relative_to(_REPO_ROOT)}\n"
        f"  provenance: {prov_path.relative_to(_REPO_ROOT)}",
        flush=True,
    )
    return 0 if status == "scored" else 1


if __name__ == "__main__":
    sys.exit(main())
