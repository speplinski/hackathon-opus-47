"""Tests for the layer schemas (ARCHITECTURE §4).

We cover three concerns:

1. Happy-path construction — every model accepts a minimal valid payload.
2. Invariants enforced by `@model_validator` fire on bad input.
3. Cross-artifact validator (§4.3 P1) catches hallucinated quotes.

We don't over-test Pydantic itself (field coercion, JSON round-trip) —
those are Pydantic's responsibility. We test the rules we added: FK
shapes, bounded ints, non-empty contracts, offset consistency, self-loops,
parent/index consistency, and the substring invariant.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from auditable_design.schemas import (
    SCHEMA_VERSION,
    AuditVerdict,
    ClassifiedReview,
    ComplaintEdge,
    ComplaintGraph,
    ComplaintNode,
    DesignDecision,
    DesignPrinciple,
    EvolutionEdge,
    EvolutionNode,
    HeuristicViolation,
    InsightCluster,
    OptimizationIteration,
    PriorityScore,
    RawReview,
    ReconciledVerdict,
    RunContext,
    SchemaValidationError,
    SkillTension,
    validate_complaint_graph_against_source,
)

NOW = datetime(2026, 4, 22, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------


def test_schema_version_is_a_positive_int() -> None:
    """Sidecar meta.json carries this verbatim — it must stay an int."""
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1


# ---------------------------------------------------------------------------
# §4.1 RawReview
# ---------------------------------------------------------------------------


def test_raw_review_minimal_valid() -> None:
    r = RawReview(
        review_id="rev-1",
        source="google_play",
        author_hash="deadbeef",
        timestamp_utc=NOW,
        rating=3,
        text="it crashes",
        lang="en",
    )
    assert r.rating == 3
    assert r.app_version is None


@pytest.mark.parametrize("rating", [0, 6, -1, 10])
def test_raw_review_rejects_rating_out_of_range(rating: int) -> None:
    with pytest.raises(ValidationError):
        RawReview(
            review_id="rev-1",
            source="google_play",
            author_hash="h",
            timestamp_utc=NOW,
            rating=rating,
            text="x",
            lang="en",
        )


def test_raw_review_rejects_unknown_source() -> None:
    with pytest.raises(ValidationError):
        RawReview(
            review_id="rev-1",
            source="pirate_forum",  # type: ignore[arg-type]
            author_hash="h",
            timestamp_utc=NOW,
            rating=1,
            text="x",
            lang="en",
        )


def test_raw_review_rejects_extra_fields() -> None:
    """extra='forbid' is a core invariant — a stray `email` field from a
    scraper would be both a schema violation AND a PII leak (V-04)."""
    with pytest.raises(ValidationError):
        RawReview.model_validate(
            {
                "review_id": "rev-1",
                "source": "google_play",
                "author_hash": "h",
                "timestamp_utc": NOW.isoformat(),
                "rating": 1,
                "text": "x",
                "lang": "en",
                "email": "leaked@example.com",  # must be rejected
            }
        )


# ---------------------------------------------------------------------------
# §4.2 ClassifiedReview
# ---------------------------------------------------------------------------


def test_classified_review_confidence_bounds() -> None:
    # Valid
    ClassifiedReview(
        review_id="rev-1",
        is_ux_relevant=True,
        classifier_confidence=0.0,
        classified_at=NOW,
    )
    ClassifiedReview(
        review_id="rev-1",
        is_ux_relevant=True,
        classifier_confidence=1.0,
        classified_at=NOW,
    )
    # Invalid
    with pytest.raises(ValidationError):
        ClassifiedReview(
            review_id="rev-1",
            is_ux_relevant=True,
            classifier_confidence=1.5,
            classified_at=NOW,
        )


# ---------------------------------------------------------------------------
# §4.3 ComplaintGraph
# ---------------------------------------------------------------------------


def _node(nid: str, *, quote: str, start: int) -> ComplaintNode:
    return ComplaintNode(
        node_id=nid,
        node_type="pain",
        verbatim_quote=quote,
        quote_start=start,
        quote_end=start + len(quote),
    )


def test_complaint_node_offset_span_must_match_quote_length() -> None:
    with pytest.raises(ValidationError, match="offsets must span"):
        ComplaintNode(
            node_id="n1",
            node_type="pain",
            verbatim_quote="crashes",
            quote_start=0,
            quote_end=3,  # wrong — "crashes" is length 7
        )


def test_complaint_node_rejects_reversed_offsets() -> None:
    with pytest.raises(ValidationError, match="quote_end"):
        ComplaintNode(
            node_id="n1",
            node_type="pain",
            verbatim_quote="x",
            quote_start=5,
            quote_end=5,
        )


def test_complaint_graph_requires_three_nodes() -> None:
    with pytest.raises(ValidationError):
        ComplaintGraph(
            review_id="rev-1",
            nodes=[_node("n1", quote="a", start=0), _node("n2", quote="b", start=1)],
            edges=[],
        )


def test_complaint_graph_rejects_duplicate_node_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate node_id"):
        ComplaintGraph(
            review_id="rev-1",
            nodes=[
                _node("n1", quote="a", start=0),
                _node("n1", quote="b", start=1),
                _node("n2", quote="c", start=2),
            ],
            edges=[],
        )


def test_complaint_graph_rejects_dangling_edge() -> None:
    nodes = [
        _node("n1", quote="a", start=0),
        _node("n2", quote="b", start=1),
        _node("n3", quote="c", start=2),
    ]
    bad_edge = ComplaintEdge(src="n1", dst="does-not-exist", relation="triggers")
    with pytest.raises(ValidationError, match="references unknown node"):
        ComplaintGraph(review_id="rev-1", nodes=nodes, edges=[bad_edge])


def test_complaint_edge_rejects_self_loop() -> None:
    with pytest.raises(ValidationError, match="self-loop"):
        ComplaintEdge(src="n1", dst="n1", relation="triggers")


# ---------------------------------------------------------------------------
# §4.3 P1 — hallucination safeguard (cross-artifact validator)
# ---------------------------------------------------------------------------


SOURCE_TEXT = "the app crashes on login, super frustrating"


def _graph_with_first_node(quote: str, start: int) -> ComplaintGraph:
    """Build a 3-node graph where n1 carries the quote under test and
    n2/n3 are benign fillers pointing at known-good offsets in SOURCE_TEXT.

    Filler quotes MUST correspond to real slices of SOURCE_TEXT so the
    cross-artifact validator's pass-case isn't polluted by filler failures.
    """
    fill2 = SOURCE_TEXT[0:3]  # "the"
    fill3 = SOURCE_TEXT[4:7]  # "app"
    nodes = [
        ComplaintNode(
            node_id="n1",
            node_type="pain",
            verbatim_quote=quote,
            quote_start=start,
            quote_end=start + len(quote),
        ),
        ComplaintNode(
            node_id="n2",
            node_type="pain",
            verbatim_quote=fill2,
            quote_start=0,
            quote_end=3,
        ),
        ComplaintNode(
            node_id="n3",
            node_type="pain",
            verbatim_quote=fill3,
            quote_start=4,
            quote_end=7,
        ),
    ]
    return ComplaintGraph(review_id="rev-1", nodes=nodes, edges=[])


def test_validate_complaint_graph_accepts_matching_quote() -> None:
    graph = _graph_with_first_node(quote="crashes", start=8)
    validate_complaint_graph_against_source(graph, source_text=SOURCE_TEXT)


def test_validate_complaint_graph_rejects_hallucinated_quote() -> None:
    """Classic Claude failure mode: plausible-sounding quote that does
    not appear in the source. This MUST be caught — it's the difference
    between a traceable method and a fiction generator."""
    graph = _graph_with_first_node(quote="slowness on upload", start=0)
    with pytest.raises(SchemaValidationError, match="does not match source"):
        validate_complaint_graph_against_source(graph, source_text=SOURCE_TEXT)


def test_validate_complaint_graph_rejects_out_of_bounds_offset() -> None:
    # Use a standalone tiny source where n1's offset overruns,
    # and the fillers don't need to be valid (we expect the validator
    # to fail on n1 first, before touching n2/n3).
    source = "short"
    # The fillers here target offsets outside "short" too — but the
    # validator iterates in order and n1 raises immediately on the
    # bounds check, so we never reach them.
    n1 = ComplaintNode(
        node_id="n1",
        node_type="pain",
        verbatim_quote="short",
        quote_start=999,
        quote_end=999 + len("short"),
    )
    n2 = ComplaintNode(node_id="n2", node_type="pain", verbatim_quote="s", quote_start=0, quote_end=1)
    n3 = ComplaintNode(node_id="n3", node_type="pain", verbatim_quote="h", quote_start=1, quote_end=2)
    graph = ComplaintGraph(review_id="rev-1", nodes=[n1, n2, n3], edges=[])
    with pytest.raises(SchemaValidationError, match="exceeds"):
        validate_complaint_graph_against_source(graph, source_text=source)


# ---------------------------------------------------------------------------
# §4.4 InsightCluster
# ---------------------------------------------------------------------------


def test_insight_cluster_caps_representative_quotes_at_five() -> None:
    with pytest.raises(ValidationError):
        InsightCluster(
            cluster_id="c1",
            label="login crashes",
            member_review_ids=["r1"],
            centroid_vector_ref="data/embeddings/c1.npy",
            representative_quotes=["a", "b", "c", "d", "e", "f"],
        )


# ---------------------------------------------------------------------------
# §4.5 AuditVerdict + HeuristicViolation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("sev", [-1, 11, 100])
def test_heuristic_violation_rejects_out_of_range_severity(sev: int) -> None:
    with pytest.raises(ValidationError):
        HeuristicViolation(
            heuristic="visibility_of_system_status",
            violation="no loading indicator",
            severity=sev,
            reasoning="r",
        )


def test_audit_verdict_requires_64_char_skill_hash() -> None:
    bad_hash = "abc123"
    with pytest.raises(ValidationError):
        AuditVerdict(
            verdict_id="v1",
            cluster_id="c1",
            skill_id="audit-usability-fundamentals",
            relevant_heuristics=[],
            produced_at=NOW,
            claude_model="claude-opus-4-6",
            skill_hash=bad_hash,
        )


def test_audit_verdict_minimal_valid() -> None:
    v = AuditVerdict(
        verdict_id="v1",
        cluster_id="c1",
        skill_id="audit-usability-fundamentals",
        relevant_heuristics=[
            HeuristicViolation(
                heuristic="visibility_of_system_status",
                violation="no spinner",
                severity=6,
                reasoning="user stares at a blank screen for 4s",
            )
        ],
        produced_at=NOW,
        claude_model="claude-opus-4-6",
        skill_hash="a" * 64,
    )
    assert v.skill_id == "audit-usability-fundamentals"


# ---------------------------------------------------------------------------
# §4.6 ReconciledVerdict
# ---------------------------------------------------------------------------


def test_reconciled_verdict_roundtrips_tension() -> None:
    r = ReconciledVerdict(
        cluster_id="c1",
        ranked_violations=[],
        tensions=[
            SkillTension(
                skill_a="audit-usability-fundamentals",
                skill_b="audit-interaction-design",
                axis="user_control",
                resolution="defer to norman on novice path, cooper on expert path",
            )
        ],
    )
    assert r.tensions[0].axis == "user_control"


# ---------------------------------------------------------------------------
# §4.7 PriorityScore
# ---------------------------------------------------------------------------


def test_priority_score_requires_five_dimensions() -> None:
    with pytest.raises(ValidationError):
        PriorityScore(
            cluster_id="c1",
            dimensions={"severity": 7, "reach": 6, "persistence": 5},  # only 3
            meta_weights={"severity": 0.3},
            weighted_total=6.0,
            validation_passes=2,
            validation_delta=0.1,
        )


def test_priority_score_rejects_dimension_over_ten() -> None:
    with pytest.raises(ValidationError, match=r"out of \[0, 10\]"):
        PriorityScore(
            cluster_id="c1",
            dimensions={
                "severity": 7,
                "reach": 6,
                "persistence": 5,
                "business_impact": 8,
                "cognitive_cost": 11,  # out of range
            },
            meta_weights={"severity": 0.2},
            weighted_total=6.5,
            validation_passes=2,
            validation_delta=0.0,
        )


# ---------------------------------------------------------------------------
# §4.8 DesignDecision, DesignPrinciple, OptimizationIteration
# ---------------------------------------------------------------------------


def test_design_decision_requires_at_least_one_resolved_heuristic() -> None:
    """ADR: a decision that resolves nothing is not traceable — reject it."""
    with pytest.raises(ValidationError):
        DesignDecision(
            decision_id="d1",
            principle_id="p1",
            description="rounded corners",
            before_snapshot="before.png",
            after_snapshot="after.png",
            resolves_heuristics=[],
        )


def test_design_principle_requires_derivation_from_reviews() -> None:
    """A principle with no review provenance is a designer's opinion,
    not a user-derived finding — reject."""
    with pytest.raises(ValidationError):
        DesignPrinciple(
            principle_id="p1",
            cluster_id="c1",
            name="Progressive disclosure",
            statement="hide advanced options until user confirms intent",
            derived_from_review_ids=[],
        )


def test_optimization_iteration_index_zero_must_have_no_parent() -> None:
    with pytest.raises(ValidationError, match="iteration_index=0"):
        OptimizationIteration(
            iteration_id="it-0",
            run_id="2026-04-22_test",
            iteration_index=0,
            parent_iteration_id="it-(-1)",  # nonsensical
            design_artifact_ref="data/artifacts/iterations/0.json",
            scores={},
            reasoning="seed",
            accepted=True,
            recorded_at=NOW,
        )


def test_optimization_iteration_non_zero_must_have_parent() -> None:
    with pytest.raises(ValidationError, match="must have a parent"):
        OptimizationIteration(
            iteration_id="it-1",
            run_id="2026-04-22_test",
            iteration_index=1,
            parent_iteration_id=None,
            design_artifact_ref="data/artifacts/iterations/1.json",
            scores={},
            reasoning="tweak",
            accepted=True,
            recorded_at=NOW,
        )


def test_optimization_iteration_rejected_requires_regression_reason() -> None:
    with pytest.raises(ValidationError, match="regression_reason"):
        OptimizationIteration(
            iteration_id="it-2",
            run_id="2026-04-22_test",
            iteration_index=2,
            parent_iteration_id="it-1",
            design_artifact_ref="data/artifacts/iterations/2.json",
            scores={},
            reasoning="tweak",
            accepted=False,
            regression_reason=None,
            recorded_at=NOW,
        )


def test_optimization_iteration_score_out_of_range() -> None:
    with pytest.raises(ValidationError, match=r"out of \[0, 10\]"):
        OptimizationIteration(
            iteration_id="it-1",
            run_id="2026-04-22_test",
            iteration_index=1,
            parent_iteration_id="it-0",
            design_artifact_ref="data/artifacts/iterations/1.json",
            scores={"audit-usability-fundamentals": {"visibility": 42}},
            reasoning="tweak",
            accepted=True,
            recorded_at=NOW,
        )


# ---------------------------------------------------------------------------
# §4.9 Evolution graph
# ---------------------------------------------------------------------------


def test_evolution_node_happy_path() -> None:
    n = EvolutionNode(node_id="ev-1", kind="review", payload_ref="data/raw/r1.json")
    assert n.kind == "review"


def test_evolution_edge_rejects_self_loop() -> None:
    with pytest.raises(ValidationError, match="self-loop"):
        EvolutionEdge(src="x", dst="x", relation="informs")


def test_evolution_edge_rejects_unknown_relation() -> None:
    with pytest.raises(ValidationError):
        EvolutionEdge(src="a", dst="b", relation="flimflammed")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# §5.4 RunContext
# ---------------------------------------------------------------------------


def test_run_context_happy_path() -> None:
    ctx = RunContext(
        run_id="2026-04-22_pilot",
        seed=42,
        skill_model_config={"audit-usability-fundamentals": "claude-opus-4-6"},
        meta_weights={"severity": 0.4, "reach": 0.3},
        active_skills=["audit-usability-fundamentals"],
    )
    assert ctx.optimization_budget == 8  # default
    assert ctx.usd_ceiling == 15.0  # default


def test_run_context_run_id_pattern_matches_storage_module() -> None:
    """The run_id regex in schemas.py MUST match storage.RUN_ID_PATTERN —
    otherwise a run_id accepted at scheduling time would be refused at
    write time (or vice versa)."""
    from auditable_design.storage import RUN_ID_PATTERN

    # Sample values the storage pattern accepts — all must also pass here.
    for ok in ["2026-04-22_pilot", "r", "a.b_c-d.1"]:
        RunContext(
            run_id=ok,
            seed=0,
            skill_model_config={},
            meta_weights={},
            active_skills=["x"],
        )
        assert RUN_ID_PATTERN.fullmatch(ok)


def test_run_context_rejects_bad_run_id() -> None:
    with pytest.raises(ValidationError):
        RunContext(
            run_id="../escape",
            seed=0,
            skill_model_config={},
            meta_weights={},
            active_skills=["x"],
        )


def test_run_context_is_frozen() -> None:
    """Mutating a RunContext mid-run would decouple later artifacts from
    earlier ones, breaking provenance. Frozen is a hard invariant."""
    ctx = RunContext(
        run_id="r",
        seed=0,
        skill_model_config={},
        meta_weights={},
        active_skills=["x"],
    )
    with pytest.raises(ValidationError):
        ctx.seed = 99  # type: ignore[misc]


def test_run_context_requires_at_least_one_active_skill() -> None:
    with pytest.raises(ValidationError):
        RunContext(
            run_id="r",
            seed=0,
            skill_model_config={},
            meta_weights={},
            active_skills=[],
        )
