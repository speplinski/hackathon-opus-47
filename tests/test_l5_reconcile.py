"""Tests for ``auditable_design.layers.l5_reconcile``.

Structure mirrors ``test_l4_audit_interaction_design.py`` where
shapes are shared (constants sanity, FakeClient, audit-batch plumbing)
and diverges for L5-specific concerns:

* **No per-finding structured fields.** L5 uses ``product_type`` /
  ``decision_mode`` style fields nowhere; instead the per-node
  discipline lives on the five SOT-derived node types (violation /
  corroboration / contradiction / tension / gap).
* **Cross-bundle validation.** Every ``violation`` node must reference
  a real L4 finding in the input bundle — ``source_skill`` +
  ``source_finding_idx`` + ``source_heuristic`` +
  ``source_severity_anchored`` all cross-checked. SKILL.md: L5 never
  introduces new heuristics.
* **Ranking formula arithmetic.** ``rank_score = severity ×
  corroboration_count``, ``corroboration_count == len(source_skills)``,
  ``unique_frames == count(distinct frames)``, sorted descending.
  Every constraint is parser-enforced and tested here.
* **Graph ↔ flat-list mirroring.** ``tensions[*]`` length matches
  tension-node count; ``gaps[*]`` entries content-mirror gap nodes
  (rationale, evidence_source, evidence_quote_idxs, why_missed).
* **Bidirectional evidence rule** on gap nodes only (violations inherit
  severity from L4 verdicts; gap is the only node type that carries
  its own evidence trail).
* **Soft-close tension axes** — unknown axes warn, do not reject.

Strategy
--------
In-process :class:`FakeClient` with scripted responses; no network, no
real replay log. The whole file runs in < 1 s.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from auditable_design.claude_client import ClaudeResponse
from auditable_design.layers import l5_reconcile as l5
from auditable_design.layers.l5_reconcile import (
    DEFAULT_CLUSTERS,
    DEFAULT_NATIVE,
    DEFAULT_VERDICTS,
    LAYER_NAME,
    MAX_TOKENS,
    MODEL,
    SKILL_ID,
    SKILL_TO_FRAME,
    SYSTEM_PROMPT,
    TEMPERATURE,
    VALID_L4_SKILLS,
    VALID_NODE_TYPES,
    VALID_RELATION_TYPES,
    VALID_TENSION_AXES,
    ReconcileOutcome,
    ReconcileParseError,
    build_provenance,
    build_user_message,
    load_verdicts_bundle,
    main,
    parse_reconcile_response,
    reconcile_batch,
    reconcile_cluster,
    skill_hash,
)
from auditable_design.schemas import (
    AuditVerdict,
    HeuristicViolation,
    InsightCluster,
    ReconciledVerdict,
    SkillTension,
)


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class FakeClient:
    """In-memory stand-in for claude_client.Client, mirroring the L4
    tests' FakeClient. First substring hit in ``user`` wins when
    scripting responses; no substring hit → default_response.
    """

    scripted: dict[str, str] = field(default_factory=dict)
    default_response: str | None = None
    raise_on: dict[str, Exception] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    cumulative_usd: float = 0.0
    cache_size: int = 0
    mode: str = "fake"

    async def call(
        self,
        *,
        system: str,
        user: str,
        model: str,
        skill_id: str,
        skill_hash: str,
        temperature: float = 0.0,
        max_tokens: int = 2048,
    ) -> ClaudeResponse:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "model": model,
                "skill_id": skill_id,
                "skill_hash": skill_hash,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )
        for key, exc in self.raise_on.items():
            if key in user:
                raise exc
        response_text = self.default_response
        for key, text in self.scripted.items():
            if key in user:
                response_text = text
                break
        if response_text is None:
            raise RuntimeError(
                f"FakeClient: no scripted response for user={user[:80]!r}..."
            )
        return ClaudeResponse(
            call_id="fake-call",
            key_hash="0" * 64,
            skill_id=skill_id,
            skill_hash=skill_hash,
            model=model,
            temperature=float(temperature),
            prompt=f"SYSTEM:\t{system}\tUSER:\t{user}",
            response=response_text,
            input_tokens=400,
            output_tokens=200,
            cost_usd=0.0,
            timestamp="2026-04-23T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _cluster(
    *,
    cluster_id: str = "cluster_02",
    label: str = (
        "Streak loss framing pressures users into mid-session purchase"
    ),
    quotes: list[str] | None = None,
    ui_context: str | None = None,
    html: str | None = None,
    screenshot_ref: str | None = None,
) -> InsightCluster:
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=["r1", "r2", "r3"],
        centroid_vector_ref="l3_centroids.npy#0",
        representative_quotes=quotes
        or [
            "streak saver popup is outright manipulative",
            "I'm trying to keep my 800+ day streak",
            "forced to pay or watch ads mid-lesson",
            "cannot concentrate on the lesson",
            "I clicked wrong button and three ads played",
        ],
        ui_context=ui_context,
        html=html,
        screenshot_ref=screenshot_ref,
    )


def _hv(
    *,
    heuristic: str = "some_heuristic",
    violation: str = "some violation description",
    severity: int = 7,
    reasoning: str = "encoded skill-specific context here",
) -> HeuristicViolation:
    """Build a HeuristicViolation — the unit of L4 output consumed by L5."""
    return HeuristicViolation(
        heuristic=heuristic,
        violation=violation,
        severity=severity,
        evidence_review_ids=[],
        reasoning=reasoning,
    )


def _verdict(
    *,
    cluster_id: str = "cluster_02",
    skill_id: str = "audit-interaction-design",
    relevant_heuristics: list[HeuristicViolation] | None = None,
    claude_model: str = "claude-sonnet-4-6",
) -> AuditVerdict:
    """Build one L4 AuditVerdict ready for bundle ingest."""
    return AuditVerdict(
        verdict_id=f"{skill_id}__{cluster_id}",
        cluster_id=cluster_id,
        skill_id=skill_id,
        relevant_heuristics=relevant_heuristics
        or [
            _hv(heuristic="posture_drift_within_product", severity=9),
            _hv(heuristic="modal_excise", severity=7),
        ],
        native_payload_ref=f"{skill_id}_verdicts.native.jsonl#{skill_id}__{cluster_id}",
        produced_at=datetime.now(UTC),
        claude_model=claude_model,
        skill_hash="a" * 64,
    )


def _six_verdict_bundle(cluster_id: str = "cluster_02") -> dict[str, AuditVerdict]:
    """Build a full six-skill bundle with plausible findings per skill.

    Used as the default input for most tests; individual tests override
    specific skills to hit the parser edges.
    """
    return {
        "audit-interaction-design": _verdict(
            cluster_id=cluster_id,
            skill_id="audit-interaction-design",
            relevant_heuristics=[
                _hv(heuristic="posture_drift_within_product", severity=9),
                _hv(heuristic="modal_excise", severity=7),
            ],
        ),
        "audit-ux-architecture": _verdict(
            cluster_id=cluster_id,
            skill_id="audit-ux-architecture",
            relevant_heuristics=[
                _hv(heuristic="skeleton_does_not_honour_priority", severity=9),
                _hv(heuristic="strategy_contradicts_itself", severity=7),
            ],
        ),
        "audit-decision-psychology": _verdict(
            cluster_id=cluster_id,
            skill_id="audit-decision-psychology",
            relevant_heuristics=[
                _hv(heuristic="loss_framing_on_streak", severity=9),
                _hv(heuristic="asymmetric_visual_weight", severity=7),
            ],
        ),
        "audit-business-alignment": _verdict(
            cluster_id=cluster_id,
            skill_id="audit-business-alignment",
            relevant_heuristics=[
                _hv(heuristic="vp_r$_tension", severity=7),
            ],
        ),
        "audit-usability-fundamentals": _verdict(
            cluster_id=cluster_id,
            skill_id="audit-usability-fundamentals",
            relevant_heuristics=[
                _hv(heuristic="gulf_of_execution", severity=7),
            ],
        ),
        "audit-accessibility": _verdict(
            cluster_id=cluster_id,
            skill_id="audit-accessibility",
            relevant_heuristics=[
                _hv(heuristic="target_size_minimum", severity=5),
            ],
        ),
    }


def _bundle(
    cluster_id: str = "cluster_02",
    verdicts: dict[str, AuditVerdict] | None = None,
) -> l5._ClusterBundle:
    return l5._ClusterBundle(
        cluster_id=cluster_id,
        verdicts_by_skill=verdicts if verdicts is not None else _six_verdict_bundle(cluster_id),
    )


def _violation_node(
    *,
    node_id: str = "v1",
    source_skill: str = "audit-interaction-design",
    source_heuristic: str = "posture_drift_within_product",
    source_severity_anchored: int = 9,
    source_finding_idx: int = 0,
    label: str = "posture_drift_within_product (sev 9)",
    rationale: str = "Learning surface drifts to promo posture mid-lesson.",
    confidence: float = 1.0,
) -> dict[str, Any]:
    """One violation node dict shaped for parse_reconcile_response."""
    return {
        "id": node_id,
        "type": "violation",
        "label": label,
        "rationale": rationale,
        "confidence": confidence,
        "source_skill": source_skill,
        "source_heuristic": source_heuristic,
        "source_severity_anchored": source_severity_anchored,
        "source_finding_idx": source_finding_idx,
        "member_ids": [],
        "skill_a": None,
        "skill_b": None,
        "axis": None,
        "resolution": None,
        "evidence_source": [],
        "evidence_quote_idxs": [],
        "why_missed": None,
    }


def _corroboration_node(
    *,
    node_id: str = "c1",
    member_ids: list[str] | None = None,
    label: str = "Modal as structural posture failure",
    rationale: str = "Two skills describe the same modal disruption.",
    confidence: float = 0.9,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "corroboration",
        "label": label,
        "rationale": rationale,
        "confidence": confidence,
        "source_skill": None,
        "source_heuristic": None,
        "source_severity_anchored": None,
        "source_finding_idx": None,
        "member_ids": member_ids or ["v1", "v2"],
        "skill_a": None,
        "skill_b": None,
        "axis": None,
        "resolution": None,
        "evidence_source": [],
        "evidence_quote_idxs": [],
        "why_missed": None,
    }


def _tension_node(
    *,
    node_id: str = "t1",
    skill_a: str = "audit-interaction-design",
    skill_b: str = "audit-decision-psychology",
    axis: str = "efficiency_vs_safety",
    resolution: str = "Cooper's remove-modal governs reversible; Kahneman's retain-confirm governs irreversible.",
    label: str = "Remove modal vs retain confirm on irreversible",
    rationale: str = "Cooper argues remove; Kahneman would retain.",
    confidence: float = 0.9,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "tension",
        "label": label,
        "rationale": rationale,
        "confidence": confidence,
        "source_skill": None,
        "source_heuristic": None,
        "source_severity_anchored": None,
        "source_finding_idx": None,
        "member_ids": [],
        "skill_a": skill_a,
        "skill_b": skill_b,
        "axis": axis,
        "resolution": resolution,
        "evidence_source": [],
        "evidence_quote_idxs": [],
        "why_missed": None,
    }


def _contradiction_node(
    *,
    node_id: str = "x1",
    skill_a: str = "audit-interaction-design",
    skill_b: str = "audit-ux-architecture",
    resolution: str = "evidence supports skill_a",
    label: str = "Modal presence disputed",
    rationale: str = "Skill_a claims modal present; skill_b claims absent.",
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "contradiction",
        "label": label,
        "rationale": rationale,
        "confidence": confidence,
        "source_skill": None,
        "source_heuristic": None,
        "source_severity_anchored": None,
        "source_finding_idx": None,
        "member_ids": [],
        "skill_a": skill_a,
        "skill_b": skill_b,
        "axis": None,
        "resolution": resolution,
        "evidence_source": [],
        "evidence_quote_idxs": [],
        "why_missed": None,
    }


def _gap_node(
    *,
    node_id: str = "g1",
    rationale: str = "Localisation quality not assessed by any L4 skill.",
    evidence_source: list[str] | None = None,
    evidence_quote_idxs: list[int] | None = None,
    why_missed: str = "Localisation falls between the six skill scopes.",
    label: str = "Localisation quality",
    confidence: float = 0.7,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": "gap",
        "label": label,
        "rationale": rationale,
        "confidence": confidence,
        "source_skill": None,
        "source_heuristic": None,
        "source_severity_anchored": None,
        "source_finding_idx": None,
        "member_ids": [],
        "skill_a": None,
        "skill_b": None,
        "axis": None,
        "resolution": None,
        "evidence_source": evidence_source
        if evidence_source is not None
        else ["ui_context"],
        "evidence_quote_idxs": evidence_quote_idxs
        if evidence_quote_idxs is not None
        else [],
        "why_missed": why_missed,
    }


def _ranked_entry(
    *,
    heuristic: str = "posture_drift_within_product",
    violation: str = "Mid-lesson modal breaks sovereign posture.",
    severity: int = 9,
    source_skills: list[str] | None = None,
    corroboration_count: int | None = None,
    unique_frames: int | None = None,
    rank_score: int | None = None,
    rationale: str = "Two-skill convergence at sev-9 on same modal disruption.",
) -> dict[str, Any]:
    """Build a ranked_violations entry with formula-consistent defaults.

    If ``corroboration_count`` / ``unique_frames`` / ``rank_score`` are
    not supplied, they are derived from ``source_skills`` and
    ``severity`` to satisfy the parser.
    """
    sks = source_skills or ["audit-interaction-design", "audit-ux-architecture"]
    cc = corroboration_count if corroboration_count is not None else len(sks)
    uf = (
        unique_frames
        if unique_frames is not None
        else len({SKILL_TO_FRAME[s] for s in sks})
    )
    rs = rank_score if rank_score is not None else severity * cc
    return {
        "heuristic": heuristic,
        "violation": violation,
        "severity": severity,
        "source_skills": sks,
        "corroboration_count": cc,
        "unique_frames": uf,
        "rank_score": rs,
        "rationale": rationale,
    }


def _tension_entry(
    *,
    skill_a: str = "audit-interaction-design",
    skill_b: str = "audit-decision-psychology",
    axis: str = "efficiency_vs_safety",
    resolution: str = "Cooper's remove-modal governs reversible; Kahneman's retain-confirm governs irreversible.",
) -> dict[str, Any]:
    return {
        "skill_a": skill_a,
        "skill_b": skill_b,
        "axis": axis,
        "resolution": resolution,
    }


def _gap_entry(
    *,
    rationale: str = "Localisation quality not assessed by any L4 skill.",
    evidence_source: list[str] | None = None,
    evidence_quote_idxs: list[int] | None = None,
    why_missed: str = "Localisation falls between the six skill scopes.",
) -> dict[str, Any]:
    return {
        "rationale": rationale,
        "evidence_source": evidence_source
        if evidence_source is not None
        else ["ui_context"],
        "evidence_quote_idxs": evidence_quote_idxs
        if evidence_quote_idxs is not None
        else [],
        "why_missed": why_missed,
    }


def _happy_payload(
    *,
    summary: str = (
        "Two skills corroborate posture/skeleton override; one tension "
        "between Cooper and Kahneman on the modal-retention axis."
    ),
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structurally valid reconcile payload — graph-primary v2.0 shape.

    Output contract is `{summary, graph}` only. The parser derives
    `ranked_violations`, `tensions`, `gaps` from the graph; this
    helper is used for model-emitted payloads (what the SKILL.md
    contract requires the model to write).

    Default graph: two violations (v1 = Cooper posture_drift, v2 =
    Garrett skeleton_priority) + one corroboration (c1 of v1, v2).
    The parser will derive one ranked entry from the corroboration.
    """
    if nodes is None:
        nodes = [
            _violation_node(
                node_id="v1",
                source_skill="audit-interaction-design",
                source_heuristic="posture_drift_within_product",
                source_severity_anchored=9,
                source_finding_idx=0,
            ),
            _violation_node(
                node_id="v2",
                source_skill="audit-ux-architecture",
                source_heuristic="skeleton_does_not_honour_priority",
                source_severity_anchored=9,
                source_finding_idx=0,
            ),
            _corroboration_node(
                node_id="c1",
                member_ids=["v1", "v2"],
            ),
        ]
    if edges is None:
        edges = [
            {"source": "c1", "target": "v1", "type": "corroborates"},
            {"source": "c1", "target": "v2", "type": "corroborates"},
        ]
    return {
        "summary": summary,
        "graph": {"nodes": nodes, "edges": edges},
    }


