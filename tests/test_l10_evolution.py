"""Tests for ``auditable_design.layers.l10_evolution``.

Pure-Python assembler. Unit tests for graph construction, identity
deduplication, DAG integrity, and cross-validation failure modes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from auditable_design.layers.l10_evolution import (
    EvolutionAssembly,
    LAYER_NAME,
    _cluster_id_of_decision,
    _cluster_id_of_iteration,
    _verdict_node_id,
    assemble_evolution,
    build_provenance,
)
from auditable_design.schemas import (
    DesignDecision,
    HeuristicViolation,
    InsightCluster,
    OptimizationIteration,
    ReconciledVerdict,
    SkillTension,
)


# =============================================================================
# Fixtures
# =============================================================================


def _cluster(
    cluster_id: str = "cluster_02",
    *,
    members: list[str] | None = None,
) -> InsightCluster:
    return InsightCluster(
        cluster_id=cluster_id,
        label=f"label for {cluster_id}",
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref="l3_centroids.npy#0",
        representative_quotes=["q1"],
    )


def _reconciled(cluster_id: str = "cluster_02") -> ReconciledVerdict:
    return ReconciledVerdict(
        cluster_id=cluster_id,
        ranked_violations=[
            HeuristicViolation(
                heuristic="h1",
                violation="bad thing",
                severity=7,
                evidence_review_ids=[],
                reasoning="because",
            ),
        ],
        tensions=[
            SkillTension(
                skill_a="audit-interaction-design",
                skill_b="audit-decision-psychology",
                axis="x",
                resolution="y",
            ),
        ],
    )


def _decision(
    cluster_id: str = "cluster_02",
    *,
    idx: int = 1,
) -> DesignDecision:
    return DesignDecision(
        decision_id=f"decision__{cluster_id}__{idx}",
        principle_id=f"principle__{cluster_id}",
        description="do a thing",
        before_snapshot="before",
        after_snapshot="after",
        resolves_heuristics=["h1"],
    )


def _iter(
    cluster_id: str = "cluster_02",
    *,
    index: int,
    parent_index: int | None = None,
    accepted: bool = True,
) -> OptimizationIteration:
    parent_id = (
        f"iteration__{cluster_id}__{parent_index:02d}"
        if parent_index is not None
        else None
    )
    return OptimizationIteration(
        iteration_id=f"iteration__{cluster_id}__{index:02d}",
        run_id="test",
        iteration_index=index,
        parent_iteration_id=parent_id,
        design_artifact_ref=f"/tmp/iter{index}.md",
        scores={"reconciled": {"h1": 0 if accepted else 7}},
        reasoning="x",
        accepted=accepted,
        regression_reason=None if accepted else "bad",
        delta_per_heuristic={},
        informing_review_ids=["r1"],
        recorded_at=datetime.now(UTC),
    )


# =============================================================================
# Happy-path assembly
# =============================================================================


class TestAssembleHappyPath:
    def _call(self) -> EvolutionAssembly:
        return assemble_evolution(
            clusters=[_cluster()],
            reconciled=[_reconciled()],
            decisions=[_decision()],
            iterations=[
                _iter(index=0),
                _iter(index=1, parent_index=0),
                _iter(index=2, parent_index=1),
            ],
            clusters_path=Path("/x/clusters.jsonl"),
            reconciled_path=Path("/x/rec.jsonl"),
            decisions_path=Path("/x/dec.jsonl"),
            iterations_path=Path("/x/iters.jsonl"),
            loop_iterations_path=Path("/x/loop.jsonl"),
        )

    def test_node_counts_by_kind(self) -> None:
        a = self._call()
        kinds = [n.kind for n in a.nodes]
        assert kinds.count("review") == 3  # r1, r2, r3
        assert kinds.count("cluster") == 1
        assert kinds.count("verdict") == 1
        assert kinds.count("decision") == 1
        assert kinds.count("iteration") == 3

    def test_verdict_node_id_follows_convention(self) -> None:
        a = self._call()
        verdict_ids = [n.node_id for n in a.nodes if n.kind == "verdict"]
        assert verdict_ids == ["verdict__cluster_02"]

    def test_review_nodes_deduplicated_across_clusters(self) -> None:
        a = assemble_evolution(
            clusters=[
                _cluster("cluster_02", members=["r1", "r2"]),
                _cluster("cluster_03", members=["r2", "r3"]),  # r2 overlaps
            ],
            reconciled=[],
            decisions=[],
            iterations=[],
            clusters_path=Path("/x/clusters.jsonl"),
            reconciled_path=Path("/x/rec.jsonl"),
            decisions_path=Path("/x/dec.jsonl"),
            iterations_path=Path("/x/iters.jsonl"),
        )
        review_ids = sorted(
            n.node_id for n in a.nodes if n.kind == "review"
        )
        assert review_ids == ["r1", "r2", "r3"]

    def test_edge_counts_by_relation(self) -> None:
        a = self._call()
        relations = [e.relation for e in a.edges]
        assert relations.count("informs") == 3
        assert relations.count("reconciled_into") == 1
        assert relations.count("decided_as") == 1
        # decision → iter0 + iter0→iter1 + iter1→iter2 = 3 iterated_to
        assert relations.count("iterated_to") == 3

    def test_edges_no_self_loops(self) -> None:
        a = self._call()
        for e in a.edges:
            assert e.src != e.dst

    def test_edges_dst_nodes_exist(self) -> None:
        a = self._call()
        node_ids = {n.node_id for n in a.nodes}
        for e in a.edges:
            assert e.src in node_ids, f"dangling src: {e.src}"
            assert e.dst in node_ids, f"dangling dst: {e.dst}"

    def test_decision_connects_to_iter0(self) -> None:
        a = self._call()
        dec_id = "decision__cluster_02__1"
        iter0_id = "iteration__cluster_02__00"
        matches = [
            e for e in a.edges
            if e.src == dec_id and e.dst == iter0_id
            and e.relation == "iterated_to"
        ]
        assert len(matches) == 1

    def test_parent_child_iteration_edges(self) -> None:
        a = self._call()
        chain = [
            ("iteration__cluster_02__00", "iteration__cluster_02__01"),
            ("iteration__cluster_02__01", "iteration__cluster_02__02"),
        ]
        for src, dst in chain:
            assert any(
                e.src == src and e.dst == dst and e.relation == "iterated_to"
                for e in a.edges
            ), f"missing edge {src} → {dst}"


# =============================================================================
# Payload refs
# =============================================================================


class TestPayloadRefs:
    def test_loop_iterations_use_loop_payload_ref(self) -> None:
        a = assemble_evolution(
            clusters=[_cluster()],
            reconciled=[_reconciled()],
            decisions=[_decision()],
            iterations=[
                _iter(index=0),
                _iter(index=1, parent_index=0),
                _iter(index=2, parent_index=1),
            ],
            clusters_path=Path("/x/clusters.jsonl"),
            reconciled_path=Path("/x/rec.jsonl"),
            decisions_path=Path("/x/dec.jsonl"),
            iterations_path=Path("/x/spine.jsonl"),
            loop_iterations_path=Path("/x/loop.jsonl"),
        )
        refs = {n.node_id: n.payload_ref for n in a.nodes if n.kind == "iteration"}
        assert refs["iteration__cluster_02__00"].endswith("spine.jsonl")
        assert refs["iteration__cluster_02__01"].endswith("spine.jsonl")
        assert refs["iteration__cluster_02__02"].endswith("loop.jsonl")

    def test_no_loop_path_falls_back_to_spine(self) -> None:
        a = assemble_evolution(
            clusters=[_cluster()],
            reconciled=[_reconciled()],
            decisions=[_decision()],
            iterations=[_iter(index=0), _iter(index=1, parent_index=0)],
            clusters_path=Path("/x/clusters.jsonl"),
            reconciled_path=Path("/x/rec.jsonl"),
            decisions_path=Path("/x/dec.jsonl"),
            iterations_path=Path("/x/spine.jsonl"),
            loop_iterations_path=None,
        )
        refs = {n.node_id: n.payload_ref for n in a.nodes if n.kind == "iteration"}
        assert refs["iteration__cluster_02__00"].endswith("spine.jsonl")


# =============================================================================
# Cross-validation failure modes
# =============================================================================


class TestCrossValidation:
    def test_reconciled_referring_unknown_cluster_raises(self) -> None:
        with pytest.raises(RuntimeError, match="unknown cluster_id"):
            assemble_evolution(
                clusters=[_cluster("cluster_02")],
                reconciled=[_reconciled("cluster_99")],  # unknown
                decisions=[],
                iterations=[],
                clusters_path=Path("/x/c.jsonl"),
                reconciled_path=Path("/x/r.jsonl"),
                decisions_path=Path("/x/d.jsonl"),
                iterations_path=Path("/x/i.jsonl"),
            )

    def test_decision_referring_unknown_cluster_raises(self) -> None:
        with pytest.raises(RuntimeError, match="unknown cluster_id"):
            assemble_evolution(
                clusters=[_cluster("cluster_02")],
                reconciled=[],
                decisions=[_decision("cluster_99")],  # unknown
                iterations=[],
                clusters_path=Path("/x/c.jsonl"),
                reconciled_path=Path("/x/r.jsonl"),
                decisions_path=Path("/x/d.jsonl"),
                iterations_path=Path("/x/i.jsonl"),
            )

    def test_iteration_referring_unknown_cluster_raises(self) -> None:
        with pytest.raises(RuntimeError, match="unknown cluster_id"):
            assemble_evolution(
                clusters=[_cluster("cluster_02")],
                reconciled=[],
                decisions=[],
                iterations=[_iter("cluster_99", index=0)],
                clusters_path=Path("/x/c.jsonl"),
                reconciled_path=Path("/x/r.jsonl"),
                decisions_path=Path("/x/d.jsonl"),
                iterations_path=Path("/x/i.jsonl"),
            )

    def test_iteration_with_missing_parent_raises(self) -> None:
        # iter 1 references parent_iteration_id that doesn't exist in the set
        with pytest.raises(RuntimeError, match="parent_iteration_id"):
            assemble_evolution(
                clusters=[_cluster()],
                reconciled=[],
                decisions=[],
                iterations=[
                    _iter(index=1, parent_index=0),  # no iter 0 present
                ],
                clusters_path=Path("/x/c.jsonl"),
                reconciled_path=Path("/x/r.jsonl"),
                decisions_path=Path("/x/d.jsonl"),
                iterations_path=Path("/x/i.jsonl"),
            )


# =============================================================================
# Rejected iterations preserved
# =============================================================================


class TestRejectedIterations:
    def test_rejected_loop_iter_still_in_graph(self) -> None:
        a = assemble_evolution(
            clusters=[_cluster()],
            reconciled=[_reconciled()],
            decisions=[_decision()],
            iterations=[
                _iter(index=0),
                _iter(index=1, parent_index=0),
                _iter(index=2, parent_index=1, accepted=False),
            ],
            clusters_path=Path("/x/c.jsonl"),
            reconciled_path=Path("/x/r.jsonl"),
            decisions_path=Path("/x/d.jsonl"),
            iterations_path=Path("/x/i.jsonl"),
            loop_iterations_path=Path("/x/l.jsonl"),
        )
        iter_ids = [n.node_id for n in a.nodes if n.kind == "iteration"]
        assert "iteration__cluster_02__02" in iter_ids


# =============================================================================
# Helpers
# =============================================================================


class TestHelpers:
    def test_cluster_id_of_decision(self) -> None:
        d = _decision("cluster_02")
        assert _cluster_id_of_decision(d) == "cluster_02"

    def test_cluster_id_of_iteration(self) -> None:
        i = _iter("cluster_02", index=3, parent_index=2)
        assert _cluster_id_of_iteration(i) == "cluster_02"

    def test_verdict_node_id_format(self) -> None:
        assert _verdict_node_id("cluster_02") == "verdict__cluster_02"

    def test_bad_decision_id_raises(self) -> None:
        bad = _decision()
        bad = bad.model_copy(update={"decision_id": "malformed"})
        with pytest.raises(ValueError, match="cluster_id"):
            _cluster_id_of_decision(bad)


# =============================================================================
# Provenance
# =============================================================================


class TestProvenance:
    def test_counts_populated(self, tmp_path: Path) -> None:
        a = assemble_evolution(
            clusters=[_cluster()],
            reconciled=[_reconciled()],
            decisions=[_decision()],
            iterations=[_iter(index=0), _iter(index=1, parent_index=0)],
            clusters_path=tmp_path / "c.jsonl",
            reconciled_path=tmp_path / "r.jsonl",
            decisions_path=tmp_path / "d.jsonl",
            iterations_path=tmp_path / "i.jsonl",
        )
        prov = build_provenance(
            a,
            run_id="t",
            clusters_path=tmp_path / "c.jsonl",
            reconciled_path=tmp_path / "r.jsonl",
            decisions_path=tmp_path / "d.jsonl",
            iterations_path=tmp_path / "i.jsonl",
            loop_iterations_path=None,
        )
        assert prov["layer"] == LAYER_NAME
        assert prov["node_count"] == len(a.nodes)
        assert prov["edge_count"] == len(a.edges)
        assert "review" in prov["nodes_by_kind"]
        assert "informs" in prov["edges_by_relation"]
        assert prov["inputs"]["loop_iterations_path"] is None


# =============================================================================
# Edge deduplication
# =============================================================================


class TestEdgeDedup:
    def test_same_edge_not_emitted_twice(self) -> None:
        """Two clusters can't legally share a member_review_id with the
        same cluster_id; but the dedup guard is defensive. Exercise
        it by passing identical clusters."""
        a = assemble_evolution(
            clusters=[_cluster("cluster_02"), _cluster("cluster_02")],  # dupe
            reconciled=[],
            decisions=[],
            iterations=[],
            clusters_path=Path("/x/c.jsonl"),
            reconciled_path=Path("/x/r.jsonl"),
            decisions_path=Path("/x/d.jsonl"),
            iterations_path=Path("/x/i.jsonl"),
        )
        # Only one cluster node, only one set of informs edges.
        cluster_nodes = [n for n in a.nodes if n.kind == "cluster"]
        assert len(cluster_nodes) == 1
        informs = [e for e in a.edges if e.relation == "informs"]
        assert len(informs) == 3  # r1, r2, r3 — not 6
