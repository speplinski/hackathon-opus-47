"""Backfill per-finding review ids into existing derived data.

Runs written before L3 recorded ``representative_review_ids`` carry
``evidence_review_ids == []`` on every L4 and L5 violation. The
information needed to fill them already exists on disk, so this script
re-derives it deterministically, with no model calls:

1. ``clusters`` — for each representative quote, find the member review
   whose L2 node has exactly that ``verbatim_quote`` (L3 copies node
   quotes verbatim, so an exact match is expected). Writes the clusters
   with ``representative_review_ids`` filled.
2. ``l4`` — for each L4 verdict, read the native sidecar's
   ``findings[i].evidence_quote_idxs`` and resolve them through the
   cluster (same code path L4 now uses at run time).
3. ``l5`` — for each reconciled verdict, re-walk the stored L5 graph to
   recover which L4 findings each ranked entry collapses, then take the
   union of their review ids from the backfilled L4 bundle.

Inputs are never modified. Every output goes under ``--out-root``,
mirroring the input path relative to the repo root (so a backfilled
tree can be diffed against the original). A JSON report on stdout lists
every quote that matched zero or several reviews and every verdict that
could not be aligned.

Typical order for the shared_l2opus47 study (repeat per branch)::

    uv run python scripts/backfill_evidence_chain.py clusters \\
        --graphs data/derived/l2_graphs/<l2 file>.jsonl \\
        --clusters data/derived/l3b_labeled_clusters/shared_l2opus47/l3b_labeled_clusters_opus47.jsonl \\
        --out-root data/derived_evchain
    uv run python scripts/backfill_evidence_chain.py l4 \\
        --clusters data/derived_evchain/data/derived/l3b_labeled_clusters/shared_l2opus47/l3b_labeled_clusters_opus47.jsonl \\
        --verdicts data/derived/l4_audit/audit_*/shared_l2opus47/l4_verdicts_*_opus47_multimodal.jsonl \\
        --out-root data/derived_evchain
    # rebuild the L5 bundle from the backfilled L4 verdicts, then:
    uv run python scripts/backfill_evidence_chain.py l5 \\
        --bundle <backfilled l5_bundle_opus47.jsonl> \\
        --verdicts data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_opus47.jsonl \\
        --out-root data/derived_evchain
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from auditable_design.layers import l5_reconcile as l5
from auditable_design.schemas import (
    AuditVerdict,
    ComplaintGraph,
    InsightCluster,
    ReconciledVerdict,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
        encoding="utf-8",
    )


def _mirror(path: Path, out_root: Path) -> Path:
    resolved = path.resolve()
    try:
        rel = resolved.relative_to(REPO_ROOT)
    except ValueError:
        rel = Path(resolved.name)
    return out_root / rel


def _native_path(verdict_path: Path) -> Path:
    return verdict_path.with_name(verdict_path.name.replace(".jsonl", ".native.jsonl"))


# ---------------------------------------------------------------------------
# Step 1 — clusters
# ---------------------------------------------------------------------------


def backfill_cluster(
    cluster: InsightCluster,
    graphs: dict[str, ComplaintGraph],
) -> tuple[InsightCluster, list[dict[str, Any]]]:
    """Return the cluster with ``representative_review_ids`` filled, plus
    a list of issues. If any quote cannot be matched the cluster is
    returned unchanged (the schema requires full alignment)."""
    issues: list[dict[str, Any]] = []
    ids: list[str] = []
    for i, quote in enumerate(cluster.representative_quotes):
        candidates = [
            rid
            for rid in cluster.member_review_ids
            if rid in graphs
            and any(n.verbatim_quote == quote for n in graphs[rid].nodes)
        ]
        if not candidates:
            issues.append(
                {"cluster_id": cluster.cluster_id, "quote_idx": i, "issue": "no_match"}
            )
            continue
        if len(candidates) > 1:
            # Identical quote text in several reviews: any of them is a
            # true source of the text. Take the first in member order and
            # record it so the choice is visible.
            issues.append(
                {
                    "cluster_id": cluster.cluster_id,
                    "quote_idx": i,
                    "issue": "ambiguous",
                    "candidates": candidates,
                    "chosen": candidates[0],
                }
            )
        ids.append(candidates[0])
    if len(ids) != len(cluster.representative_quotes):
        return cluster, issues
    data = cluster.model_dump(mode="json")
    data["representative_review_ids"] = ids
    return InsightCluster.model_validate(data), issues


def cmd_clusters(args: argparse.Namespace) -> dict[str, Any]:
    graphs = {
        g.review_id: g
        for g in (ComplaintGraph.model_validate(r) for r in _read_jsonl(args.graphs))
    }
    report: dict[str, Any] = {"files": []}
    for path in args.clusters:
        rows = _read_jsonl(path)
        out_rows = []
        issues: list[dict[str, Any]] = []
        filled = 0
        for row in rows:
            cluster = InsightCluster.model_validate(row)
            new, cluster_issues = backfill_cluster(cluster, graphs)
            issues += cluster_issues
            if new.representative_review_ids:
                filled += 1
            out_rows.append(new.model_dump(mode="json"))
        out = _mirror(path, args.out_root)
        _write_jsonl(out, out_rows)
        report["files"].append(
            {
                "input": str(path),
                "output": str(out),
                "clusters": len(rows),
                "filled": filled,
                "issues": issues,
            }
        )
    return report


# ---------------------------------------------------------------------------
# Step 2 — L4
# ---------------------------------------------------------------------------


def backfill_l4_verdict(
    verdict: AuditVerdict,
    native_payload: dict[str, Any],
    cluster: InsightCluster,
) -> tuple[AuditVerdict, str | None]:
    """Fill ``evidence_review_ids`` on each heuristic from the native
    finding at the same position. Returns (verdict, issue-or-None)."""
    findings = native_payload.get("findings")
    if not verdict.relevant_heuristics:
        return verdict, None
    if not isinstance(findings, list) or len(findings) != len(
        verdict.relevant_heuristics
    ):
        return verdict, "findings_count_mismatch"
    if not cluster.representative_review_ids:
        return verdict, "cluster_without_review_ids"
    data = verdict.model_dump(mode="json")
    for hv, finding in zip(data["relevant_heuristics"], findings, strict=True):
        if hv["heuristic"] != finding.get("heuristic"):
            return verdict, "heuristic_order_mismatch"
        hv["evidence_review_ids"] = cluster.evidence_review_ids_for(
            list(finding.get("evidence_quote_idxs", []))
        )
    return AuditVerdict.model_validate(data), None


def cmd_l4(args: argparse.Namespace) -> dict[str, Any]:
    clusters = {
        c.cluster_id: c
        for c in (InsightCluster.model_validate(r) for r in _read_jsonl(args.clusters))
    }
    report: dict[str, Any] = {"files": []}
    for path in args.verdicts:
        native_path = _native_path(path)
        natives = {r["verdict_id"]: r for r in _read_jsonl(native_path)}
        out_rows = []
        issues = []
        filled = 0
        for row in _read_jsonl(path):
            verdict = AuditVerdict.model_validate(row)
            cluster = clusters.get(verdict.cluster_id)
            native = natives.get(verdict.verdict_id)
            if cluster is None or native is None:
                issue = "cluster_missing" if cluster is None else "native_missing"
                issues.append({"verdict_id": verdict.verdict_id, "issue": issue})
                out_rows.append(row)
                continue
            new, issue = backfill_l4_verdict(verdict, native.get("payload", {}), cluster)
            if issue:
                issues.append({"verdict_id": verdict.verdict_id, "issue": issue})
            if any(h.evidence_review_ids for h in new.relevant_heuristics):
                filled += 1
            out_rows.append(new.model_dump(mode="json"))
        out = _mirror(path, args.out_root)
        _write_jsonl(out, out_rows)
        report["files"].append(
            {
                "input": str(path),
                "output": str(out),
                "verdicts": len(out_rows),
                "filled": filled,
                "issues": issues,
            }
        )
    return report


# ---------------------------------------------------------------------------
# Step 3 — L5
# ---------------------------------------------------------------------------


def rederive_ranked(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Re-walk a stored L5 graph into ranked entries with
    ``source_findings``. ``None`` if the payload has no graph (legacy
    flat-list output)."""
    graph = payload.get("graph")
    if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
        return None
    nodes = graph["nodes"]
    ranked, _, _ = l5._derive_flat_lists_from_graph(
        nodes,
        [n for n in nodes if n.get("type") == "tension"],
        [n for n in nodes if n.get("type") == "gap"],
    )
    return ranked