def _happy_payload_legacy(
    *,
    nodes: list[dict[str, Any]] | None = None,
    edges: list[dict[str, Any]] | None = None,
    ranked_violations: list[dict[str, Any]] | None = None,
    tensions: list[dict[str, Any]] | None = None,
    gaps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """SKILL.md v1.x payload shape — includes top-level `ranked_violations`,
    `tensions`, `gaps`.

    Used only to verify parser's legacy tolerance (silently drops these
    top-level keys and derives fresh lists from graph). SKILL.md v2.0
    requires the model to emit graph-only; v1.x raw responses are
    still parseable by the v2.0 parser for backwards compatibility.
    """
    p = _happy_payload(nodes=nodes, edges=edges)
    p["ranked_violations"] = (
        ranked_violations
        if ranked_violations is not None
        else [
            _ranked_entry(
                heuristic="posture_drift__skeleton_override",
                source_skills=[
                    "audit-interaction-design",
                    "audit-ux-architecture",
                ],
                severity=9,
            ),
        ]
    )
    p["tensions"] = tensions if tensions is not None else []
    p["gaps"] = gaps if gaps is not None else []
    return p


def _happy_response_text(payload: dict[str, Any] | None = None) -> str:
    return json.dumps(payload or _happy_payload())


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            for r in rows
        )
        + ("\n" if rows else "")
    )


