"""One-shot smoke for the L5 reconcile on one cluster.

Companion to the production :mod:`auditable_design.layers.l5_reconcile`
module (SOT-reconcile skill — arbiter over the six L4 audits). Takes
one cluster bundle (a JSONL concatenating the six L4 verdicts for the
cluster), calls the Anthropic SDK directly, parses the response
through the same parser the module uses, and writes verdicts /
native / provenance next to the module's own outputs. Zero cache
interaction.

Text-only. L5 reconciles verdicts (JSON + text), not UI surfaces —
screenshots do not add signal for principle-level tension detection.
The matched-model eval grid is therefore 3 models × 1 modality = 3
cells per cluster.

Output
------
Mirrors the module's native output contract, with a per-(cluster,
model) suffix so all runs in a matched eval coexist:

``…_{clusterNN}_<modelshort>.{jsonl,native.jsonl,provenance.json}``

Cluster stem is derived from the loaded cluster's ``cluster_id``
(e.g. ``cluster_02`` → ``cluster02``). Model-short mapping matches
the L4 smokes: claude-opus-4-6 → opus46, claude-sonnet-4-6 →
sonnet46, claude-opus-4-7 → opus47.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import anthropic

# Make `src/` importable without requiring `uv run` wrapping.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.claude_client import _omits_sampling_params  # noqa: E402
from auditable_design.layers.l4_audit import _fallback_native  # noqa: E402
from auditable_design.layers.l5_reconcile import (  # noqa: E402
    MAX_TOKENS,
    SKILL_ID,
    SKILL_TO_FRAME,
    SYSTEM_PROMPT,
    TEMPERATURE,
    VALID_L4_SKILLS,
    VALID_NODE_TYPES,
    VALID_RELATION_TYPES,
    ReconcileParseError,
    _build_reconciled_verdict,
    build_user_message,
    load_verdicts_bundle,
    parse_reconcile_response,
    skill_hash,
)
from auditable_design.schemas import InsightCluster  # noqa: E402

# Same unconscious-decision-mode set as in the module — factored
# nowhere else to keep this smoke provenance self-contained.
_UNCONSCIOUS_DECISION_MODES = frozenset({"default", "mimicry", "fiat"})

DEFAULT_VERDICTS_BUNDLE = (
    _REPO_ROOT
    / "data/derived/l5_reconcile/cluster_02_opus46_text_bundle.jsonl"
)
DEFAULT_CLUSTERS = (
    # L4 shared cluster_02 fixture — byte-identical across the six
    # L4 skills. Any of the six works; pick one deterministically.
    _REPO_ROOT
    / "data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl"
)
DEFAULT_OUT_DIR = _REPO_ROOT / "data/derived/l5_reconcile"
DEFAULT_MODEL = "claude-opus-4-7"


def _load_cluster(path: Path, cluster_id: str) -> InsightCluster:
    """Load the named cluster from a JSONL of :class:`InsightCluster`
    rows. Raises if the cluster is not present."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("cluster_id") == cluster_id:
            return InsightCluster.model_validate(row)
    raise RuntimeError(
        f"cluster_id={cluster_id!r} not found in {path}"
    )


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


