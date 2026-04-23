"""Pydantic models for all 10 pipeline layers.

This module is the authoritative data model for Auditable Design. Every
artifact written to `data/derived/*.jsonl` is an instance of a model
defined here, serialised row-per-record.

Design notes
------------
* Pydantic 2.x. We use `ConfigDict(extra="forbid")` everywhere — an
  unexpected field in a layer output is a red flag, not a feature, and
  the whole point of schema contracts is to catch drift between a
  prompt tweak and a consumer downstream.
* Models are NOT frozen by default. A naive "frozen everywhere" would
  be safer but makes it inconvenient for tests and fixture builders.
  Where a model becomes a hash key (run context, etc.), we freeze.
* Cross-artifact invariants that cannot be expressed on a single
  record (e.g. "verbatim quote is a substring of source review text")
  are enforced by standalone validator functions at the bottom of the
  module. The layer-runner calls them at ingest time, per ADR §4.3 P1.
* SCHEMA_VERSION lives here and is written into every sidecar
  (storage.ArtifactMeta.schema_version). Bump on breaking changes.

See ARCHITECTURE.md §4 for the narrative spec and the threat-model P1
(hallucination), P3 (uniform audit contract) constraints.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Grouped by pipeline layer on purpose; alphabetical sort would destroy the
# implicit table of contents this list provides for readers.
__all__ = [  # noqa: RUF022
    "SCHEMA_VERSION",
    # Raw / ingest
    "RawReview",
    # Layer 1
    "ClassifiedReview",
    # Layer 2
    "ComplaintEdge",
    "ComplaintGraph",
    "ComplaintNode",
    "NodeType",
    "RelationType",
    # Layer 3
    "InsightCluster",
    # Layer 4
    "AuditVerdict",
    "HeuristicViolation",
    # Layer 5
    "ReconciledVerdict",
    "SkillTension",
    # Layer 6
    "PriorityScore",
    # Layers 7–9
    "DesignDecision",
    "DesignPrinciple",
    "OptimizationIteration",
    # Layer 10
    "EvolutionEdge",
    "EvolutionKind",
    "EvolutionNode",
    "EvolutionRelation",
    # Orchestration
    "RunContext",
    # Validators
    "SchemaValidationError",
    "validate_complaint_graph_against_source",
]


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

# Bump ONLY on breaking changes (renamed field, removed field, type narrowed).
# Additive, nullable fields do not require a bump — but document them inline.
SCHEMA_VERSION: int = 1


# ---------------------------------------------------------------------------
# Base config
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Base for all schemas — rejects unknown fields.

    An unexpected key in a Claude response or a stale JSONL row
    should fail loudly at parse time, not silently round-trip through
    the pipeline. `extra="forbid"` is the cheapest way to enforce that.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


# ---------------------------------------------------------------------------
# §4.1 Raw corpus
# ---------------------------------------------------------------------------


class RawReview(_StrictModel):
    review_id: str = Field(..., min_length=1, description="sha1(source + author_hash + timestamp)")
    source: Literal["google_play", "app_store"]
    author_hash: str = Field(
        ...,
        min_length=1,
        description="Hashed identifier — raw author name/handle MUST NOT land here. See SECURITY.md V-04.",
    )
    timestamp_utc: datetime
    rating: int = Field(..., ge=1, le=5)
    text: str = Field(..., min_length=1)
    lang: str = Field(..., min_length=2, max_length=8)
    app_version: str | None = None


# ---------------------------------------------------------------------------
# §4.2 Layer 1 — classification
# ---------------------------------------------------------------------------


class ClassifiedReview(_StrictModel):
    review_id: str = Field(..., min_length=1, description="FK → RawReview.review_id")
    is_ux_relevant: bool
    classifier_confidence: float = Field(..., ge=0.0, le=1.0)
    rubric_tags: list[str] = Field(default_factory=list)
    classified_at: datetime


# ---------------------------------------------------------------------------
# §4.3 Layer 2 — structure of complaint
# ---------------------------------------------------------------------------


NodeType = Literal[
    "pain",
    "expectation",
    "triggered_element",
    "workaround",
    "lost_value",
]

RelationType = Literal[
    "triggers",
    "violates_expectation",
    "compensates_for",
    "correlates_with",
]


class ComplaintNode(_StrictModel):
    node_id: str = Field(..., min_length=1)
    node_type: NodeType
    verbatim_quote: str = Field(..., min_length=1)
    quote_start: int = Field(..., ge=0)
    quote_end: int = Field(..., ge=1)

    @model_validator(mode="after")
    def _offsets_well_ordered(self) -> ComplaintNode:
        if self.quote_end <= self.quote_start:
            raise ValueError(f"quote_end ({self.quote_end}) must be > quote_start ({self.quote_start})")
        if (self.quote_end - self.quote_start) != len(self.verbatim_quote):
            raise ValueError(
                "quote offsets must span exactly the verbatim_quote length "
                f"(offset span={self.quote_end - self.quote_start}, quote len={len(self.verbatim_quote)})"
            )
        return self


class ComplaintEdge(_StrictModel):
    src: str = Field(..., min_length=1)
    dst: str = Field(..., min_length=1)
    relation: RelationType

    @model_validator(mode="after")
    def _no_self_loops(self) -> ComplaintEdge:
        if self.src == self.dst:
            raise ValueError(f"self-loop on node {self.src!r} is not allowed")
        return self


class ComplaintGraph(_StrictModel):
    review_id: str = Field(..., min_length=1, description="FK → RawReview.review_id")
    nodes: list[ComplaintNode] = Field(..., min_length=3, max_length=7)
    edges: list[ComplaintEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def _edges_reference_nodes(self) -> ComplaintGraph:
        node_ids = {n.node_id for n in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("duplicate node_id in ComplaintGraph")
        for e in self.edges:
            if e.src not in node_ids:
                raise ValueError(f"edge.src {e.src!r} references unknown node")
            if e.dst not in node_ids:
                raise ValueError(f"edge.dst {e.dst!r} references unknown node")
        return self


# ---------------------------------------------------------------------------
# §4.4 Layer 3 — insight clusters
# ---------------------------------------------------------------------------


class InsightCluster(_StrictModel):
    """A single cluster of pain/expectation nodes produced by L3.

    Notes on semantics (not enforced by the schema, but load-bearing
    for downstream layers):

    - **Multi-membership is legal and intentional.** A given
      ``review_id`` may appear in the ``member_review_ids`` of multiple
      ``InsightCluster`` records — this happens whenever a review's
      pain and expectation nodes land in different clusters. L4/L5
      must treat reviews as potentially non-exclusive across clusters.

    - **Label lifecycle.** L3 writes placeholder labels prefixed with
      ``"UNLABELED:"`` (e.g. ``"UNLABELED:cluster_03"``). Human-readable
      labels are produced by the **L3b** layer (``l3b_label``, Claude-
      backed, own ``skill_hashes``) which emits
      ``l3b_labeled_clusters.jsonl`` rather than rewriting this
      artifact. See ``layers/l3_cluster.py`` module docstring
      §"Label lifecycle" for the full rationale.

    - **``centroid_vector_ref`` format.** The value is a pointer string
      of the form ``"<file>#<index>"`` where ``<file>`` is the
      basename of the sibling ``.npy`` (e.g. ``"l3_centroids.npy"``)
      and ``<index>`` is the row index within the stacked array
      (matching ``cluster_id``'s numeric tail). A reader resolves it as
      ``np.load(dir / file)[int(index)]``.
    """

    cluster_id: str = Field(..., min_length=1)
    label: str = Field(
        ...,
        min_length=1,
        description=(
            "Cluster label. L3 writes 'UNLABELED:cluster_NN' placeholders; "
            "a downstream labeling layer rewrites them via Claude."
        ),
    )
    member_review_ids: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Reviews whose pain/expectation nodes fell in this cluster. "
            "A review may appear in multiple clusters (multi-membership)."
        ),
    )
    centroid_vector_ref: str = Field(
        ...,
        min_length=1,
        description=(
            "Pointer of the form '<file>#<index>' into a sibling .npy "
            "array of stacked centroids (e.g. 'l3_centroids.npy#3')."
        ),
    )
    representative_quotes: list[str] = Field(..., min_length=1, max_length=5)
    ui_context: str | None = Field(
        default=None,
        description=(
            "Optional short description of the UI surface the cluster concerns "
            "(e.g. 'paywall modal after lesson 3', 'streak-recovery screen'). "
            "When present, L4 audit skills use it as primary UI scaffold "
            "alongside quotes; when absent, skills reason from quotes alone. "
            "Per concept §7, audit input is 'UI description + user voice' — "
            "this field makes that explicit while keeping L3b output back-"
            "compatible (absent field parses as None). Prompt builders MUST "
            "omit the UI-context tag entirely when this is None so existing "
            "replay-cache entries remain valid."
        ),
    )


# ---------------------------------------------------------------------------
# §4.5 Layer 4 — audit verdicts (uniform contract, P3)
# ---------------------------------------------------------------------------


class HeuristicViolation(_StrictModel):
    """One heuristic's finding. Severity anchors per ADR-008:
    0 = non-issue, 3 = cosmetic, 6 = material, 9 = critical.
    """

    heuristic: str = Field(..., min_length=1)
    violation: str = Field(..., min_length=1)
    severity: int = Field(..., ge=0, le=10)
    evidence_review_ids: list[str] = Field(default_factory=list)
    reasoning: str = Field(..., min_length=1)


class AuditVerdict(_StrictModel):
    verdict_id: str = Field(..., min_length=1)
    cluster_id: str = Field(..., min_length=1, description="FK → InsightCluster.cluster_id")
    skill_id: str = Field(..., min_length=1, description='e.g. "audit-usability-fundamentals"')
    relevant_heuristics: list[HeuristicViolation] = Field(default_factory=list)
    native_payload_ref: str | None = Field(
        default=None, description="Path to full skill narrative (e.g. Norman scorecard)"
    )
    produced_at: datetime
    claude_model: str = Field(..., min_length=1, description="Frozen per-run for reproducibility")
    skill_hash: str = Field(
        ..., min_length=64, max_length=64, description="sha256 of the skill directory at call time"
    )


# ---------------------------------------------------------------------------
# §4.6 Layer 5 — reconciled verdicts
# ---------------------------------------------------------------------------


class SkillTension(_StrictModel):
    """A cross-skill tension the SOT reconcile step discovered.

    Represented as a first-class model rather than `dict[str, Any]`
    so the L5 contract is auditable and dashboard-renderable.
    """

    skill_a: str = Field(..., min_length=1)
    skill_b: str = Field(..., min_length=1)
    axis: str = Field(..., min_length=1, description='e.g. "user_control", "efficiency_vs_safety"')
    resolution: str = Field(..., min_length=1)


class ReconciledVerdict(_StrictModel):
    cluster_id: str = Field(..., min_length=1)
    ranked_violations: list[HeuristicViolation] = Field(default_factory=list)
    tensions: list[SkillTension] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# §4.7 Layer 6 — weighted priority
# ---------------------------------------------------------------------------


class PriorityScore(_StrictModel):
    cluster_id: str = Field(..., min_length=1)
    dimensions: dict[str, int] = Field(
        ..., min_length=5, max_length=5, description="Five priority dimensions, each 0–10"
    )
    meta_weights: dict[str, float] = Field(..., description="Editable via UI")
    weighted_total: float = Field(..., ge=0.0)
    validation_passes: int = Field(..., ge=2, le=3)
    validation_delta: float = Field(..., ge=0.0, description="Max diff between passes")

    @model_validator(mode="after")
    def _dimensions_in_range(self) -> PriorityScore:
        for k, v in self.dimensions.items():
            if not 0 <= v <= 10:
                raise ValueError(f"dimension {k!r}={v} out of [0, 10]")
        return self


# ---------------------------------------------------------------------------
# §4.8 Layers 7–9 — decisions, iterations
# ---------------------------------------------------------------------------


class DesignPrinciple(_StrictModel):
    principle_id: str = Field(..., min_length=1)
    cluster_id: str = Field(..., min_length=1, description="FK → InsightCluster")
    name: str = Field(..., min_length=1, description="Short memorable")
    statement: str = Field(..., min_length=1, description="Constraining, traceable, operational")
    derived_from_review_ids: list[str] = Field(..., min_length=1)


class DesignDecision(_StrictModel):
    decision_id: str = Field(..., min_length=1)
    principle_id: str = Field(..., min_length=1, description="FK → DesignPrinciple")
    description: str = Field(..., min_length=1)
    before_snapshot: str = Field(..., min_length=1)
    after_snapshot: str = Field(..., min_length=1)
    resolves_heuristics: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "REQUIRED non-empty — a design decision with no heuristic it resolves is not "
            "auditable back to a user complaint, which defeats the whole method."
        ),
    )


class OptimizationIteration(_StrictModel):
    iteration_id: str = Field(..., min_length=1)
    run_id: str = Field(..., min_length=1)
    iteration_index: int = Field(..., ge=0, description="0 = initial")
    parent_iteration_id: str | None = None
    design_artifact_ref: str = Field(..., min_length=1, description="Path under data/artifacts/iterations/")
    scores: dict[str, dict[str, int]] = Field(..., description="{skill_id: {heuristic: severity 0–10}}")
    reasoning: str = Field(..., min_length=1)
    accepted: bool
    regression_reason: str | None = None
    delta_per_heuristic: dict[str, int] = Field(default_factory=dict)
    informing_review_ids: list[str] = Field(default_factory=list)
    recorded_at: datetime

    @model_validator(mode="after")
    def _parent_consistent_with_index(self) -> OptimizationIteration:
        if self.iteration_index == 0 and self.parent_iteration_id is not None:
            raise ValueError("iteration_index=0 must have parent_iteration_id=None")
        if self.iteration_index > 0 and self.parent_iteration_id is None:
            raise ValueError("iteration_index>0 must have a parent_iteration_id")
        if not self.accepted and self.regression_reason is None:
            raise ValueError("rejected iterations must explain regression_reason")
        return self

    @model_validator(mode="after")
    def _scores_in_range(self) -> OptimizationIteration:
        for skill_id, hs in self.scores.items():
            for h, v in hs.items():
                if not 0 <= v <= 10:
                    raise ValueError(f"scores[{skill_id!r}][{h!r}]={v} out of [0, 10]")
        return self


# ---------------------------------------------------------------------------
# §4.9 Layer 10 — evolution graph
# ---------------------------------------------------------------------------


EvolutionKind = Literal[
    "review",
    "cluster",
    "verdict",
    "decision",
    "iteration",
    "element",
]

EvolutionRelation = Literal[
    "informs",
    "audited_by",
    "reconciled_into",
    "prioritized_as",
    "decided_as",
    "iterated_to",
    "produced_element",
    "dismissed_for",
]


class EvolutionNode(_StrictModel):
    node_id: str = Field(..., min_length=1)
    kind: EvolutionKind
    payload_ref: str = Field(..., min_length=1)


class EvolutionEdge(_StrictModel):
    src: str = Field(..., min_length=1)
    dst: str = Field(..., min_length=1)
    relation: EvolutionRelation

    @model_validator(mode="after")
    def _no_self_loops(self) -> EvolutionEdge:
        if self.src == self.dst:
            raise ValueError(f"self-loop on node {self.src!r} is not allowed")
        return self


# ---------------------------------------------------------------------------
# §5.4 Orchestration — RunContext
# ---------------------------------------------------------------------------


class RunContext(_StrictModel):
    """Serialised to data/derived/run_context/{run_id}.json.

    Every artifact references `run_id`; multiple runs coexist in the
    repo by construction (ADR-011 / §5.4 / §11 reviewer reproducibility).

    Frozen — the RunContext is a run's identity. Mutating it mid-run
    would silently decouple later artifacts from earlier ones.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(..., min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    seed: int = Field(..., ge=0, description="RNG seed (clustering determinism)")
    # NOTE: Pydantic reserves the attribute name `model_config` for its own
    # ConfigDict. Using `skill_model_config` avoids the collision and keeps
    # the intent ("which Claude model per skill").
    skill_model_config: dict[str, str] = Field(..., description="{skill_id: model_name}")
    meta_weights: dict[str, float] = Field(..., description="L6 knobs")
    optimization_budget: int = Field(default=8, ge=1, le=50)
    convergence_patience: int = Field(default=3, ge=1, le=20)
    quality_ceiling: int = Field(default=90, ge=0, le=100)
    active_skills: list[str] = Field(..., min_length=1, description="Enables 6→4 skill fallback")
    usd_ceiling: float = Field(
        default=15.0,
        ge=0.0,
        description="Per-run cost kill-switch ceiling — ADR-015, §5.5",
    )


# ---------------------------------------------------------------------------
# Cross-artifact validators
# ---------------------------------------------------------------------------


class SchemaValidationError(ValueError):
    """Raised by the standalone cross-artifact validators.

    Distinct from Pydantic's ValidationError so layer runners can
    handle hallucination-safeguard failures specifically (per P1).
    """


def validate_complaint_graph_against_source(
    graph: ComplaintGraph,
    *,
    source_text: str,
) -> None:
    """Enforce §4.3 P1: every node's verbatim_quote MUST be a substring
    of the source review at exactly [quote_start : quote_end].

    This can only be checked once we have both the graph and the review
    text, so it lives outside the model itself and is called at L2
    ingest time. On failure, the review is flagged for re-processing.

    Raises
    ------
    SchemaValidationError
        If any node's quote does not match its declared offsets in
        ``source_text``. The message identifies which node failed.
    """
    for node in graph.nodes:
        if node.quote_end > len(source_text):
            raise SchemaValidationError(
                f"node {node.node_id!r}: quote_end={node.quote_end} exceeds "
                f"source_text length {len(source_text)}"
            )
        actual = source_text[node.quote_start : node.quote_end]
        if actual != node.verbatim_quote:
            raise SchemaValidationError(
                f"node {node.node_id!r}: verbatim_quote does not match source "
                f"at [{node.quote_start}:{node.quote_end}]. "
                f"expected={node.verbatim_quote!r} actual={actual!r}"
            )