# =============================================================================
# Constants — sanity
# =============================================================================


class TestConstants:
    def test_skill_id(self) -> None:
        assert SKILL_ID == "sot-reconcile"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l5_reconcile"

    def test_default_model_is_opus_47(self) -> None:
        # ADR-009: L5 reasoning-heavy low-volume → Opus 4.7.
        assert MODEL == "claude-opus-4-7"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_bumped_for_dense_output(self) -> None:
        # L5 emits graph + ranked + tensions + gaps; 8192 leaves
        # headroom for a reasoning preamble.
        assert 6144 < MAX_TOKENS <= 16384

    def test_valid_l4_skills_closed_set_six(self) -> None:
        assert VALID_L4_SKILLS == frozenset(
            {
                "audit-usability-fundamentals",
                "audit-accessibility",
                "audit-decision-psychology",
                "audit-business-alignment",
                "audit-interaction-design",
                "audit-ux-architecture",
            }
        )

    def test_skill_to_frame_covers_all_six_skills(self) -> None:
        assert set(SKILL_TO_FRAME) == VALID_L4_SKILLS

    def test_skill_to_frame_values_are_six_distinct(self) -> None:
        # Each skill maps to a unique frame; the mapping is bijective.
        assert len(set(SKILL_TO_FRAME.values())) == 6

    def test_valid_node_types_closed_set_five(self) -> None:
        assert VALID_NODE_TYPES == frozenset(
            {"violation", "corroboration", "contradiction", "tension", "gap"}
        )

    def test_valid_relation_types_closed_set_four(self) -> None:
        assert VALID_RELATION_TYPES == frozenset(
            {"corroborates", "contradicts", "in_tension_with", "elaborates"}
        )

    def test_valid_tension_axes_closed_set_seven(self) -> None:
        assert len(VALID_TENSION_AXES) == 7
        # Spot check the canonical ones present in SKILL.md.
        for expected in (
            "efficiency_vs_safety",
            "conversion_vs_user_wellbeing",
            "user_control_vs_platform_norms",
        ):
            assert expected in VALID_TENSION_AXES

    def test_default_paths_under_data_derived(self) -> None:
        assert DEFAULT_CLUSTERS == Path(
            "data/derived/l3b_labeled_clusters.jsonl"
        )
        assert DEFAULT_VERDICTS == Path(
            "data/derived/l5_reconciled_verdicts.jsonl"
        )
        assert DEFAULT_NATIVE == Path(
            "data/derived/l5_reconciled_verdicts.native.jsonl"
        )

    def test_skill_hash_independent_of_l4_siblings(self) -> None:
        from auditable_design.layers import (
            l4_audit,
            l4_audit_accessibility,
            l4_audit_business_alignment,
            l4_audit_decision_psychology,
            l4_audit_interaction_design,
            l4_audit_ux_architecture,
        )

        assert skill_hash() != l4_audit.skill_hash()
        assert skill_hash() != l4_audit_accessibility.skill_hash()
        assert skill_hash() != l4_audit_decision_psychology.skill_hash()
        assert skill_hash() != l4_audit_business_alignment.skill_hash()
        assert skill_hash() != l4_audit_interaction_design.skill_hash()
        assert skill_hash() != l4_audit_ux_architecture.skill_hash()


# =============================================================================
# skill_hash
# =============================================================================


class TestSkillHash:
    def test_returns_64_char_hex(self) -> None:
        h = skill_hash()
        assert len(h) == 64
        int(h, 16)

    def test_is_sha256_of_system_prompt(self) -> None:
        expected = hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()
        assert skill_hash() == expected

    def test_stable_across_calls(self) -> None:
        assert skill_hash() == skill_hash()


# =============================================================================
# load_verdicts_bundle
# =============================================================================