def _run(
    *,
    cluster: InsightCluster,
    bundle,
    model: str,
) -> tuple[anthropic.types.Message, str]:
    """One Claude call. Returns (raw Message, response text).

    Text-only — the reconcile skill consumes structured verdicts, not
    UI surfaces; screenshot attachment would add tokens without
    signal.
    """
    client = anthropic.Anthropic()
    user_text = build_user_message(cluster, bundle)

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
    payload: dict | None,
    parse_error: str | None,
    input_tokens: int,
    output_tokens: int,
    sh: str,
    bundle_sha256: str,
) -> dict:
    """Mirror of ``l5_reconcile.build_provenance`` for one-cluster smoke.

    Shape parallels the full-module provenance — node_type histogram
    (5 keys), relation_type histogram (4 keys), tension_axis
    histogram, corroboration_count histogram, ranked totals, plus the
    single-cluster token / bundle metadata.
    """
    audited = 1 if payload is not None else 0
    fallback = 1 if payload is None else 0

    node_type_hist: dict[str, int] = {t: 0 for t in VALID_NODE_TYPES}
    relation_type_hist: dict[str, int] = {r: 0 for r in VALID_RELATION_TYPES}
    tension_axis_hist: dict[str, int] = {}
    corroboration_count_hist: dict[int, int] = {k: 0 for k in range(1, 7)}
    total_ranked = 0
    total_tensions = 0
    total_gaps = 0
    top_rank_score = 0

    if payload is not None:
        for node in payload.get("graph", {}).get("nodes", []):
            nt = node["type"]
            node_type_hist[nt] = node_type_hist.get(nt, 0) + 1
        for edge in payload.get("graph", {}).get("edges", []):
            et = edge["type"]
            relation_type_hist[et] = relation_type_hist.get(et, 0) + 1
        ranked = payload.get("ranked_violations", [])
        tensions = payload.get("tensions", [])
        gaps = payload.get("gaps", [])
        total_ranked = len(ranked)
        total_tensions = len(tensions)
        total_gaps = len(gaps)
        for entry in ranked:
            corr = int(entry["corroboration_count"])
            corroboration_count_hist[corr] = corroboration_count_hist.get(corr, 0) + 1
        for t in tensions:
            axis = t["axis"]
            tension_axis_hist[axis] = tension_axis_hist.get(axis, 0) + 1
        if ranked:
            top_rank_score = int(ranked[0]["rank_score"])

    return {
        "skill_id": SKILL_ID,
        "model": model,
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
        "mode": "text_direct_sdk",
        "modality": "text",
        "cluster_count": 1,
        "audited_count": audited,
        "fallback_count": fallback,
        "transport_failure_count": 0,
        "total_ranked_violations": total_ranked,
        "total_tensions": total_tensions,
        "total_gaps": total_gaps,
        "top_rank_score": top_rank_score,
        "node_type_histogram": node_type_hist,
        "relation_type_histogram": relation_type_hist,
        "tension_axis_histogram": tension_axis_hist,
        "corroboration_count_histogram": {
            str(k): v for k, v in sorted(corroboration_count_hist.items())
        },
        "fallback_reasons": (
            [{"cluster_id": cluster_id, "reason": parse_error}]
            if parse_error is not None
            else []
        ),
        "transport_failures": [],
        "skill_hash": sh,
        "bundle_sha256": bundle_sha256,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Smoke for L5 sot-reconcile on one cluster's L4 verdict "
            "bundle. Text-only; writes verdicts / native / provenance "
            "with a per-(cluster, model) suffix so a matched-model "
            "eval can run from a single bash loop."
        )
    )
    parser.add_argument("--verdicts", type=Path, default=DEFAULT_VERDICTS_BUNDLE)
    parser.add_argument(
        "--clusters",
        type=Path,
        default=DEFAULT_CLUSTERS,
        help=(
            "Path to a JSONL of InsightCluster rows; the row matching "
            "the bundle's cluster_id is the reconcile context."
        ),
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--suffix",
        default=None,
        help=(
            "Override the auto-generated filename suffix. Default "
            "derives from model only (no modality — L5 is text-only): "
            "'_opus47' etc."
        ),
    )
    args = parser.parse_args(argv)

    bundles = load_verdicts_bundle(args.verdicts)
    if len(bundles) != 1:
        raise RuntimeError(
            f"expected exactly one cluster in {args.verdicts}, "
            f"got {len(bundles)}: {sorted(bundles)}"
        )
    cluster_id, bundle = next(iter(bundles.items()))
    cluster = _load_cluster(args.clusters, cluster_id)

    sh = skill_hash()
    bundle_sha256 = _sha256(args.verdicts)

    if args.suffix is None:
        short = _short_model_name(args.model)
        suffix = f"_{short}"
    else:
        suffix = args.suffix

    present_skills = sorted(bundle.verdicts_by_skill)
    missing = sorted(VALID_L4_SKILLS - set(present_skills))
    print(
        f"smoke: cluster={cluster_id} model={args.model} "
        f"bundle_skills={len(present_skills)}/6 "
        f"missing={missing or '—'} "
        f"bundle_sha256={bundle_sha256[:16]}… "
        f"skill_hash={sh[:16]}…",
        flush=True,
    )

    message, text = _run(cluster=cluster, bundle=bundle, model=args.model)

    usage = message.usage
    input_tokens = int(usage.input_tokens)
    output_tokens = int(usage.output_tokens)

    cluster_stem = cluster.cluster_id.replace("_", "")
    native_stem = f"l5_reconciled_{cluster_stem}{suffix}"
    verdicts_path = args.out_dir / f"{native_stem}.jsonl"
    native_path = args.out_dir / f"{native_stem}.native.jsonl"
    provenance_path = args.out_dir / f"{native_stem}.provenance.json"

    parse_error: str | None = None
    payload: dict | None = None
    try:
        payload = parse_reconcile_response(
            text,
            bundle=bundle,
            n_quotes=len(cluster.representative_quotes),
        )
    except ReconcileParseError as e:
        parse_error = str(e)

    if payload is not None:
        verdict = _build_reconciled_verdict(payload, cluster.cluster_id)
        status = "audited"
        native_row_payload: dict | object = payload
    else:
        from auditable_design.schemas import ReconciledVerdict

        verdict = ReconciledVerdict(
            cluster_id=cluster.cluster_id,
            ranked_violations=[],
            tensions=[],
        )
        status = "fallback"
        native_row_payload = _fallback_native(text, parse_error or "unknown")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    verdicts_path.write_text(
        json.dumps(verdict.model_dump(mode="json"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    native_row = {
        "cluster_id": cluster.cluster_id,
        "status": status,
        "payload": native_row_payload,
    }
    native_path.write_text(
        json.dumps(native_row, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    provenance_path.write_text(
        json.dumps(
            _build_provenance(
                cluster_id=cluster.cluster_id,
                model=args.model,
                payload=payload,
                parse_error=parse_error,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                sh=sh,
                bundle_sha256=bundle_sha256,
            ),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    if payload is not None:
        n_ranked = len(payload["ranked_violations"])
        n_tensions = len(payload["tensions"])
        n_gaps = len(payload["gaps"])
        n_nodes = len(payload["graph"]["nodes"])
        summary_line = (
            f"done: status={status} ranked={n_ranked} "
            f"tensions={n_tensions} gaps={n_gaps} nodes={n_nodes} "
            f"input_tokens={input_tokens} output_tokens={output_tokens}"
        )
    else:
        summary_line = (
            f"done: status={status} (fallback) "
            f"input_tokens={input_tokens} output_tokens={output_tokens}"
        )
    print(
        f"{summary_line}\n"
        f"  verdict:    {verdicts_path.relative_to(_REPO_ROOT)}\n"
        f"  native:     {native_path.relative_to(_REPO_ROOT)}\n"
        f"  provenance: {provenance_path.relative_to(_REPO_ROOT)}",
        flush=True,
    )
    return 0 if status == "audited" else 1


if __name__ == "__main__":
    sys.exit(main())