def backfill_l5_verdict(
    verdict: ReconciledVerdict,
    native_payload: dict[str, Any],
    bundle: l5._ClusterBundle | None,
) -> tuple[ReconciledVerdict, dict[str, Any], str | None]:
    if not verdict.ranked_violations:
        return verdict, native_payload, None
    if bundle is None:
        return verdict, native_payload, "bundle_missing"
    ranked = rederive_ranked(native_payload)
    if ranked is None:
        return verdict, native_payload, "no_graph_in_native_payload"
    stored = [(v.heuristic, v.severity) for v in verdict.ranked_violations]
    derived = [(e["heuristic"], e["severity"]) for e in ranked]
    if stored != derived:
        return verdict, native_payload, "ranked_order_mismatch"
    data = verdict.model_dump(mode="json")
    for hv, entry in zip(data["ranked_violations"], ranked, strict=True):
        hv["evidence_review_ids"] = l5.evidence_review_ids_for_entry(entry, bundle)
    new_native = dict(native_payload)
    new_native["ranked_violations"] = ranked
    return ReconciledVerdict.model_validate(data), new_native, None


def cmd_l5(args: argparse.Namespace) -> dict[str, Any]:
    bundles = l5.load_verdicts_bundle(args.bundle)
    report: dict[str, Any] = {"files": []}
    for path in args.verdicts:
        native_path = _native_path(path)
        natives = {r["cluster_id"]: r for r in _read_jsonl(native_path)}
        out_rows, out_natives, issues = [], [], []
        filled = 0
        for row in _read_jsonl(path):
            verdict = ReconciledVerdict.model_validate(row)
            native = natives.get(verdict.cluster_id)
            if native is None:
                issues.append({"cluster_id": verdict.cluster_id, "issue": "native_missing"})
                out_rows.append(row)
                continue
            new, new_payload, issue = backfill_l5_verdict(
                verdict, native.get("payload", {}), bundles.get(verdict.cluster_id)
            )
            if issue:
                issues.append({"cluster_id": verdict.cluster_id, "issue": issue})
            if any(v.evidence_review_ids for v in new.ranked_violations):
                filled += 1
            out_rows.append(new.model_dump(mode="json"))
            out_natives.append({**native, "payload": new_payload})
        out = _mirror(path, args.out_root)
        _write_jsonl(out, out_rows)
        _write_jsonl(_native_path(out), out_natives)
        report["files"].append(
            {
                "input": str(path),
                "output": str(out),
                "verdicts": len(out_rows),
                "filled": filled,
                "issues": issues,
            }
        )
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("clusters", help="fill representative_review_ids")
    pc.add_argument("--graphs", type=Path, required=True)
    pc.add_argument("--clusters", type=Path, nargs="+", required=True)

    p4 = sub.add_parser("l4", help="fill L4 evidence_review_ids")
    p4.add_argument("--clusters", type=Path, required=True)
    p4.add_argument("--verdicts", type=Path, nargs="+", required=True)

    p5 = sub.add_parser("l5", help="fill L5 evidence_review_ids")
    p5.add_argument("--bundle", type=Path, required=True)
    p5.add_argument("--verdicts", type=Path, nargs="+", required=True)

    for sp in (pc, p4, p5):
        sp.add_argument("--out-root", type=Path, required=True)

    args = p.parse_args(argv)
    report = {"clusters": cmd_clusters, "l4": cmd_l4, "l5": cmd_l5}[args.cmd](args)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