class TestLoadVerdictsBundle:
    def test_groups_by_cluster_id(self, tmp_path: Path) -> None:
        path = tmp_path / "bundle.jsonl"
        rows: list[dict[str, Any]] = []
        for cid in ("c01", "c02"):
            for sk in sorted(VALID_L4_SKILLS):
                rows.append(
                    _verdict(cluster_id=cid, skill_id=sk).model_dump(mode="json")
                )
        _write_jsonl(path, rows)

        bundles = load_verdicts_bundle(path)
        assert set(bundles) == {"c01", "c02"}
        for cid in ("c01", "c02"):
            assert set(bundles[cid].verdicts_by_skill) == VALID_L4_SKILLS

    def test_partial_bundle_permitted(self, tmp_path: Path) -> None:
        """A cluster with fewer than six verdicts still loads — the
        loader does not enforce "all six"; that is SKILL.md's concern.
        """
        path = tmp_path / "partial.jsonl"
        _write_jsonl(
            path,
            [
                _verdict(
                    cluster_id="c1", skill_id="audit-interaction-design"
                ).model_dump(mode="json"),
                _verdict(
                    cluster_id="c1", skill_id="audit-ux-architecture"
                ).model_dump(mode="json"),
            ],
        )
        bundles = load_verdicts_bundle(path)
        assert set(bundles["c1"].verdicts_by_skill) == {
            "audit-interaction-design",
            "audit-ux-architecture",
        }

    def test_duplicate_cluster_skill_last_wins(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "dup.jsonl"
        hv_first = _hv(heuristic="first_finding", severity=5)
        hv_second = _hv(heuristic="second_finding", severity=9)
        _write_jsonl(
            path,
            [
                _verdict(
                    cluster_id="c1",
                    skill_id="audit-interaction-design",
                    relevant_heuristics=[hv_first],
                ).model_dump(mode="json"),
                _verdict(
                    cluster_id="c1",
                    skill_id="audit-interaction-design",
                    relevant_heuristics=[hv_second],
                ).model_dump(mode="json"),
            ],
        )
        with caplog.at_level("WARNING"):
            bundles = load_verdicts_bundle(path)
        verdict = bundles["c1"].verdicts_by_skill["audit-interaction-design"]
        assert verdict.relevant_heuristics[0].heuristic == "second_finding"
        assert any("duplicate" in rec.message for rec in caplog.records)

    def test_unknown_skill_id_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "unknown.jsonl"
        bad = _verdict().model_dump(mode="json")
        bad["skill_id"] = "audit-tarot-reading"
        _write_jsonl(path, [bad])
        with pytest.raises(RuntimeError, match="unknown skill_id"):
            load_verdicts_bundle(path)

    def test_malformed_row_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        # Missing required fields (cluster_id, verdict_id, etc.).
        path.write_text('{"skill_id": "audit-interaction-design"}\n')
        with pytest.raises(RuntimeError, match="not a valid AuditVerdict"):
            load_verdicts_bundle(path)


# =============================================================================
# build_user_message
# =============================================================================


class TestBuildUserMessage:
    def test_minimal_cluster_contains_label_and_verdicts_block(self) -> None:
        c = _cluster(quotes=["q0", "q1"])
        b = _bundle(
            cluster_id=c.cluster_id,
            verdicts={
                "audit-interaction-design": _verdict(
                    cluster_id=c.cluster_id,
                    skill_id="audit-interaction-design",
                ),
            },
        )
        msg = build_user_message(c, b)
        assert "<cluster>" in msg and "</cluster>" in msg
        assert "<verdicts>" in msg and "</verdicts>" in msg
        assert f"<cluster_id>{c.cluster_id}</cluster_id>" in msg
        assert f"<label>{c.label}</label>" in msg
        assert '<q idx="0">q0</q>' in msg
        assert '<q idx="1">q1</q>' in msg
        assert '<verdict skill="audit-interaction-design">' in msg

    def test_ui_context_html_screenshot_only_when_present(self) -> None:
        c = _cluster(
            quotes=["x"],
            ui_context="Duolingo paywall",
            html="<div>m</div>",
            screenshot_ref="p.png",
        )
        msg = build_user_message(c, _bundle(cluster_id=c.cluster_id))
        assert "<ui_context>Duolingo paywall</ui_context>" in msg
        assert "<html><![CDATA[\n<div>m</div>\n]]></html>" in msg
        assert "<screenshot_ref>p.png</screenshot_ref>" in msg

    def test_optional_tags_absent_when_fields_none(self) -> None:
        c = _cluster(quotes=["x"])  # no ui_context/html/screenshot_ref
        msg = build_user_message(c, _bundle(cluster_id=c.cluster_id))
        assert "<ui_context>" not in msg
        assert "<html>" not in msg
        assert "<screenshot_ref>" not in msg

    def test_verdicts_rendered_in_canonical_tie_break_order(self) -> None:
        """SKILL.md canonical order:
        business_alignment, decision_psychology, accessibility,
        usability_fundamentals, interaction_design, ux_architecture.
        Deterministic for matching replay cache keys.
        """
        c = _cluster()
        msg = build_user_message(c, _bundle(cluster_id=c.cluster_id))
        order = [
            "audit-business-alignment",
            "audit-decision-psychology",
            "audit-accessibility",
            "audit-usability-fundamentals",
            "audit-interaction-design",
            "audit-ux-architecture",
        ]
        positions = [msg.index(f'<verdict skill="{s}">') for s in order]
        assert positions == sorted(positions), (
            f"verdict order drifted: {positions}"
        )

    def test_missing_skills_skipped_gracefully(self) -> None:
        c = _cluster()
        b = _bundle(
            cluster_id=c.cluster_id,
            verdicts={
                "audit-interaction-design": _verdict(
                    cluster_id=c.cluster_id, skill_id="audit-interaction-design"
                ),
                "audit-accessibility": _verdict(
                    cluster_id=c.cluster_id, skill_id="audit-accessibility"
                ),
            },
        )
        msg = build_user_message(c, b)
        assert '<verdict skill="audit-interaction-design">' in msg
        assert '<verdict skill="audit-accessibility">' in msg
        assert '<verdict skill="audit-ux-architecture">' not in msg
        assert '<verdict skill="audit-business-alignment">' not in msg

    def test_findings_carry_idx_heuristic_severity_and_violation(self) -> None:
        c = _cluster()
        v = _verdict(
            cluster_id=c.cluster_id,
            skill_id="audit-interaction-design",
            relevant_heuristics=[
                _hv(
                    heuristic="posture_drift_within_product",
                    violation="Learning surface drifts",
                    severity=9,
                    reasoning="[flow_excise] some reasoning",
                ),
            ],
        )
        b = _bundle(
            cluster_id=c.cluster_id,
            verdicts={"audit-interaction-design": v},
        )
        msg = build_user_message(c, b)
        assert '<finding idx="0"' in msg
        assert 'heuristic="posture_drift_within_product"' in msg
        assert 'severity="9"' in msg
        assert "Learning surface drifts" in msg
        assert "[reasoning: " in msg

    def test_special_characters_escaped_in_label_quotes_ui_context(self) -> None:
        c = _cluster(
            label="A & B <injected>",
            quotes=["hi <script>alert()</script> & more"],
            ui_context="caf\u00e9 & noise <issue>",
        )
        msg = build_user_message(c, _bundle(cluster_id=c.cluster_id))
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg
        assert "&amp;" in msg
        assert "&lt;injected&gt;" in msg
        assert "&lt;issue&gt;" in msg

    def test_html_not_escaped_because_cdata(self) -> None:
        raw = '<button onclick="x()">Submit & go</button>'
        c = _cluster(html=raw)
        msg = build_user_message(c, _bundle(cluster_id=c.cluster_id))
        assert raw in msg


# =============================================================================
# parse_reconcile_response — happy paths
# =============================================================================


class TestParseHappy:
    def test_minimal_happy_path(self) -> None:
        payload = parse_reconcile_response(
            _happy_response_text(),
            bundle=_bundle(),
            n_quotes=5,
        )
        assert set(payload) == {"summary", "graph", "ranked_violations", "tensions", "gaps"}
        assert len(payload["graph"]["nodes"]) == 3
        assert len(payload["graph"]["edges"]) == 2
        assert len(payload["ranked_violations"]) == 1

    def test_tolerates_leading_prose(self) -> None:
        text = "Thinking...\n\n" + _happy_response_text()
        parse_reconcile_response(text, bundle=_bundle(), n_quotes=5)

    def test_tolerates_code_fences(self) -> None:
        text = "```json\n" + _happy_response_text() + "\n```"
        parse_reconcile_response(text, bundle=_bundle(), n_quotes=5)

    def test_empty_lists_legal(self) -> None:
        """Empty ranked_violations + empty tensions + empty gaps is
        legal when the bundle had no L4 findings (rare in practice)."""
        payload = {
            "summary": "No findings to reconcile in this cluster.",
            "graph": {"nodes": [], "edges": []},
            "ranked_violations": [],
            "tensions": [],
            "gaps": [],
        }
        parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )

    def test_tension_and_gap_legal(self) -> None:
        payload = _happy_payload(
            nodes=[
                _violation_node(
                    node_id="v1",
                    source_skill="audit-interaction-design",
                    source_heuristic="posture_drift_within_product",
                    source_severity_anchored=9,
                    source_finding_idx=0,
                ),
                _violation_node(
                    node_id="v2",
                    source_skill="audit-decision-psychology",
                    source_heuristic="loss_framing_on_streak",
                    source_severity_anchored=9,
                    source_finding_idx=0,
                ),
                _tension_node(
                    node_id="t1",
                    skill_a="audit-interaction-design",
                    skill_b="audit-decision-psychology",
                ),
                _gap_node(node_id="g1"),
            ],
            edges=[
                {"source": "v1", "target": "v2", "type": "in_tension_with"},
                {"source": "v2", "target": "v1", "type": "in_tension_with"},
            ],
        )
        parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )


