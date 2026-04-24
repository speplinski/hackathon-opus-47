"""Layer 10 — evolution log assembler.

Reads the outputs of every preceding layer and assembles them into a
single DAG of :class:`EvolutionNode` / :class:`EvolutionEdge` records.
This is the backbone the demo UI traverses: "click any review →
follow it to the cluster it informed → to the L5 verdict it was
audited into → to the L7 decision made in response → to the L8
iterations that refined the decision."

Pure-Python — no Claude calls, no re-scoring. Everything here is
structural assembly over artifacts the pipeline already produced.

Graph structure
---------------
Nodes (EvolutionKind):

* ``review``    — one per member_review_id referenced by any cluster
  (deduplicated across clusters). ``payload_ref`` points at the L3b
  labeled-clusters file.
* ``cluster``   — one per InsightCluster in the L3b file.
* ``verdict``   — one per ReconciledVerdict (L5). ``verdict__{cluster_id}``.
* ``decision``  — one per DesignDecision (L7). Usually one per cluster.
* ``iteration`` — one per OptimizationIteration (L8 thin spine + L8
  multi-round loop). ``iteration__{cluster_id}__{NN}``.

``element`` kind (single design element inside an iteration) is not
emitted yet — decision snapshots are described as paragraphs, not
elements. Adding it requires a deeper L9 render; out of scope.

Edges (EvolutionRelation):

* review    → cluster    — ``informs``
* cluster   → verdict    — ``reconciled_into``
* verdict   → decision   — ``decided_as``     (L6 priority is
  side-channel metadata on the cluster/verdict pair, not a node; the
  L7 decision is the observable consequence.)
* decision  → iteration  — ``iterated_to``    (connects L7's
  decision to iteration 0, the baseline snapshot of the decision's
  before-state; iteration 1 then builds off iteration 0.)
* iteration → iteration  — ``iterated_to``    (parent → child for
  every OptimizationIteration with ``parent_iteration_id``).

Rejected loop iterations keep their parent-pointer edge — they are
dead-ends in the DAG, not removed. The UI filters on accepted vs
rejected; the evolution log preserves every attempt for audit.

Input / output
--------------
* Reads:

  - L3b labeled clusters (default: ``data/derived/l3b_labeled_clusters.jsonl``)
  - L5 reconciled verdicts (default: ``data/derived/l5_reconcile/l5_reconciled_*.jsonl``)
  - L7 design decisions (default: ``data/derived/l7_decide/l7_design_decisions_*.jsonl``)
  - L8 thin-spine iterations (default:
    ``data/derived/l8_optimize/l8_optimization_iterations_*.jsonl``)
  - L8 loop iterations (default: ``data/derived/l8_loop/l8_loop_iterations_*.jsonl``)

  The CLI accepts a single file per layer; callers that want the
  matched-model grid (multiple files per layer) can pick a specific
  modelshort or run the assembler once per model and concatenate.

* Writes:

  - ``evolution_nodes.jsonl`` — one EvolutionNode per row.
  - ``evolution_edges.jsonl`` — one EvolutionEdge per row.
  - ``evolution.provenance.json`` — counts per kind/relation and
    input-file hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from auditable_design.layers.l4_audit import (
    _atomic_write_bytes,
    _configure_logging,
    _default_run_id,
    _resolve_repo_root,
    load_clusters,
)
from auditable_design.layers.l6_weight import load_reconciled_verdicts
from auditable_design.layers.l7_decide import load_priority_scores  # noqa: F401
from auditable_design.layers.l8_optimize import load_decisions
from auditable_design.schemas import (
    SCHEMA_VERSION,
    DesignDecision,
    EvolutionEdge,
    EvolutionNode,
    InsightCluster,
    OptimizationIteration,
    ReconciledVerdict,
)
from auditable_design.storage import read_jsonl, write_jsonl_atomic

__all__ = [
    "DEFAULT_CLUSTERS",
    "DEFAULT_DECISIONS",
    "DEFAULT_EVOLUTION_EDGES",
    "DEFAULT_EVOLUTION_NODES",
    "DEFAULT_L8_ITERATIONS",
    "DEFAULT_L8_LOOP_ITERATIONS",
    "DEFAULT_RECONCILED",
    "EvolutionAssembly",
    "LAYER_NAME",
    "build_provenance",
    "assemble_evolution",
    "main",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LAYER_NAME: str = "l10_evolution"

DEFAULT_CLUSTERS = Path(
    "data/derived/l3b_labeled_clusters/l3b_labeled_clusters_full_opus47.jsonl"
)
DEFAULT_RECONCILED = Path(
    "data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl"
)
DEFAULT_DECISIONS = Path(
    "data/derived/l7_decide/l7_design_decisions_cluster02_opus46.jsonl"
)
DEFAULT_L8_ITERATIONS = Path(
    "data/derived/l8_optimize/l8_optimization_iterations_cluster02_opus47.jsonl"
)
DEFAULT_L8_LOOP_ITERATIONS = Path(
    "data/derived/l8_loop/l8_loop_iterations_cluster02_opus47_tchebycheff.jsonl"
)
DEFAULT_EVOLUTION_NODES = Path(
    "data/derived/l10_evolution/evolution_nodes.jsonl"
)
DEFAULT_EVOLUTION_EDGES = Path(
    "data/derived/l10_evolution/evolution_edges.jsonl"
)
DEFAULT_EVOLUTION_PROVENANCE = Path(
    "data/derived/l10_evolution/evolution.provenance.json"
)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EvolutionAssembly:
    """Complete assembly output.

    Each list is deduplicated by identity: nodes by ``node_id``,
    edges by the ``(src, relation, dst)`` triple. Both are ordered
    by insertion to keep reruns deterministic on identical inputs.
    """

    nodes: list[EvolutionNode] = field(default_factory=list)
    edges: list[EvolutionEdge] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cluster_id_of_reconciled(v: ReconciledVerdict) -> str:
    return v.cluster_id


def _verdict_node_id(cluster_id: str) -> str:
    return f"verdict__{cluster_id}"


def _cluster_id_of_decision(d: DesignDecision) -> str:
    # decision_id = "decision__{cluster_id}__{idx}"
    parts = d.decision_id.split("__")
    if len(parts) < 3 or parts[0] != "decision":
        raise ValueError(
            f"unexpected decision_id shape {d.decision_id!r}; "
            f"cannot derive cluster_id"
        )
    return "__".join(parts[1:-1])


def _cluster_id_of_iteration(it: OptimizationIteration) -> str:
    parts = it.iteration_id.split("__")
    if len(parts) < 3 or parts[0] != "iteration":
        raise ValueError(
            f"unexpected iteration_id shape {it.iteration_id!r}; "
            f"cannot derive cluster_id"
        )
    return "__".join(parts[1:-1])


def _sha256(path: Path) -> str:
    if not path.exists():
        return "missing"
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def assemble_evolution(
    *,
    clusters: list[InsightCluster],
    reconciled: list[ReconciledVerdict],
    decisions: list[DesignDecision],
    iterations: list[OptimizationIteration],
    clusters_path: Path,
    reconciled_path: Path,
    decisions_path: Path,
    iterations_path: Path,
    loop_iterations_path: Path | None = None,
) -> EvolutionAssembly:
    """Build the node/edge lists from in-memory pipeline inputs.

    ``iterations`` must include BOTH the L8 thin-spine iterations
    (index 0 baseline + index 1 proposed) AND the L8-loop iterations
    (index 2+). The caller concatenates the two files.

    Cross-validation:

    * Every cluster_id referenced by reconciled/decisions/iterations
      must exist among ``clusters``.
    * Every iteration's ``parent_iteration_id`` (when not ``None``)
      must exist among the iteration set.

    Raises ``RuntimeError`` on validation failure — the demo UI
    cannot render a DAG with dangling edges.
    """
    by_cluster: dict[str, InsightCluster] = {
        c.cluster_id: c for c in clusters
    }

    nodes: list[EvolutionNode] = []
    seen_node_ids: set[str] = set()

    def _add_node(node: EvolutionNode) -> None:
        if node.node_id in seen_node_ids:
            return
        seen_node_ids.add(node.node_id)
        nodes.append(node)

    # review nodes — one per unique member_review_id across clusters.
    for cluster in clusters:
        for rid in cluster.member_review_ids:
            _add_node(
                EvolutionNode(
                    node_id=rid,
                    kind="review",
                    payload_ref=str(clusters_path),
                )
            )

    # cluster nodes — one per cluster.
    for cluster in clusters:
        _add_node(
            EvolutionNode(
                node_id=cluster.cluster_id,
                kind="cluster",
                payload_ref=str(clusters_path),
            )
        )

    # verdict nodes — one per reconciled.
    for v in reconciled:
        cid = _cluster_id_of_reconciled(v)
        if cid not in by_cluster:
            raise RuntimeError(
                f"{LAYER_NAME}: reconciled refers to unknown cluster_id "
                f"{cid!r}; not present in {clusters_path}"
            )
        _add_node(
            EvolutionNode(
                node_id=_verdict_node_id(cid),
                kind="verdict",
                payload_ref=str(reconciled_path),
            )
        )

    # decision nodes — one per L7 decision.
    for d in decisions:
        cid = _cluster_id_of_decision(d)
        if cid not in by_cluster:
            raise RuntimeError(
                f"{LAYER_NAME}: decision refers to unknown cluster_id "
                f"{cid!r}; not present in {clusters_path}"
            )
        _add_node(
            EvolutionNode(
                node_id=d.decision_id,
                kind="decision",
                payload_ref=str(decisions_path),
            )
        )

    # iteration nodes — one per OptimizationIteration.
    by_iteration_id: dict[str, OptimizationIteration] = {}
    for it in iterations:
        cid = _cluster_id_of_iteration(it)
        if cid not in by_cluster:
            raise RuntimeError(
                f"{LAYER_NAME}: iteration refers to unknown cluster_id "
                f"{cid!r}; not present in {clusters_path}"
            )
        # Loop iterations (index ≥ 2) live in loop_iterations_path;
        # earlier ones live in iterations_path. Use the right payload.
        if it.iteration_index >= 2 and loop_iterations_path is not None:
            payload_ref = str(loop_iterations_path)
        else:
            payload_ref = str(iterations_path)
        _add_node(
            EvolutionNode(
                node_id=it.iteration_id,
                kind="iteration",
                payload_ref=payload_ref,
            )
        )
        by_iteration_id[it.iteration_id] = it

    # Edges.
    edges: list[EvolutionEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def _add_edge(src: str, relation: str, dst: str) -> None:
        key = (src, relation, dst)
        if key in seen_edges or src == dst:
            return
        seen_edges.add(key)
        edges.append(EvolutionEdge(src=src, dst=dst, relation=relation))  # type: ignore[arg-type]

    # review → cluster.
    for cluster in clusters:
        for rid in cluster.member_review_ids:
            _add_edge(rid, "informs", cluster.cluster_id)

    # cluster → verdict.
    for v in reconciled:
        cid = _cluster_id_of_reconciled(v)
        _add_edge(cid, "reconciled_into", _verdict_node_id(cid))

    # verdict → decision.
    for d in decisions:
        cid = _cluster_id_of_decision(d)
        _add_edge(
            _verdict_node_id(cid), "decided_as", d.decision_id
        )

    # decision → first iteration per cluster (iteration_index == 0).
    iterations_by_cluster: dict[str, list[OptimizationIteration]] = {}
    for it in iterations:
        iterations_by_cluster.setdefault(
            _cluster_id_of_iteration(it), []
        ).append(it)
    for cid, its in iterations_by_cluster.items():
        its_sorted = sorted(its, key=lambda x: x.iteration_index)
        if its_sorted[0].iteration_index != 0:
            # Missing baseline — skip decision→iteration bridge.
            continue
        # Connect every decision for this cluster to iteration 0.
        for d in decisions:
            if _cluster_id_of_decision(d) == cid:
                _add_edge(
                    d.decision_id, "iterated_to", its_sorted[0].iteration_id
                )

    # iteration → iteration (parent pointer).
    for it in iterations:
        if it.parent_iteration_id is None:
            continue
        if it.parent_iteration_id not in by_iteration_id:
            raise RuntimeError(
                f"{LAYER_NAME}: iteration {it.iteration_id!r} references "
                f"parent_iteration_id={it.parent_iteration_id!r} which is "
                f"not in the loaded iteration set"
            )
        _add_edge(
            it.parent_iteration_id, "iterated_to", it.iteration_id
        )

    return EvolutionAssembly(nodes=nodes, edges=edges)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def build_provenance(
    assembly: EvolutionAssembly,
    *,
    run_id: str,
    clusters_path: Path,
    reconciled_path: Path,
    decisions_path: Path,
    iterations_path: Path,
    loop_iterations_path: Path | None,
) -> dict[str, Any]:
    kind_counts: dict[str, int] = {}
    for n in assembly.nodes:
        kind_counts[n.kind] = kind_counts.get(n.kind, 0) + 1
    relation_counts: dict[str, int] = {}
    for e in assembly.edges:
        relation_counts[e.relation] = (
            relation_counts.get(e.relation, 0) + 1
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "layer": LAYER_NAME,
        "run_id": run_id,
        "node_count": len(assembly.nodes),
        "edge_count": len(assembly.edges),
        "nodes_by_kind": kind_counts,
        "edges_by_relation": relation_counts,
        "inputs": {
            "clusters_path": str(clusters_path),
            "clusters_sha256": _sha256(clusters_path),
            "reconciled_path": str(reconciled_path),
            "reconciled_sha256": _sha256(reconciled_path),
            "decisions_path": str(decisions_path),
            "decisions_sha256": _sha256(decisions_path),
            "iterations_path": str(iterations_path),
            "iterations_sha256": _sha256(iterations_path),
            "loop_iterations_path": (
                str(loop_iterations_path)
                if loop_iterations_path
                else None
            ),
            "loop_iterations_sha256": (
                _sha256(loop_iterations_path)
                if loop_iterations_path
                else None
            ),
        },
        "recorded_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_iterations(path: Path) -> list[OptimizationIteration]:
    if not path.exists():
        raise RuntimeError(
            f"{LAYER_NAME}: iterations file not found at {path}"
        )
    rows = read_jsonl(path)
    return [OptimizationIteration.model_validate(row) for row in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=LAYER_NAME,
        description=(
            "Assemble the evolution-log DAG over every preceding "
            "layer's outputs. Writes nodes + edges + provenance."
        ),
    )
    parser.add_argument("--clusters", type=Path, default=DEFAULT_CLUSTERS)
    parser.add_argument(
        "--reconciled", type=Path, default=DEFAULT_RECONCILED
    )
    parser.add_argument(
        "--decisions", type=Path, default=DEFAULT_DECISIONS
    )
    parser.add_argument(
        "--iterations", type=Path, default=DEFAULT_L8_ITERATIONS,
        help="L8 thin-spine iterations jsonl (iter 0 + iter 1)",
    )
    parser.add_argument(
        "--loop-iterations", type=Path, default=DEFAULT_L8_LOOP_ITERATIONS,
        help="L8-loop iterations jsonl (iter 2+); pass --loop-iterations '' to skip",
    )
    parser.add_argument(
        "--nodes-output", type=Path, default=DEFAULT_EVOLUTION_NODES
    )
    parser.add_argument(
        "--edges-output", type=Path, default=DEFAULT_EVOLUTION_EDGES
    )
    parser.add_argument(
        "--provenance-output",
        type=Path,
        default=DEFAULT_EVOLUTION_PROVENANCE,
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    run_id = args.run_id or _default_run_id()

    clusters = load_clusters(args.clusters)
    reconciled = list(load_reconciled_verdicts(args.reconciled).values())
    decisions = list(load_decisions(args.decisions).values())

    iters = _load_iterations(args.iterations)
    loop_iters: list[OptimizationIteration] = []
    loop_path: Path | None = args.loop_iterations
    if loop_path and str(loop_path) != "" and loop_path.exists():
        loop_iters = _load_iterations(loop_path)
    else:
        loop_path = None

    all_iters = iters + loop_iters

    assembly = assemble_evolution(
        clusters=clusters,
        reconciled=reconciled,
        decisions=decisions,
        iterations=all_iters,
        clusters_path=args.clusters,
        reconciled_path=args.reconciled,
        decisions_path=args.decisions,
        iterations_path=args.iterations,
        loop_iterations_path=loop_path,
    )

    input_hashes: dict[str, str] = {
        "clusters": _sha256(args.clusters),
        "reconciled": _sha256(args.reconciled),
        "decisions": _sha256(args.decisions),
        "iterations": _sha256(args.iterations),
    }
    if loop_path is not None:
        input_hashes["loop_iterations"] = _sha256(loop_path)

    args.nodes_output.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl_atomic(
        args.nodes_output,
        [n.model_dump(mode="json") for n in assembly.nodes],
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes=input_hashes,
    )
    write_jsonl_atomic(
        args.edges_output,
        [e.model_dump(mode="json") for e in assembly.edges],
        run_id=run_id,
        layer=LAYER_NAME,
        input_hashes=input_hashes,
    )
    prov = build_provenance(
        assembly,
        run_id=run_id,
        clusters_path=args.clusters,
        reconciled_path=args.reconciled,
        decisions_path=args.decisions,
        iterations_path=args.iterations,
        loop_iterations_path=loop_path,
    )
    _atomic_write_bytes(
        args.provenance_output,
        (json.dumps(prov, indent=2) + "\n").encode("utf-8"),
    )

    _log.info(
        "evolution assembled: nodes=%d edges=%d (by kind: %s; by relation: %s)",
        len(assembly.nodes),
        len(assembly.edges),
        prov["nodes_by_kind"],
        prov["edges_by_relation"],
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
