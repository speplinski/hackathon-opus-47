"""Build per-model L5 input bundles for the shared-L2/L3 matched grid.

For each of the three branches (opus47, opus46, sonnet46), emit:

* ``l5_bundle_<branch>.jsonl`` — concatenation of the 36 L4 verdicts
  (6 clusters × 6 lenses) for that branch, in the ``AuditVerdict``
  shape the L5 layer expects.
* ``l3b_filtered_<branch>.jsonl`` — the six target cluster rows from
  the branch's L3b labelled clusters, filtered down so the L5 batch
  only attempts to reconcile clusters we actually have L4 evidence
  for (the full L3b file has 14 clusters; 8 have no screenshots and
  weren't audited at L4).

Outputs under ``data/derived/l5_reconcile/shared_l2opus47/``.

Fallback L4 verdicts (relevant_heuristics: []) are included in the
bundle as-is — L5's reconciler tolerates missing corroboration from
individual skills (see its docstring § "A cluster whose bundle is
missing one or more of the six canonical skill_ids will still
reconcile, but the SKILL.md contract expects the full set").

Usage:
    uv run python scripts/build_l5_bundles.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CLUSTERS = ["cluster_00", "cluster_01", "cluster_06", "cluster_11", "cluster_12", "cluster_13"]
LENSES = [
    "accessibility",
    "business_alignment",
    "decision_psychology",
    "interaction_design",
    "usability_fundamentals",
    "ux_architecture",
]
BRANCHES = ["opus47", "opus46", "sonnet46"]

L4_BASE = REPO_ROOT / "data/derived/l4_audit"
L3B_BASE = REPO_ROOT / "data/derived/l3b_labeled_clusters/shared_l2opus47"
OUT_DIR = REPO_ROOT / "data/derived/l5_reconcile/shared_l2opus47"


def _verdict_path(lens: str, cluster_id: str, branch: str) -> Path:
    cstem = cluster_id.replace("_", "")
    return (
        L4_BASE
        / f"audit_{lens}"
        / "shared_l2opus47"
        / f"l4_verdicts_audit_{lens}_{cstem}_{branch}_multimodal.jsonl"
    )


def _build_bundle(branch: str) -> tuple[int, int]:
    """Write the bundle JSONL for one branch. Return (rows_written, rows_missing)."""
    out_path = OUT_DIR / f"l5_bundle_{branch}.jsonl"
    written = 0
    missing = 0
    with out_path.open("w", encoding="utf-8") as out:
        for lens in LENSES:
            for cid in CLUSTERS:
                verdict_path = _verdict_path(lens, cid, branch)
                if not verdict_path.exists():
                    missing += 1
                    print(f"  ! missing: {verdict_path.relative_to(REPO_ROOT)}", file=sys.stderr)
                    continue
                for line in verdict_path.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        out.write(line + "\n")
                        written += 1
    return written, missing


def _filter_clusters(branch: str) -> int:
    """Write the filtered L3b clusters JSONL for one branch. Return rows_written."""
    in_path = L3B_BASE / f"l3b_labeled_clusters_{branch}.jsonl"
    out_path = OUT_DIR / f"l3b_filtered_{branch}.jsonl"
    keep = set(CLUSTERS)
    written = 0
    with out_path.open("w", encoding="utf-8") as out:
        for line in in_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("cluster_id") in keep:
                out.write(line + "\n")
                written += 1
    return written


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for branch in BRANCHES:
        rows, miss = _build_bundle(branch)
        clusters_written = _filter_clusters(branch)
        print(
            f"[{branch}] bundle={rows} rows ({miss} missing), "
            f"filtered clusters={clusters_written}"
        )
    print(f"[build-l5-bundles] outputs under {OUT_DIR.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