# =============================================================================
# parse_reconcile_response — structural failures
# =============================================================================


class TestParseStructural:
    def test_no_json(self) -> None:
        with pytest.raises(ReconcileParseError, match="no JSON object"):
            parse_reconcile_response(
                "sorry nothing here", bundle=_bundle(), n_quotes=5
            )

    def test_malformed_json(self) -> None:
        with pytest.raises(ReconcileParseError, match="no JSON object"):
            parse_reconcile_response(
                '{"summary": "x"', bundle=_bundle(), n_quotes=5
            )

    def test_missing_top_level_key(self) -> None:
        # v2.0 top-level is {summary, graph}. Removing `graph` should
        # fail; removing a legacy key that the parser tolerates would not.
        payload = _happy_payload()
        del payload["graph"]
        with pytest.raises(ReconcileParseError, match="missing required top-level"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_extra_top_level_key(self) -> None:
        payload = {**_happy_payload(), "extra": 1}
        with pytest.raises(ReconcileParseError, match="unexpected top-level"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_empty_summary(self) -> None:
        payload = _happy_payload(summary="   ")
        with pytest.raises(ReconcileParseError, match="summary.*non-empty"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )


# =============================================================================
# parse_reconcile_response — graph nodes
# =============================================================================


class TestParseNodes:
    def test_invalid_node_type(self) -> None:
        bad = _violation_node()
        bad["type"] = "hypothesis"
        payload = _happy_payload(nodes=[bad], edges=[])
        with pytest.raises(ReconcileParseError, match=r"type='hypothesis'"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_duplicate_node_id(self) -> None:
        v1 = _violation_node(node_id="v1", source_finding_idx=0)
        v1b = _violation_node(
            node_id="v1",  # same id
            source_skill="audit-ux-architecture",
            source_heuristic="skeleton_does_not_honour_priority",
            source_finding_idx=0,
        )
        payload = _happy_payload(
            nodes=[v1, v1b],
            edges=[],
        )
        with pytest.raises(ReconcileParseError, match="duplicate id"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_confidence_out_of_range(self) -> None:
        bad = _violation_node(confidence=1.5)
        payload = _happy_payload(nodes=[bad], edges=[])
        with pytest.raises(ReconcileParseError, match=r"confidence=1.5 out of"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_violation_source_skill_not_in_bundle(self) -> None:
        bad = _violation_node(
            source_skill="audit-accessibility",  # not in our partial bundle
            source_heuristic="target_size_minimum",
            source_severity_anchored=5,
            source_finding_idx=0,
        )
        partial = _bundle(
            verdicts={
                "audit-interaction-design": _verdict(
                    skill_id="audit-interaction-design"
                ),
            },
        )
        payload = _happy_payload(nodes=[bad], edges=[])
        with pytest.raises(
            ReconcileParseError, match="not present in input bundle"
        ):
            parse_reconcile_response(
                json.dumps(payload), bundle=partial, n_quotes=5
            )

    def test_violation_source_skill_not_in_valid_set(self) -> None:
        bad = _violation_node()
        bad["source_skill"] = "audit-vibes-check"
        payload = _happy_payload(nodes=[bad], edges=[])
        with pytest.raises(ReconcileParseError, match="not in VALID_L4_SKILLS"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_violation_finding_idx_out_of_range(self) -> None:
        bad = _violation_node(source_finding_idx=99)
        payload = _happy_payload(nodes=[bad], edges=[])
        with pytest.raises(ReconcileParseError, match="out of"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_violation_source_heuristic_mismatch(self) -> None:
        bad = _violation_node(
            source_finding_idx=0,
            source_heuristic="wrong_heuristic_name",  # doesn't match bundle
        )
        payload = _happy_payload(nodes=[bad], edges=[])
        with pytest.raises(
            ReconcileParseError, match="source_heuristic.*does not match"
        ):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_violation_source_severity_mismatch(self) -> None:
        bad = _violation_node(
            source_finding_idx=0,
            source_severity_anchored=5,  # bundle's finding 0 of this skill is sev 9
        )
        payload = _happy_payload(nodes=[bad], edges=[])
        with pytest.raises(
            ReconcileParseError, match="source_severity_anchored.*does not match"
        ):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_corroboration_too_few_members(self) -> None:
        v1 = _violation_node(node_id="v1", source_finding_idx=0)
        corr = _corroboration_node(node_id="c1", member_ids=["v1"])  # only 1
        payload = _happy_payload(
            nodes=[v1, corr],
            edges=[],
        )
        with pytest.raises(ReconcileParseError, match=r"≥ 2"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_corroboration_member_id_not_a_violation(self) -> None:
        v1 = _violation_node(node_id="v1", source_finding_idx=0)
        gap = _gap_node(node_id="g1")  # gap, not violation
        corr = _corroboration_node(node_id="c1", member_ids=["v1", "g1"])
        payload = _happy_payload(
            nodes=[v1, gap, corr],
            edges=[],
        )
        with pytest.raises(
            ReconcileParseError, match=r"not a violation-node id"
        ):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_tension_same_skill_rejected(self) -> None:
        v1 = _violation_node(node_id="v1", source_finding_idx=0)
        t = _tension_node(
            node_id="t1",
            skill_a="audit-interaction-design",
            skill_b="audit-interaction-design",
        )
        payload = _happy_payload(
            nodes=[v1, t],
            edges=[],
        )
        with pytest.raises(ReconcileParseError, match="must differ"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_tension_unknown_axis_passes_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Soft-close axis set — novel axis must pass but log WARNING."""
        v1 = _violation_node(node_id="v1", source_finding_idx=0)
        t = _tension_node(
            node_id="t1",
            axis="velocity_vs_craftsmanship",  # not in closed set
        )
        payload = _happy_payload(
            nodes=[v1, t],
            edges=[],
        )
        with caplog.at_level("WARNING"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )
        assert any(
            "not in the closed set" in rec.message for rec in caplog.records
        )

    def test_gap_requires_why_missed(self) -> None:
        g = _gap_node(why_missed="   ")
        payload = _happy_payload(
            nodes=[g],
            edges=[],
        )
        with pytest.raises(ReconcileParseError, match="why_missed.*non-empty"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_gap_bidirectional_forward(self) -> None:
        g = _gap_node(
            evidence_source=["quotes", "ui_context"],
            evidence_quote_idxs=[],  # 'quotes' present but idxs empty
        )
        payload = _happy_payload(
            nodes=[g],
            edges=[],
        )
        with pytest.raises(ReconcileParseError, match="bidirectional"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_gap_bidirectional_reverse(self) -> None:
        g = _gap_node(
            evidence_source=["ui_context"],
            evidence_quote_idxs=[0],  # idxs non-empty but 'quotes' absent
        )
        payload = _happy_payload(
            nodes=[g],
            edges=[],
        )
        with pytest.raises(ReconcileParseError, match="bidirectional"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )


# =============================================================================
# parse_reconcile_response — edges
# =============================================================================


class TestParseEdges:
    def test_edge_source_not_a_node(self) -> None:
        v1 = _violation_node(node_id="v1", source_finding_idx=0)
        payload = _happy_payload(
            nodes=[v1],
            edges=[{"source": "ghost", "target": "v1", "type": "corroborates"}],
        )
        with pytest.raises(ReconcileParseError, match="not a known node id"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )

    def test_edge_invalid_relation_type(self) -> None:
        v1 = _violation_node(node_id="v1", source_finding_idx=0)
        v2 = _violation_node(
            node_id="v2",
            source_skill="audit-ux-architecture",
            source_heuristic="skeleton_does_not_honour_priority",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        payload = _happy_payload(
            nodes=[v1, v2],
            edges=[{"source": "v1", "target": "v2", "type": "weakens"}],
        )
        with pytest.raises(ReconcileParseError, match="type='weakens'"):
            parse_reconcile_response(
                json.dumps(payload), bundle=_bundle(), n_quotes=5
            )


# =============================================================================
# parse_reconcile_response — flat-list derivation from graph
#
# SKILL.md v2.0 is graph-primary: model emits only `{summary, graph}`;
# parser derives `ranked_violations`, `tensions`, `gaps` by traversing
# the graph. The class below tests the derivation logic end-to-end
# through ``parse_reconcile_response``.
# =============================================================================


class TestDeriveFlatLists:
    def test_solitary_violation_becomes_ranked_entry(self) -> None:
        """A violation node not cited by any corroboration becomes a
        solitary ranked entry with corroboration_count = 1."""
        v1 = _violation_node(
            node_id="v1",
            source_skill="audit-interaction-design",
            source_heuristic="posture_drift_within_product",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        payload = _happy_payload(nodes=[v1], edges=[])
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )
        assert len(parsed["ranked_violations"]) == 1
        entry = parsed["ranked_violations"][0]
        assert entry["heuristic"] == "posture_drift_within_product"
        assert entry["severity"] == 9
        assert entry["source_skills"] == ["audit-interaction-design"]
        assert entry["corroboration_count"] == 1
        assert entry["unique_frames"] == 1
        assert entry["rank_score"] == 9

    def test_corroboration_collapses_members_to_one_entry(self) -> None:
        """Two violation nodes both cited by a corroboration node collapse
        into one ranked entry with source_skills = union of member skills
        (deduped) and severity = max."""
        v1 = _violation_node(
            node_id="v1",
            source_skill="audit-interaction-design",
            source_heuristic="posture_drift_within_product",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        v2 = _violation_node(
            node_id="v2",
            source_skill="audit-ux-architecture",
            source_heuristic="skeleton_does_not_honour_priority",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        c1 = _corroboration_node(
            node_id="c1", member_ids=["v1", "v2"]
        )
        payload = _happy_payload(
            nodes=[v1, v2, c1],
            edges=[
                {"source": "c1", "target": "v1", "type": "corroborates"},
                {"source": "c1", "target": "v2", "type": "corroborates"},
            ],
        )
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )
        # Members are consumed by the corroboration — the parser should
        # not emit them as separate solitary entries.
        assert len(parsed["ranked_violations"]) == 1
        entry = parsed["ranked_violations"][0]
        assert entry["severity"] == 9
        assert set(entry["source_skills"]) == {
            "audit-interaction-design",
            "audit-ux-architecture",
        }
        assert entry["corroboration_count"] == 2
        assert entry["unique_frames"] == 2  # interaction + architecture
        assert entry["rank_score"] == 18

    def test_mixed_corroborated_and_solitary(self) -> None:
        """One corroboration (v1+v2) + one solitary violation (v3) yields
        two ranked entries, ordered descending by rank_score."""
        v1 = _violation_node(
            node_id="v1",
            source_skill="audit-interaction-design",
            source_heuristic="posture_drift_within_product",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        v2 = _violation_node(
            node_id="v2",
            source_skill="audit-ux-architecture",
            source_heuristic="skeleton_does_not_honour_priority",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        v3 = _violation_node(
            node_id="v3",
            source_skill="audit-decision-psychology",
            source_heuristic="loss_framing_on_streak",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        c1 = _corroboration_node(node_id="c1", member_ids=["v1", "v2"])
        payload = _happy_payload(
            nodes=[v1, v2, v3, c1],
            edges=[
                {"source": "c1", "target": "v1", "type": "corroborates"},
                {"source": "c1", "target": "v2", "type": "corroborates"},
            ],
        )
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )
        assert len(parsed["ranked_violations"]) == 2
        # Sorted descending by rank_score.
        assert parsed["ranked_violations"][0]["rank_score"] == 18  # corr
        assert parsed["ranked_violations"][1]["rank_score"] == 9   # solitary

    def test_tension_node_becomes_tensions_entry(self) -> None:
        v1 = _violation_node(
            node_id="v1",
            source_skill="audit-interaction-design",
            source_heuristic="posture_drift_within_product",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        t1 = _tension_node(
            node_id="t1",
            skill_a="audit-interaction-design",
            skill_b="audit-decision-psychology",
            axis="efficiency_vs_safety",
            resolution="Cooper removes, Kahneman retains — depends on reversibility.",
        )
        payload = _happy_payload(nodes=[v1, t1], edges=[])
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )
        assert len(parsed["tensions"]) == 1
        t = parsed["tensions"][0]
        assert t["skill_a"] == "audit-interaction-design"
        assert t["skill_b"] == "audit-decision-psychology"
        assert t["axis"] == "efficiency_vs_safety"

    def test_gap_node_becomes_gaps_entry(self) -> None:
        v1 = _violation_node(
            node_id="v1",
            source_skill="audit-interaction-design",
            source_heuristic="posture_drift_within_product",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        g1 = _gap_node(
            node_id="g1",
            rationale="Localisation not audited.",
            evidence_source=["ui_context"],
            evidence_quote_idxs=[],
            why_missed="Falls between the six skill scopes.",
        )
        payload = _happy_payload(nodes=[v1, g1], edges=[])
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )
        assert len(parsed["gaps"]) == 1
        g = parsed["gaps"][0]
        assert g["rationale"] == "Localisation not audited."
        assert g["evidence_source"] == ["ui_context"]
        assert g["why_missed"] == "Falls between the six skill scopes."

    def test_legacy_top_level_flat_lists_silently_dropped(self) -> None:
        """A SKILL.md v1.x payload (with top-level ranked_violations /
        tensions / gaps emitted by the model) is accepted by the v2.0
        parser — the legacy keys are dropped and the parser derives
        fresh flat lists from the graph. Backwards compatibility."""
        payload = _happy_payload_legacy()
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )
        # Parser discards the legacy top-level ranked_violations and
        # derives its own. Since the default payload has c1 collapsing
        # v1+v2, the derived list has exactly one entry.
        assert len(parsed["ranked_violations"]) == 1
        assert parsed["ranked_violations"][0]["corroboration_count"] == 2

    def test_ranked_sorted_descending(self) -> None:
        """Derived ranked_violations must be sorted descending by
        rank_score, regardless of node-emission order in the graph."""
        # v1 (sev 9, Cooper) + v2 (sev 7, Kahneman) + v3 (sev 5, Osterwalder).
        # No corroborations → three solitary entries, rank_scores 9, 7, 5.
        v1 = _violation_node(
            node_id="v1",
            source_skill="audit-interaction-design",
            source_heuristic="posture_drift_within_product",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        v2 = _violation_node(
            node_id="v2",
            source_skill="audit-decision-psychology",
            source_heuristic="asymmetric_visual_weight",
            source_severity_anchored=7,
            source_finding_idx=0,
        )
        v3 = _violation_node(
            node_id="v3",
            source_skill="audit-business-alignment",
            source_heuristic="vp_r$_tension",
            source_severity_anchored=5,
            source_finding_idx=0,
        )
        # Emit in scrambled order to prove parser sorts.
        v3["source_severity_anchored"] = 5
        v3["source_heuristic"] = "vp_r$_tension"
        bundle = _bundle(
            verdicts={
                **_six_verdict_bundle("cluster_02"),
                "audit-business-alignment": _verdict(
                    cluster_id="cluster_02",
                    skill_id="audit-business-alignment",
                    relevant_heuristics=[
                        _hv(heuristic="vp_r$_tension", severity=5),
                    ],
                ),
                "audit-decision-psychology": _verdict(
                    cluster_id="cluster_02",
                    skill_id="audit-decision-psychology",
                    relevant_heuristics=[
                        _hv(heuristic="asymmetric_visual_weight", severity=7),
                    ],
                ),
            }
        )
        payload = _happy_payload(nodes=[v3, v1, v2], edges=[])  # scrambled
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=bundle, n_quotes=5
        )
        scores = [e["rank_score"] for e in parsed["ranked_violations"]]
        assert scores == [9, 7, 5]  # descending

    def test_severity_uses_max_across_corroboration_members(self) -> None:
        """A corroboration over sev-7 + sev-9 yields a ranked entry at sev-9."""
        v1 = _violation_node(
            node_id="v1",
            source_skill="audit-interaction-design",
            source_heuristic="modal_excise",
            source_severity_anchored=7,
            source_finding_idx=1,
        )
        v2 = _violation_node(
            node_id="v2",
            source_skill="audit-ux-architecture",
            source_heuristic="skeleton_does_not_honour_priority",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        c1 = _corroboration_node(node_id="c1", member_ids=["v1", "v2"])
        payload = _happy_payload(
            nodes=[v1, v2, c1],
            edges=[
                {"source": "c1", "target": "v1", "type": "corroborates"},
                {"source": "c1", "target": "v2", "type": "corroborates"},
            ],
        )
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )
        assert parsed["ranked_violations"][0]["severity"] == 9  # max

    def test_empty_graph_produces_empty_flat_lists(self) -> None:
        """A cluster with zero L4 findings reconciles to an empty graph
        and empty flat lists."""
        payload = _happy_payload(nodes=[], edges=[])
        parsed = parse_reconcile_response(
            json.dumps(payload), bundle=_bundle(), n_quotes=5
        )
        assert parsed["ranked_violations"] == []
        assert parsed["tensions"] == []
        assert parsed["gaps"] == []


# =============================================================================
# reconcile_cluster
# =============================================================================


class TestReconcileCluster:
    def test_happy_path_yields_audited_outcome(self) -> None:
        client = FakeClient(default_response=_happy_response_text())
        c = _cluster()
        outcome = asyncio.run(
            reconcile_cluster(
                c, _bundle(cluster_id=c.cluster_id), client, skill_hash_value=skill_hash()
            )
        )
        assert outcome.status == "audited"
        assert outcome.reason is None
        assert outcome.verdict.cluster_id == c.cluster_id
        assert len(outcome.verdict.ranked_violations) == 1

    def test_empty_bundle_yields_immediate_fallback_no_call(self) -> None:
        """An empty bundle means zero L4 verdicts for this cluster;
        reconcile returns a fallback without invoking Claude."""
        client = FakeClient()  # no scripted response — would fail if called
        c = _cluster()
        outcome = asyncio.run(
            reconcile_cluster(
                c,
                l5._ClusterBundle(c.cluster_id, {}),
                client,
                skill_hash_value=skill_hash(),
            )
        )
        assert outcome.status == "fallback"
        assert outcome.reason == "no L4 verdicts in input bundle"
        assert client.calls == []

    def test_parse_failure_yields_fallback(self) -> None:
        client = FakeClient(default_response="not a JSON object")
        outcome = asyncio.run(
            reconcile_cluster(
                _cluster(), _bundle(), client, skill_hash_value=skill_hash()
            )
        )
        assert outcome.status == "fallback"
        assert outcome.reason is not None
        assert outcome.verdict.ranked_violations == []

    def test_call_uses_layer_constants(self) -> None:
        client = FakeClient(default_response=_happy_response_text())
        asyncio.run(
            reconcile_cluster(
                _cluster(), _bundle(), client, skill_hash_value=skill_hash()
            )
        )
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["skill_id"] == SKILL_ID
        assert call["model"] == MODEL
        assert call["temperature"] == TEMPERATURE
        assert call["max_tokens"] == MAX_TOKENS
        assert call["system"] == SYSTEM_PROMPT

    def test_transport_failure_propagates(self) -> None:
        client = FakeClient(
            raise_on={"Streak loss framing": RuntimeError("replay miss")}
        )
        with pytest.raises(RuntimeError, match="replay miss"):
            asyncio.run(
                reconcile_cluster(
                    _cluster(), _bundle(), client, skill_hash_value=skill_hash()
                )
            )

    def test_ranked_violations_become_heuristic_violations(self) -> None:
        """_build_reconciled_verdict must encode cross-skill metadata
        (rank_score, skills, unique_frames) into reasoning — the
        consumer view."""
        client = FakeClient(default_response=_happy_response_text())
        outcome = asyncio.run(
            reconcile_cluster(
                _cluster(), _bundle(), client, skill_hash_value=skill_hash()
            )
        )
        hv = outcome.verdict.ranked_violations[0]
        assert hv.severity == 9  # passes through from L4
        assert "rank_score=18" in hv.reasoning
        assert "audit-interaction-design" in hv.reasoning
        assert "audit-ux-architecture" in hv.reasoning
        assert "unique_frames=2" in hv.reasoning


# =============================================================================
# reconcile_batch
# =============================================================================


class TestReconcileBatch:
    def test_processes_all_clusters(self) -> None:
        client = FakeClient(default_response=_happy_response_text())
        clusters = [_cluster(cluster_id=f"cluster_{i:02d}") for i in range(3)]
        bundles = {
            c.cluster_id: _bundle(cluster_id=c.cluster_id) for c in clusters
        }
        outcomes, failures = asyncio.run(
            reconcile_batch(clusters, bundles, client)
        )
        assert len(outcomes) == 3
        assert failures == []

    def test_missing_bundle_yields_fallback_not_error(self) -> None:
        client = FakeClient(default_response=_happy_response_text())
        clusters = [_cluster(cluster_id=f"cluster_{i:02d}") for i in range(2)]
        # Only first cluster has a bundle.
        bundles = {"cluster_00": _bundle(cluster_id="cluster_00")}
        outcomes, failures = asyncio.run(
            reconcile_batch(clusters, bundles, client)
        )
        assert failures == []
        assert len(outcomes) == 2
        statuses = {o.cluster_id: o.status for o in outcomes}
        assert statuses["cluster_00"] == "audited"
        assert statuses["cluster_01"] == "fallback"

    def test_parse_failure_is_fallback_not_transport_failure(self) -> None:
        client = FakeClient(default_response="garbage")
        clusters = [_cluster()]
        bundles = {clusters[0].cluster_id: _bundle(cluster_id=clusters[0].cluster_id)}
        outcomes, failures = asyncio.run(
            reconcile_batch(clusters, bundles, client)
        )
        assert failures == []
        assert outcomes[0].status == "fallback"


# =============================================================================
# build_provenance
# =============================================================================


class TestProvenance:
    def _outcome_audited(
        self, cluster_id: str, payload: dict[str, Any]
    ) -> ReconcileOutcome:
        return ReconcileOutcome(
            cluster_id=cluster_id,
            verdict=ReconciledVerdict(cluster_id=cluster_id),
            native_payload=payload,
            status="audited",
            reason=None,
        )

    def _outcome_fallback(self, cluster_id: str, reason: str) -> ReconcileOutcome:
        return ReconcileOutcome(
            cluster_id=cluster_id,
            verdict=ReconciledVerdict(cluster_id=cluster_id),
            native_payload={
                "fallback": True,
                "reason": reason,
                "raw_response": "",
            },
            status="fallback",
            reason=reason,
        )

    def test_counts_match(self) -> None:
        outcomes = [
            self._outcome_audited("c00", _happy_payload()),
            self._outcome_fallback("c01", "parse error"),
        ]
        prov = build_provenance(outcomes, failures=[], model=MODEL)
        assert prov["cluster_count"] == 2
        assert prov["audited_count"] == 1
        assert prov["fallback_count"] == 1

    def test_node_type_histogram_covers_all_five_keys(self) -> None:
        payload = _happy_payload()
        prov = build_provenance(
            [self._outcome_audited("c00", payload)],
            failures=[],
            model=MODEL,
        )
        hist = prov["node_type_histogram"]
        assert set(hist) == VALID_NODE_TYPES
        assert hist["violation"] == 2
        assert hist["corroboration"] == 1
        assert hist["tension"] == 0
        assert hist["gap"] == 0
        assert hist["contradiction"] == 0

    def test_relation_type_histogram_covers_all_four_keys(self) -> None:
        payload = _happy_payload()
        prov = build_provenance(
            [self._outcome_audited("c00", payload)],
            failures=[],
            model=MODEL,
        )
        hist = prov["relation_type_histogram"]
        assert set(hist) == VALID_RELATION_TYPES
        assert hist["corroborates"] == 2
        for k in ("contradicts", "in_tension_with", "elaborates"):
            assert hist[k] == 0

    def test_tension_axis_histogram(self) -> None:
        # build_provenance reads payload['tensions'] — the parser-derived
        # flat list. In real flow the parser populates it from graph
        # tension nodes; we pass the payload through parse_reconcile_response
        # here so build_provenance gets the same shape it would in prod.
        v1 = _violation_node(node_id="v1", source_finding_idx=0)
        v2 = _violation_node(
            node_id="v2",
            source_skill="audit-decision-psychology",
            source_heuristic="loss_framing_on_streak",
            source_severity_anchored=9,
            source_finding_idx=0,
        )
        t = _tension_node(node_id="t1", axis="efficiency_vs_safety")
        raw_payload = _happy_payload(nodes=[v1, v2, t], edges=[])
        parsed = parse_reconcile_response(
            json.dumps(raw_payload), bundle=_bundle(), n_quotes=5
        )
        prov = build_provenance(
            [self._outcome_audited("c00", parsed)],
            failures=[],
            model=MODEL,
        )
        assert prov["tension_axis_histogram"] == {"efficiency_vs_safety": 1}

    def test_corroboration_count_histogram(self) -> None:
        """build_provenance consumes a post-parse payload where the
        parser has already derived ranked_violations. We construct that
        shape directly to exercise the provenance histograms."""
        payload = _happy_payload()  # {summary, graph}
        payload["graph"]["nodes"] = [
            _violation_node(
                node_id="v1",
                source_skill="audit-interaction-design",
                source_heuristic="posture_drift_within_product",
                source_severity_anchored=9,
                source_finding_idx=0,
            ),
            _violation_node(
                node_id="v2",
                source_skill="audit-ux-architecture",
                source_heuristic="skeleton_does_not_honour_priority",
                source_severity_anchored=9,
                source_finding_idx=0,
            ),
            _violation_node(
                node_id="v3",
                source_skill="audit-decision-psychology",
                source_heuristic="loss_framing_on_streak",
                source_severity_anchored=7,
                source_finding_idx=1,
            ),
            _violation_node(
                node_id="v4",
                source_skill="audit-business-alignment",
                source_heuristic="vp_r$_tension",
                source_severity_anchored=7,
                source_finding_idx=0,
            ),
            _corroboration_node(node_id="c1", member_ids=["v1", "v2"]),
        ]
        payload["graph"]["edges"] = [
            {"source": "c1", "target": "v1", "type": "corroborates"},
            {"source": "c1", "target": "v2", "type": "corroborates"},
        ]
        # Simulate parser-derived ranked_violations (what build_provenance
        # actually reads). In real flow this is produced by
        # _derive_flat_lists_from_graph; here we hand-build the equivalent.
        payload["ranked_violations"] = [
            _ranked_entry(
                source_skills=[
                    "audit-interaction-design",
                    "audit-ux-architecture",
                ],
                severity=9,
            ),
            _ranked_entry(
                heuristic="solitary_1",
                source_skills=["audit-decision-psychology"],
                severity=7,
            ),
            _ranked_entry(
                heuristic="solitary_2",
                source_skills=["audit-business-alignment"],
                severity=5,
            ),
        ]
        payload["tensions"] = []
        payload["gaps"] = []
        prov = build_provenance(
            [self._outcome_audited("c00", payload)],
            failures=[],
            model=MODEL,
        )
        hist = prov["corroboration_count_histogram"]
        assert hist["1"] == 2  # two solitary entries
        assert hist["2"] == 1  # one double-corroborated
        assert hist["3"] == 0

    def test_mean_top_rank_score(self) -> None:
        # Use parse_reconcile_response to get real post-parse payloads
        # (parser computes rank_score from graph). p1 has the default
        # corroboration (rank_score = 9 × 2 = 18), p2 has a single
        # solitary sev-7 violation (rank_score = 7 × 1 = 7).
        p1 = parse_reconcile_response(
            _happy_response_text(), bundle=_bundle(), n_quotes=5
        )
        v_sev7 = _violation_node(
            node_id="v1",
            source_skill="audit-interaction-design",
            source_heuristic="modal_excise",
            source_severity_anchored=7,
            source_finding_idx=1,
        )
        p2 = parse_reconcile_response(
            json.dumps(_happy_payload(nodes=[v_sev7], edges=[])),
            bundle=_bundle(),
            n_quotes=5,
        )
        prov = build_provenance(
            [
                self._outcome_audited("c00", p1),
                self._outcome_audited("c01", p2),
            ],
            failures=[],
            model=MODEL,
        )
        assert prov["mean_top_rank_score"] == (18 + 7) / 2

    def test_transport_failures_rendered(self) -> None:
        prov = build_provenance(
            outcomes=[],
            failures=[("c99", ValueError("oops"))],
            model=MODEL,
        )
        assert prov["transport_failure_count"] == 1
        assert prov["transport_failures"][0]["cluster_id"] == "c99"
        assert "ValueError" in prov["transport_failures"][0]["error"]


# =============================================================================
# CLI — main
# =============================================================================


class TestMain:
    def test_cli_end_to_end_with_fakeclient(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l5, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [_cluster(cluster_id="cluster_02").model_dump(mode="json")],
        )
        verdicts_path = data_dir / "verdicts.jsonl"
        bundle_rows = [
            v.model_dump(mode="json")
            for v in _six_verdict_bundle("cluster_02").values()
        ]
        _write_jsonl(verdicts_path, bundle_rows)

        output_path = data_dir / "reconciled.jsonl"
        native_path = data_dir / "reconciled.native.jsonl"

        fake = FakeClient(default_response=_happy_response_text())
        monkeypatch.setattr(l5, "Client", lambda **_k: fake)

        rc = main(
            [
                "--verdicts",
                str(verdicts_path),
                "--clusters",
                str(clusters_path),
                "--output",
                str(output_path),
                "--native-output",
                str(native_path),
                "--mode",
                "replay",
                "--run-id",
                "l5-test-run",
            ]
        )
        assert rc == 0
        assert output_path.exists()
        assert native_path.exists()

        verdicts_raw = [
            json.loads(line) for line in output_path.read_text().splitlines()
        ]
        assert len(verdicts_raw) == 1
        for row in verdicts_raw:
            ReconciledVerdict.model_validate(row)
            assert row["cluster_id"] == "cluster_02"

        native_raw = [
            json.loads(line) for line in native_path.read_text().splitlines()
        ]
        assert len(native_raw) == 1
        assert native_raw[0]["cluster_id"] == "cluster_02"
        assert native_raw[0]["status"] == "audited"

        prov_path = output_path.with_suffix(".provenance.json")
        assert prov_path.exists()
        prov = json.loads(prov_path.read_text())
        assert prov["cluster_count"] == 1
        assert prov["audited_count"] == 1
        assert prov["skill_id"] == SKILL_ID
        for k in (
            "node_type_histogram",
            "relation_type_histogram",
            "tension_axis_histogram",
            "corroboration_count_histogram",
            "mean_top_rank_score",
            "total_gaps",
            "clusters_with_no_tensions",
        ):
            assert k in prov

    def test_cli_empty_clusters_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l5, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        verdicts_path = data_dir / "verdicts.jsonl"
        verdicts_path.write_text("")

        monkeypatch.setattr(l5, "Client", lambda **_k: FakeClient())

        rc = main(
            [
                "--verdicts",
                str(verdicts_path),
                "--clusters",
                str(clusters_path),
                "--output",
                str(data_dir / "out.jsonl"),
                "--native-output",
                str(data_dir / "native.jsonl"),
                "--mode",
                "replay",
            ]
        )
        assert rc == 1
