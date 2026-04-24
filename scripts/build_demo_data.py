"""Build demo/public/data.json — single bundle the static demo loads.

Pulls from the pipeline's existing per-layer outputs (L3b cluster,
L5 reconciled verdict, L6 priority, L7 decision, L8 iterations,
verify_on_product grounded evidence, design brief provenance) for
one (cluster, model) cell and writes a self-contained JSON the
scroll-narrative HTML demo can consume without touching the rest
of the repo.

Default cell: cluster_02 × Opus 4.7 × Tchebycheff loop. Zero
Claude calls.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from auditable_design.layers.l6_weight import (  # noqa: E402
    load_reconciled_verdicts,
)
from auditable_design.layers.l7_decide import (  # noqa: E402
    load_priority_scores,
)
from auditable_design.layers.l8_optimize import (  # noqa: E402
    load_decisions,
)
from auditable_design.schemas import (  # noqa: E402
    OptimizationIteration,
)


DEFAULT_CLUSTER_ID = "cluster_11"
DEFAULT_MODEL_SHORT = "opus47"
DEFAULT_LOOP_VERIFIER = "tchebycheff"
DEFAULT_OUT_DIR = _REPO_ROOT / "demo" / "public"
# Shared-L2-opus47 layout (the re-run after the clustering refactor).
# All per-branch files live under */shared_l2opus47/ and pack multiple
# clusters per file — load steps filter to DEFAULT_CLUSTER_ID.
_SHARED_SUBDIR = "shared_l2opus47"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _load_cluster(path: Path, cluster_id: str) -> dict[str, Any]:
    for row in _load_jsonl(path):
        if row.get("cluster_id") == cluster_id:
            return row
    raise RuntimeError(f"cluster_id={cluster_id!r} not found in {path}")


def _extract_snapshot_from_artifact(
    md_path: Path, header: str
) -> str | None:
    if not md_path.exists():
        return None
    body = md_path.read_text(encoding="utf-8")
    marker = f"## {header}\n"
    idx = body.find(marker)
    if idx < 0:
        return None
    after = body[idx + len(marker):]
    end = after.find("\n## ")
    return (after if end < 0 else after[:end]).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build demo data bundle")
    parser.add_argument("--cluster-id", default=DEFAULT_CLUSTER_ID)
    parser.add_argument("--model-short", default=DEFAULT_MODEL_SHORT)
    parser.add_argument("--loop-verifier", default=DEFAULT_LOOP_VERIFIER)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args(argv)

    cluster_id = args.cluster_id
    model_short = args.model_short
    stem = cluster_id.replace("_", "")

    # Resolve paths under the shared-L2-opus47 layout.
    clusters_path = (
        _REPO_ROOT / "data/derived/l5_reconcile" / _SHARED_SUBDIR
        / f"l3b_filtered_{model_short}.jsonl"
    )
    reconciled_path = (
        _REPO_ROOT / "data/derived/l5_reconcile" / _SHARED_SUBDIR
        / f"l5_reconciled_{model_short}.jsonl"
    )
    priority_path = (
        _REPO_ROOT / "data/derived/l6_weight" / _SHARED_SUBDIR
        / f"l6_priority_{model_short}.jsonl"
    )
    decisions_path = (
        _REPO_ROOT / "data/derived/l7_decide" / _SHARED_SUBDIR
        / f"l7_design_decisions_{model_short}.jsonl"
    )
    iters_thin_path = (
        _REPO_ROOT / "data/derived/l8_optimize" / _SHARED_SUBDIR
        / f"l8_optimization_iterations_{model_short}.jsonl"
    )
    iters_loop_path = (
        _REPO_ROOT / "data/derived/l8_loop" / _SHARED_SUBDIR
        / f"l8_loop_iterations_{stem}_{model_short}_{args.loop_verifier}.jsonl"
    )
    verify_path = (
        _REPO_ROOT / "data/derived/verify_on_product" / _SHARED_SUBDIR
        / f"verify_on_product_{stem}_{model_short}.json"
    )

    # Load.
    cluster = _load_cluster(clusters_path, cluster_id)
    reconciled = load_reconciled_verdicts(reconciled_path)[cluster_id]
    priority = load_priority_scores(priority_path)[cluster_id]
    decision = load_decisions(decisions_path)[cluster_id]

    iter_rows = _load_jsonl(iters_thin_path) + _load_jsonl(iters_loop_path)
    # shared_l2opus47 thin-spine files pack iterations for all six
    # target clusters; filter to this cluster via the iteration_id
    # prefix ('iteration__<cluster_id>__<NN>').
    _prefix = f"iteration__{cluster_id}__"
    iter_rows = [r for r in iter_rows if str(r.get("iteration_id", "")).startswith(_prefix)]
    iterations = sorted(
        [OptimizationIteration.model_validate(r) for r in iter_rows],
        key=lambda it: it.iteration_index,
    )

    verify_payload = (
        json.loads(verify_path.read_text(encoding="utf-8"))
        if verify_path.exists() else None
    )

    # Build iteration view with recovered snapshots.
    iter_view: list[dict[str, Any]] = []
    for it in iterations:
        scores = it.scores.get("reconciled", {})
        if it.iteration_index == 0:
            snapshot = decision.before_snapshot
        elif it.iteration_index == 1:
            snapshot = decision.after_snapshot
        else:
            snapshot = (
                _extract_snapshot_from_artifact(
                    Path(it.design_artifact_ref), header="new_snapshot"
                ) or ""
            )
        iter_view.append({
            "iteration_id": it.iteration_id,
            "iteration_index": it.iteration_index,
            "parent_iteration_id": it.parent_iteration_id,
            "accepted": it.accepted,
            "scores": dict(scores),
            "severity_sum": sum(scores.values()),
            "delta_per_heuristic": dict(it.delta_per_heuristic),
            "reasoning": (it.reasoning or "")[:500],
            "regression_reason": it.regression_reason,
            "snapshot": snapshot,
            "design_artifact_ref": it.design_artifact_ref,
        })

    # Baseline severity from L5 for easy chart.
    baseline_severities = {
        v.heuristic: int(v.severity) for v in reconciled.ranked_violations
    }

    # Build bundle.
    bundle = {
        "meta": {
            "cluster_id": cluster_id,
            "model_short": model_short,
            "loop_verifier": args.loop_verifier,
            "baseline_sum": sum(baseline_severities.values()),
            "final_sum": iter_view[-1]["severity_sum"] if iter_view else 0,
        },
        "cluster": {
            "cluster_id": cluster["cluster_id"],
            "label": cluster["label"],
            "member_review_ids": cluster["member_review_ids"],
            "representative_quotes": cluster["representative_quotes"],
            "ui_context": cluster.get("ui_context"),
        },
        "reconciled": {
            "ranked_violations": [
                {
                    "heuristic": v.heuristic,
                    "severity": int(v.severity),
                    "violation": v.violation,
                    "reasoning": v.reasoning,
                    "evidence_review_ids": list(v.evidence_review_ids),
                }
                for v in reconciled.ranked_violations
            ],
            "tensions": [
                {
                    "skill_a": t.skill_a,
                    "skill_b": t.skill_b,
                    "axis": t.axis,
                    "resolution": t.resolution,
                }
                for t in reconciled.tensions
            ],
        },
        "priority": {
            "dimensions": dict(priority.dimensions),
            "meta_weights": dict(priority.meta_weights),
            "weighted_total": priority.weighted_total,
        },
        "decision": {
            "decision_id": decision.decision_id,
            "description": decision.description,
            "before_snapshot": decision.before_snapshot,
            "after_snapshot": decision.after_snapshot,
            "resolves_heuristics": list(decision.resolves_heuristics),
        },
        "iterations": iter_view,
        "verify_on_product": verify_payload,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = args.out_dir / "data.json"
    bundle_path.write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # Copy screenshots into demo asset dir.
    assets_dir = args.out_dir / "screenshots"
    assets_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "energy_manage.png",
        "out_of_energy_home.png",
        "out_of_energy_mid_lesson.png",
    ):
        src = _REPO_ROOT / "data/raw/duolingo_screenshots" / name
        if src.exists():
            shutil.copy2(src, assets_dir / name)

    print(
        f"[demo-data] cluster={cluster_id} model={model_short} "
        f"heuristics={len(baseline_severities)} "
        f"iterations={len(iter_view)} "
        f"verify_included={verify_payload is not None}"
    )
    print(f"[demo-data] → {bundle_path}")
    print(f"[demo-data] screenshots → {assets_dir}/")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
