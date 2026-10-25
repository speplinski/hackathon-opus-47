"""Summarise how far the evidence chain reaches in backfilled study data.

Reads the output of ``scripts/run_backfill_evidence_chain_study.sh``
(``data/derived_evchain``) and prints a markdown report:

* representative quotes per grid cluster that match a single review
  ("exact") versus several reviews with identical text ("attested");
* L4 findings that cite user quotes, by lens and by model;
* L5 ranked violations that reach reviews, by model, and how many
  member reviews they reach;
* per-violation detail for Study B (cluster_11) and Study A (cluster_02).

No model calls; read-only.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
EV = REPO_ROOT / "data/derived_evchain"
M = EV / "data/derived"
GRID = ["cluster_00", "cluster_01", "cluster_06", "cluster_11", "cluster_12", "cluster_13"]
BRANCHES = ["opus47", "opus46", "sonnet46"]
LENS_NAMES = {
    "usability_fundamentals": "Norman",
    "accessibility": "WCAG",
    "decision_psychology": "Kahneman",
    "business_alignment": "Osterwalder",
    "interaction_design": "Cooper",
    "ux_architecture": "Garrett",
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _pct(a: int, b: int) -> str:
    return f"{a}/{b} ({100 * a / b:.0f}%)" if b else "0/0"


def main() -> int:
    argparse.ArgumentParser(description=__doc__.split("\n\n")[0]).parse_args()
    out: list[str] = ["# Evidence chain — backfilled study data", ""]

    # --- clusters ---------------------------------------------------------
    report = json.loads((EV / "reports/grid_clusters.json").read_text())
    ambiguous: dict[str, set[int]] = defaultdict(set)
    for f in report["files"]:
        if f["input"].endswith("shared_l2opus47/l3_clusters.jsonl"):
            for i in f["issues"]:
                if i["issue"] == "ambiguous":
                    ambiguous[i["cluster_id"]].add(i["quote_idx"])
    clusters = {
        c["cluster_id"]: c for c in _jsonl(M / "l3_clusters/shared_l2opus47/l3_clusters.jsonl")
    }
    out += [
        "## Representative quotes (grid clusters)",
        "",
        "| cluster | members | quotes | exact | attested (text in several reviews) | distinct reviews |",
        "|---|---|---|---|---|---|",
    ]
    for cid in GRID:
        c = clusters[cid]
        n = len(c["representative_quotes"])
        a = len(ambiguous[cid])
        out.append(
            f"| {cid} | {len(c['member_review_ids'])} | {n} | {n - a} | {a} | "
            f"{len(set(c['representative_review_ids']))} |"
        )
    out.append("")

    # --- L4 ---------------------------------------------------------------
    by_lens: dict[str, Counter[str]] = defaultdict(Counter)
    by_model: dict[str, Counter[str]] = defaultdict(Counter)
    attested = 0
    for path in sorted(M.glob("l4_audit/audit_*/shared_l2opus47/l4_verdicts_*.jsonl")):
        if path.name.endswith(".native.jsonl"):
            continue
        lens = path.parts[-3].removeprefix("audit_")
        branch = re.search(r"_(opus47|opus46|sonnet46)_", path.name).group(1)  # type: ignore[union-attr]
        for v in _jsonl(path):
            cid = v["cluster_id"]
            if not v["relevant_heuristics"]:
                by_model[branch]["empty_verdicts"] += 1
            for h in v["relevant_heuristics"]:
                ids = h["evidence_review_ids"]
                for counter in (by_lens[lens], by_model[branch]):
                    counter["findings"] += 1
                    counter["with_quotes"] += bool(ids)
                rep_ids = clusters[cid]["representative_review_ids"]
                if ids and any(rep_ids.index(r) in ambiguous[cid] for r in ids):
                    attested += 1
    total = sum(c["findings"] for c in by_lens.values())
    quoted = sum(c["with_quotes"] for c in by_lens.values())
    out += [
        "## L4 findings citing user quotes (grid, 108 verdicts)",
        "",
        f"All lenses: {_pct(quoted, total)}; of those, {attested} rest at "
        "least partly on an attested (not exact) quote.",
        "",
        "| lens | findings citing quotes |",
        "|---|---|",
    ]
    for lens, name in LENS_NAMES.items():
        c = by_lens[lens]
        out.append(f"| {name} | {_pct(c['with_quotes'], c['findings'])} |")
    out += ["", "| model | findings citing quotes | verdicts with no findings |", "|---|---|---|"]
    for b in BRANCHES:
        c = by_model[b]
        out.append(f"| {b} | {_pct(c['with_quotes'], c['findings'])} | {c['empty_verdicts']} |")
    out.append("")

    # --- L5 ---------------------------------------------------------------
    out += [
        "## L5 ranked violations reaching reviews (grid)",
        "",
        "| model | violations with review ids | mean reviews reached | mean cluster size |",
        "|---|---|---|---|",
    ]
    for b in BRANCHES:
        n = with_ids = reached = members = 0
        for v in _jsonl(M / f"l5_reconcile/shared_l2opus47/l5_reconciled_{b}.jsonl"):
            for h in v["ranked_violations"]:
                n += 1
                if h["evidence_review_ids"]:
                    with_ids += 1
                    reached += len(h["evidence_review_ids"])
                    members += len(clusters[v["cluster_id"]]["member_review_ids"])
        out.append(
            f"| {b} | {_pct(with_ids, n)} | {reached / max(1, with_ids):.1f} | "
            f"{members / max(1, with_ids):.1f} |"
        )
    out.append("")

    # --- per-violation detail -------------------------------------------
    def detail(title: str, cluster: dict[str, Any], paths: dict[str, Path]) -> None:
        quotes: dict[str, list[str]] = defaultdict(list)
        for rid, q in zip(
            cluster["representative_review_ids"], cluster["representative_quotes"], strict=True
        ):
            quotes[rid].append(q)
        out.extend([f"## {title}", "", f"Cluster members: {len(cluster['member_review_ids'])}.", ""])
        for b, path in paths.items():
            out.extend([f"**{b}**", "", "| sev | heuristic | reviews | quotes |", "|---|---|---|---|"])
            for v in _jsonl(path):
                if v["cluster_id"] != cluster["cluster_id"]:
                    continue
                for h in v["ranked_violations"]:
                    ids = h["evidence_review_ids"]
                    qs = "; ".join(f"“{q}”" for r in ids for q in quotes[r]) or "— (artifact only)"
                    out.append(
                        f"| {h['severity']} | `{h['heuristic']}` | "
                        f"{', '.join(r[:8] for r in ids) or '—'} | {qs} |"
                    )
            out.append("")

    detail(
        "Study B — cluster_11",
        clusters["cluster_11"],
        {b: M / f"l5_reconcile/shared_l2opus47/l5_reconciled_{b}.jsonl" for b in BRANCHES},
    )
    study_a = _jsonl(M / "l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl")[0]
    detail(
        "Study A — cluster_02",
        study_a,
        {b: M / f"l5_reconcile/l5_reconciled_cluster02_{b}.jsonl" for b in BRANCHES},
    )

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
