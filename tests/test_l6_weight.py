"""Tests for ``auditable_design.layers.l6_weight``.

Structure mirrors ``test_l5_reconcile.py`` where shapes are shared
(constants sanity, FakeClient, batch plumbing) and diverges for L6-
specific concerns:

* **5 priority dimensions** — ``severity``, ``reach``, ``persistence``,
  ``business_impact``, ``cognitive_cost``; each an integer in [0, 10].
* **Double-pass with optional third pass.** Two Claude calls per
  cluster by default; a per-dim delta > 1 triggers a third call and
  a median aggregation. FakeClient supports a per-call response queue
  for this pattern.
* **Meta-weights layer-separated.** The model never sees weights;
  ``weighted_total`` is computed by the L6 module using
  :data:`DEFAULT_META_WEIGHTS` (symmetric 0.2 × 5).
* **Aggregation arithmetic tested directly.** ``_aggregate_passes``
  and ``_needs_third_pass`` have their own unit tests — the
  pass-count / median logic is the heart of the double-pass discipline.

Strategy
--------
In-process :class:`FakeClient` with scripted per-call response queue;
no network, no real replay log. The whole file runs in < 1 s.
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
from auditable_design.layers import l6_weight as l6
from auditable_design.layers.l6_weight import (
    DEFAULT_CLUSTERS,
    DEFAULT_META_WEIGHTS,
    DEFAULT_NATIVE,
    DEFAULT_RECONCILED,
    DEFAULT_VERDICTS,
    DIMENSION_KEYS,
    LAYER_NAME,
    MAX_DIMENSION_DELTA,
    MAX_TOKENS,
    MODEL,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    PriorityOutcome,
    PriorityParseError,
    _aggregate_passes,
    _needs_third_pass,
    build_provenance,
    build_user_message,
    load_reconciled_verdicts,
    main,
    parse_priority_response,
    score_batch,
    score_cluster,
    skill_hash,
    weighted_total,
)
from auditable_design.schemas import (
    HeuristicViolation,
    InsightCluster,
    PriorityScore,
    ReconciledVerdict,
    SkillTension,
)


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class FakeClient:
    """In-memory stand-in with per-call response queue.

    ``responses`` is consumed FIFO — each call pops the front. When
    empty, ``default_response`` is used if set, else RuntimeError.

    ``scripted`` remains for the substring-match pattern (same as L5
    FakeClient) — if a key matches a substring of ``user``, that key's
    response wins over the queue. Lets a test force a specific payload
    regardless of call order.
    """

    responses: list[str] = field(default_factory=list)
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
        # Substring-scripted wins over queue.
        for key, text in self.scripted.items():
            if key in user:
                response_text = text
                break
        else:
            if self.responses:
                response_text = self.responses.pop(0)
            elif self.default_response is not None:
                response_text = self.default_response
            else:
                raise RuntimeError(
                    f"FakeClient: empty queue and no default_response for "
                    f"user={user[:80]!r}..."
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
            input_tokens=200,
            output_tokens=80,
            cost_usd=0.0,
            timestamp="2026-04-23T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _cluster(
    *,
    cluster_id: str = "cluster_02",
    label: str = "Streak loss framing pressures users into mid-session purchase",
    quotes: list[str] | None = None,
    members: list[str] | None = None,
    ui_context: str | None = "Duolingo mid-lesson modal blocks next question.",
    html: str | None = None,
    screenshot_ref: str | None = None,
) -> InsightCluster:
    return InsightCluster(
        cluster_id=cluster_id,
        label=label,
        member_review_ids=members or [f"r{i}" for i in range(7)],
        centroid_vector_ref="l3_centroids.npy#0",
        representative_quotes=quotes
        or [
            "streak saver popup is outright manipulative",
            "I'm trying to keep my 800+ day streak",
            "forced to pay or watch ads mid-lesson",
        ],
        ui_context=ui_context,
        html=html,
        screenshot_ref=screenshot_ref,
    )


def _reconciled(
    *,
    cluster_id: str = "cluster_02",
    ranked: list[HeuristicViolation] | None = None,
    tensions: list[SkillTension] | None = None,
) -> ReconciledVerdict:
    return ReconciledVerdict(
        cluster_id=cluster_id,
        ranked_violations=ranked
        or [
            HeuristicViolation(
                heuristic="posture_drift__skeleton_override",
                violation="Mid-lesson modal breaks sovereign learning posture.",
                severity=9,
                evidence_review_ids=[],
                reasoning=(
                    "rank_score=18 (severity=9 × corroboration=2, "
                    "unique_frames=2) | skills=[audit-interaction-design, "
                    "audit-ux-architecture] | rationale: Two skills converge."
                ),
            ),
            HeuristicViolation(
                heuristic="loss_framing_on_streak",
                violation="Midnight-countdown loss framing on 800-day streak.",
                severity=9,
                evidence_review_ids=[],
                reasoning=(
                    "rank_score=9 (severity=9 × corroboration=1, "
                    "unique_frames=1) | skills=[audit-decision-psychology]"
                ),
            ),
        ],
        tensions=tensions
        or [
            SkillTension(
                skill_a="audit-interaction-design",
                skill_b="audit-decision-psychology",
                axis="efficiency_vs_safety",
                resolution=(
                    "Cooper governs when reversible; Kahneman governs "
                    "when irreversible."
                ),
            )
        ],
    )


def _score_payload(
    *,
    severity: int = 9,
    reach: int = 9,
    persistence: int = 8,
    business_impact: int = 8,
    cognitive_cost: int = 9,
    rationale_prefix: str = "Because evidence shows it",
    overall_note: str = "Core-loop, high-reach, high-cognitive-cost cluster.",
) -> dict[str, Any]:
    """A structurally valid priority-weight payload."""
    return {
        "dimensions": {
            "severity": severity,
            "reach": reach,
            "persistence": persistence,
            "business_impact": business_impact,
            "cognitive_cost": cognitive_cost,
        },
        "rationale": {
            "severity": f"{rationale_prefix} — severity rationale.",
            "reach": f"{rationale_prefix} — reach rationale.",
            "persistence": f"{rationale_prefix} — persistence rationale.",
            "business_impact": f"{rationale_prefix} — business_impact rationale.",
            "cognitive_cost": f"{rationale_prefix} — cognitive_cost rationale.",
        },
        "overall_note": overall_note,
    }


def _score_text(payload: dict[str, Any] | None = None) -> str:
    return json.dumps(payload or _score_payload())


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
# Constants
# =============================================================================


class TestConstants:
    def test_skill_id(self) -> None:
        assert SKILL_ID == "priority-weight"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l6_weight"

    def test_default_model_is_opus_47(self) -> None:
        # ADR-009: L6 reasoning-heavy low-volume.
        assert MODEL == "claude-opus-4-7"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        # Per-pass output ~300 tokens; 4096 has 10× headroom.
        assert 2048 <= MAX_TOKENS <= 8192

    def test_dimension_keys_exactly_five(self) -> None:
        assert DIMENSION_KEYS == frozenset(
            {
                "severity",
                "reach",
                "persistence",
                "business_impact",
                "cognitive_cost",
            }
        )

    def test_default_meta_weights_sum_to_one(self) -> None:
        total = sum(DEFAULT_META_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_default_meta_weights_cover_all_dimensions(self) -> None:
        assert set(DEFAULT_META_WEIGHTS) == DIMENSION_KEYS

    def test_default_meta_weights_symmetric(self) -> None:
        # Each weight is 0.2 (symmetric default).
        for w in DEFAULT_META_WEIGHTS.values():
            assert w == 0.2

    def test_max_dimension_delta_is_one(self) -> None:
        # SKILL.md "Two honest scorers": delta >1 triggers third pass.
        assert MAX_DIMENSION_DELTA == 1

    def test_default_paths(self) -> None:
        assert DEFAULT_RECONCILED == Path(
            "data/derived/l5_reconciled_verdicts.jsonl"
        )
        assert DEFAULT_CLUSTERS == Path(
            "data/derived/l3b_labeled_clusters.jsonl"
        )
        assert DEFAULT_VERDICTS == Path("data/derived/l6_priority_scores.jsonl")
        assert DEFAULT_NATIVE == Path(
            "data/derived/l6_priority_scores.native.jsonl"
        )

    def test_skill_hash_independent_of_other_skills(self) -> None:
        from auditable_design.layers import (
            l4_audit,
            l4_audit_interaction_design,
            l5_reconcile,
        )

        assert skill_hash() != l4_audit.skill_hash()
        assert skill_hash() != l4_audit_interaction_design.skill_hash()
        assert skill_hash() != l5_reconcile.skill_hash()


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
# load_reconciled_verdicts
# =============================================================================


class TestLoadReconciledVerdicts:
    def test_single_verdict_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "reconciled.jsonl"
        _write_jsonl(
            path, [_reconciled(cluster_id="cluster_02").model_dump(mode="json")]
        )
        loaded = load_reconciled_verdicts(path)
        assert set(loaded) == {"cluster_02"}

    def test_multiple_verdicts_keyed_by_cluster(self, tmp_path: Path) -> None:
        path = tmp_path / "reconciled.jsonl"
        _write_jsonl(
            path,
            [
                _reconciled(cluster_id="c1").model_dump(mode="json"),
                _reconciled(cluster_id="c2").model_dump(mode="json"),
            ],
        )
        loaded = load_reconciled_verdicts(path)
        assert set(loaded) == {"c1", "c2"}

    def test_duplicate_cluster_id_last_wins(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "dup.jsonl"
        first = _reconciled(cluster_id="c1").model_dump(mode="json")
        second = _reconciled(cluster_id="c1").model_dump(mode="json")
        second["ranked_violations"] = []  # mark the second one distinctly
        _write_jsonl(path, [first, second])
        with caplog.at_level("WARNING"):
            loaded = load_reconciled_verdicts(path)
        assert loaded["c1"].ranked_violations == []
        assert any("duplicate" in r.message for r in caplog.records)

    def test_malformed_row_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.jsonl"
        # ReconciledVerdict requires cluster_id; ranked_violations and
        # tensions have default_factory=list, so {"cluster_id": "c1"}
        # actually parses. A truly malformed row is one missing
        # cluster_id or with a type-mismatched field.
        path.write_text('{"ranked_violations": []}\n')  # cluster_id missing
        with pytest.raises(RuntimeError, match="not a valid ReconciledVerdict"):
            load_reconciled_verdicts(path)


# =============================================================================
# build_user_message
# =============================================================================


class TestBuildUserMessage:
    def test_contains_cluster_and_reconciled_blocks(self) -> None:
        c = _cluster()
        r = _reconciled()
        msg = build_user_message(c, r)
        assert "<cluster>" in msg and "</cluster>" in msg
        assert "<reconciled_verdict>" in msg and "</reconciled_verdict>" in msg
        assert f"<cluster_id>{c.cluster_id}</cluster_id>" in msg
        assert "<ranked_violations>" in msg
        assert "<tensions>" in msg

    def test_member_count_rendered(self) -> None:
        c = _cluster(members=["r1", "r2", "r3", "r4"])
        msg = build_user_message(c, _reconciled())
        assert "<member_review_ids_count>4</member_review_ids_count>" in msg

    def test_ranked_violations_include_severity_and_heuristic(self) -> None:
        c = _cluster()
        r = _reconciled()
        msg = build_user_message(c, r)
        assert 'severity="9"' in msg
        assert 'heuristic="posture_drift__skeleton_override"' in msg
        assert 'heuristic="loss_framing_on_streak"' in msg

    def test_tensions_include_axis_and_skills(self) -> None:
        c = _cluster()
        r = _reconciled()
        msg = build_user_message(c, r)
        assert 'skill_a="audit-interaction-design"' in msg
        assert 'skill_b="audit-decision-psychology"' in msg
        assert 'axis="efficiency_vs_safety"' in msg

    def test_optional_context_absent_when_none(self) -> None:
        c = _cluster(ui_context=None)
        msg = build_user_message(c, _reconciled())
        assert "<ui_context>" not in msg
        assert "<html>" not in msg
        assert "<screenshot_ref>" not in msg

    def test_html_cdata_wrapped(self) -> None:
        raw = '<button onclick="x()">Submit</button>'
        c = _cluster(html=raw)
        msg = build_user_message(c, _reconciled())
        assert "<html><![CDATA[\n" in msg
        assert raw in msg
        assert "]]></html>" in msg

    def test_quotes_and_label_escaped(self) -> None:
        c = _cluster(
            label="A & B <injected>",
            quotes=["hi <script>alert()</script> & more"],
            ui_context="caf\u00e9 & noise <issue>",
        )
        msg = build_user_message(c, _reconciled())
        assert "<script>" not in msg
        assert "&lt;script&gt;" in msg
        assert "&amp;" in msg

    def test_empty_ranked_and_tensions_still_renders(self) -> None:
        c = _cluster()
        r = _reconciled(ranked=[], tensions=[])
        msg = build_user_message(c, r)
        # The wrapper tags must still be present even if empty — the
        # prompt shape is consistent, the lists are just empty.
        assert "<ranked_violations>" in msg
        assert "</ranked_violations>" in msg
        assert "<tensions>" in msg
        assert "</tensions>" in msg


# =============================================================================
# parse_priority_response
# =============================================================================


class TestParseHappy:
    def test_minimal_happy_path(self) -> None:
        payload = parse_priority_response(_score_text())
        assert set(payload["dimensions"]) == DIMENSION_KEYS
        assert payload["dimensions"]["severity"] == 9

    def test_tolerates_leading_prose(self) -> None:
        text = "Thinking...\n\n" + _score_text()
        parse_priority_response(text)

    def test_tolerates_code_fences(self) -> None:
        text = "```json\n" + _score_text() + "\n```"
        parse_priority_response(text)

    def test_all_zero_scores_legal(self) -> None:
        payload = _score_payload(
            severity=0,
            reach=0,
            persistence=0,
            business_impact=0,
            cognitive_cost=0,
        )
        parse_priority_response(json.dumps(payload))

    def test_all_ten_scores_legal(self) -> None:
        payload = _score_payload(
            severity=10,
            reach=10,
            persistence=10,
            business_impact=10,
            cognitive_cost=10,
        )
        parse_priority_response(json.dumps(payload))


class TestParseFailures:
    def test_no_json(self) -> None:
        with pytest.raises(PriorityParseError, match="no JSON object"):
            parse_priority_response("sorry nothing here")

    def test_malformed_json(self) -> None:
        with pytest.raises(PriorityParseError, match="malformed JSON|no JSON"):
            parse_priority_response('{"dimensions":')

    def test_missing_top_level_key(self) -> None:
        payload = _score_payload()
        del payload["rationale"]
        with pytest.raises(PriorityParseError, match="missing required top-level"):
            parse_priority_response(json.dumps(payload))

    def test_extra_top_level_key(self) -> None:
        payload = {**_score_payload(), "extra": 1}
        with pytest.raises(PriorityParseError, match="unexpected top-level"):
            parse_priority_response(json.dumps(payload))

    def test_dimension_out_of_range_high(self) -> None:
        payload = _score_payload(severity=11)
        with pytest.raises(PriorityParseError, match=r"severity.*=11 out of"):
            parse_priority_response(json.dumps(payload))

    def test_dimension_out_of_range_low(self) -> None:
        payload = _score_payload(severity=-1)
        with pytest.raises(PriorityParseError, match=r"out of \[0, 10\]"):
            parse_priority_response(json.dumps(payload))

    def test_dimension_missing_key(self) -> None:
        payload = _score_payload()
        del payload["dimensions"]["severity"]
        with pytest.raises(PriorityParseError, match="dimensions missing keys"):
            parse_priority_response(json.dumps(payload))

    def test_dimension_extra_key(self) -> None:
        payload = _score_payload()
        payload["dimensions"]["extra_dim"] = 5
        with pytest.raises(PriorityParseError, match="unexpected keys"):
            parse_priority_response(json.dumps(payload))

    def test_dimension_float_rejected(self) -> None:
        payload = _score_payload()
        payload["dimensions"]["severity"] = 7.5  # type: ignore[assignment]
        with pytest.raises(PriorityParseError, match="must be int"):
            parse_priority_response(json.dumps(payload))

    def test_dimension_bool_rejected(self) -> None:
        payload = _score_payload()
        payload["dimensions"]["severity"] = True  # type: ignore[assignment]
        with pytest.raises(PriorityParseError, match="must be int, got bool"):
            parse_priority_response(json.dumps(payload))

    def test_rationale_missing_key(self) -> None:
        payload = _score_payload()
        del payload["rationale"]["reach"]
        with pytest.raises(PriorityParseError, match="rationale missing keys"):
            parse_priority_response(json.dumps(payload))

    def test_rationale_empty_string(self) -> None:
        payload = _score_payload()
        payload["rationale"]["severity"] = "   "
        with pytest.raises(PriorityParseError, match="non-empty str"):
            parse_priority_response(json.dumps(payload))

    def test_overall_note_empty(self) -> None:
        payload = _score_payload(overall_note="   ")
        with pytest.raises(PriorityParseError, match="overall_note.*non-empty"):
            parse_priority_response(json.dumps(payload))


# =============================================================================
# Aggregation logic
# =============================================================================


class TestAggregatePasses:
    def test_two_passes_identical_scores(self) -> None:
        p1 = {d: 5 for d in DIMENSION_KEYS}
        p2 = {d: 5 for d in DIMENSION_KEYS}
        agg, delta = _aggregate_passes([p1, p2])
        assert all(agg[d] == 5 for d in DIMENSION_KEYS)
        assert delta == 0.0

    def test_two_passes_delta_one_rounds(self) -> None:
        p1 = {d: 6 for d in DIMENSION_KEYS}
        p2 = {d: 7 for d in DIMENSION_KEYS}
        agg, delta = _aggregate_passes([p1, p2])
        # mean of (6, 7) = 6.5 → rounded = 6 (Python banker's round) or 7.
        # Accept either — depends on Python rounding.
        for d in DIMENSION_KEYS:
            assert agg[d] in {6, 7}
        assert delta == 1.0

    def test_two_passes_mean_integer(self) -> None:
        p1 = {d: 4 for d in DIMENSION_KEYS}
        p2 = {d: 8 for d in DIMENSION_KEYS}
        agg, delta = _aggregate_passes([p1, p2])
        # mean 6.0 → 6
        assert all(agg[d] == 6 for d in DIMENSION_KEYS)
        assert delta == 4.0

    def test_three_passes_median(self) -> None:
        p1 = {d: 3 for d in DIMENSION_KEYS}
        p2 = {d: 5 for d in DIMENSION_KEYS}
        p3 = {d: 9 for d in DIMENSION_KEYS}
        agg, delta = _aggregate_passes([p1, p2, p3])
        # median of (3, 5, 9) = 5
        assert all(agg[d] == 5 for d in DIMENSION_KEYS)
        assert delta == 6.0  # max - min across triad

    def test_three_passes_max_delta_tracks_widest_swing(self) -> None:
        p1 = {d: (2 if d == "severity" else 5) for d in DIMENSION_KEYS}
        p2 = {d: 5 for d in DIMENSION_KEYS}
        p3 = {d: (8 if d == "severity" else 5) for d in DIMENSION_KEYS}
        agg, delta = _aggregate_passes([p1, p2, p3])
        # severity triad: 2, 5, 8 → median 5, delta 6. Other dims no drift.
        assert agg["severity"] == 5
        assert delta == 6.0

    def test_rejects_unexpected_pass_count(self) -> None:
        p1 = {d: 5 for d in DIMENSION_KEYS}
        with pytest.raises(ValueError, match="unexpected pass count"):
            _aggregate_passes([p1])


class TestNeedsThirdPass:
    def test_delta_zero_no_third_pass(self) -> None:
        p1 = {d: 5 for d in DIMENSION_KEYS}
        p2 = {d: 5 for d in DIMENSION_KEYS}
        assert _needs_third_pass(p1, p2) is False

    def test_delta_one_no_third_pass(self) -> None:
        """MAX_DIMENSION_DELTA = 1 is inclusive upper bound; exactly 1
        does NOT trigger a third pass."""
        p1 = {d: 5 for d in DIMENSION_KEYS}
        p2 = {d: 6 for d in DIMENSION_KEYS}
        assert _needs_third_pass(p1, p2) is False

    def test_delta_two_triggers_third_pass(self) -> None:
        p1 = {d: 5 for d in DIMENSION_KEYS}
        p2 = {d: 7 for d in DIMENSION_KEYS}
        assert _needs_third_pass(p1, p2) is True

    def test_single_dim_drift_triggers(self) -> None:
        p1 = {d: 5 for d in DIMENSION_KEYS}
        p2 = dict(p1)
        p2["severity"] = 8  # delta 3 on one dim
        assert _needs_third_pass(p1, p2) is True


# =============================================================================
# weighted_total
# =============================================================================


class TestWeightedTotal:
    def test_symmetric_weights_arithmetic_mean(self) -> None:
        dims = {d: 5 for d in DIMENSION_KEYS}
        total = weighted_total(dims, DEFAULT_META_WEIGHTS)
        assert abs(total - 5.0) < 1e-9

    def test_skewed_weights(self) -> None:
        weights = {
            "severity": 0.5,
            "reach": 0.2,
            "persistence": 0.1,
            "business_impact": 0.1,
            "cognitive_cost": 0.1,
        }
        dims = {
            "severity": 10,
            "reach": 0,
            "persistence": 0,
            "business_impact": 0,
            "cognitive_cost": 0,
        }
        total = weighted_total(dims, weights)
        assert abs(total - 5.0) < 1e-9

    def test_zero_scores_zero_total(self) -> None:
        dims = {d: 0 for d in DIMENSION_KEYS}
        total = weighted_total(dims, DEFAULT_META_WEIGHTS)
        assert total == 0.0

    def test_all_tens_weighted(self) -> None:
        dims = {d: 10 for d in DIMENSION_KEYS}
        total = weighted_total(dims, DEFAULT_META_WEIGHTS)
        assert abs(total - 10.0) < 1e-9


# =============================================================================
# score_cluster (end-to-end)
# =============================================================================


class TestScoreClusterHappy:
    def test_two_identical_passes_scored(self) -> None:
        client = FakeClient(
            responses=[_score_text(), _score_text()],
        )
        c = _cluster()
        r = _reconciled()
        outcome = asyncio.run(
            score_cluster(c, r, client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "scored"
        assert outcome.reason is None
        assert outcome.priority.validation_passes == 2
        assert outcome.priority.validation_delta == 0.0
        assert len(client.calls) == 2

    def test_two_passes_delta_one_scored_no_third(self) -> None:
        p1 = _score_payload(severity=7)
        p2 = _score_payload(severity=8)  # delta 1, no third pass
        client = FakeClient(
            responses=[json.dumps(p1), json.dumps(p2)],
        )
        outcome = asyncio.run(
            score_cluster(_cluster(), _reconciled(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "scored"
        assert outcome.priority.validation_passes == 2
        assert outcome.priority.validation_delta == 1.0
        assert len(client.calls) == 2
        # Mean of (7, 8) = 7.5 → int rounds to 7 or 8 (Python banker's).
        assert outcome.priority.dimensions["severity"] in {7, 8}

    def test_two_passes_delta_two_triggers_third(self) -> None:
        p1 = _score_payload(severity=5)
        p2 = _score_payload(severity=8)  # delta 3 → third pass
        p3 = _score_payload(severity=7)  # median (5, 7, 8) = 7
        client = FakeClient(
            responses=[json.dumps(p1), json.dumps(p2), json.dumps(p3)],
        )
        outcome = asyncio.run(
            score_cluster(_cluster(), _reconciled(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "scored"
        assert outcome.priority.validation_passes == 3
        assert outcome.priority.dimensions["severity"] == 7
        assert len(client.calls) == 3

    def test_weighted_total_computed(self) -> None:
        client = FakeClient(
            responses=[_score_text(), _score_text()],
        )
        outcome = asyncio.run(
            score_cluster(_cluster(), _reconciled(), client, skill_hash_value=skill_hash())
        )
        # Default scores all = 9/9/8/8/9. Symmetric weights 0.2 each.
        # Weighted total = 0.2*(9+9+8+8+9) = 0.2*43 = 8.6
        assert abs(outcome.priority.weighted_total - 8.6) < 1e-9

    def test_meta_weights_preserved_in_priority(self) -> None:
        client = FakeClient(
            responses=[_score_text(), _score_text()],
        )
        custom = {
            "severity": 0.5,
            "reach": 0.2,
            "persistence": 0.1,
            "business_impact": 0.1,
            "cognitive_cost": 0.1,
        }
        outcome = asyncio.run(
            score_cluster(
                _cluster(), _reconciled(), client,
                meta_weights=custom, skill_hash_value=skill_hash(),
            )
        )
        assert outcome.priority.meta_weights == custom

    def test_call_uses_layer_constants(self) -> None:
        client = FakeClient(
            responses=[_score_text(), _score_text()],
        )
        asyncio.run(
            score_cluster(_cluster(), _reconciled(), client, skill_hash_value=skill_hash())
        )
        for call in client.calls:
            assert call["skill_id"] == SKILL_ID
            assert call["model"] == MODEL
            assert call["temperature"] == TEMPERATURE
            assert call["max_tokens"] == MAX_TOKENS
            assert call["system"] == SYSTEM_PROMPT


class TestScoreClusterFallback:
    def test_both_passes_unparseable_yields_fallback(self) -> None:
        client = FakeClient(
            responses=["not json", "still not json"],
        )
        outcome = asyncio.run(
            score_cluster(_cluster(), _reconciled(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "fallback"
        assert "both passes failed to parse" in (outcome.reason or "")
        assert all(outcome.priority.dimensions[d] == 0 for d in DIMENSION_KEYS)
        assert outcome.priority.weighted_total == 0.0

    def test_one_pass_parse_one_fail_flags_fallback(self) -> None:
        client = FakeClient(
            responses=[_score_text(), "garbage"],
        )
        outcome = asyncio.run(
            score_cluster(_cluster(), _reconciled(), client, skill_hash_value=skill_hash())
        )
        # One pass parsed; validation discipline did not run → fallback
        # status but scores come from the single parsed pass.
        assert outcome.status == "fallback"
        assert "only one" in (outcome.reason or "")
        assert outcome.priority.dimensions["severity"] == 9  # from the parsed pass

    def test_third_pass_fallback_still_aggregates_on_two(self) -> None:
        """If pass 1 and 2 parse but drift, then pass 3 fails, we
        aggregate on the two parsed passes and still produce a scored
        outcome (no fallback)."""
        p1 = _score_payload(severity=5)
        p2 = _score_payload(severity=8)  # delta 3 → third pass
        client = FakeClient(
            responses=[json.dumps(p1), json.dumps(p2), "garbage"],
        )
        outcome = asyncio.run(
            score_cluster(_cluster(), _reconciled(), client, skill_hash_value=skill_hash())
        )
        assert outcome.status == "scored"
        # With only two parsed passes, we aggregate those two.
        assert outcome.priority.validation_passes == 2
        assert len(client.calls) == 3

    def test_transport_failure_propagates(self) -> None:
        client = FakeClient(
            raise_on={"Streak loss framing": RuntimeError("replay miss")},
        )
        with pytest.raises(RuntimeError, match="replay miss"):
            asyncio.run(
                score_cluster(_cluster(), _reconciled(), client, skill_hash_value=skill_hash())
            )


# =============================================================================
# score_batch
# =============================================================================


class TestScoreBatch:
    def test_processes_all_clusters_with_reconciled(self) -> None:
        client = FakeClient(
            # 2 passes × 2 clusters = 4 responses needed.
            responses=[_score_text(), _score_text(), _score_text(), _score_text()],
        )
        clusters = [_cluster(cluster_id=f"c{i}") for i in range(2)]
        reconciled_by = {
            "c0": _reconciled(cluster_id="c0"),
            "c1": _reconciled(cluster_id="c1"),
        }
        outcomes, failures = asyncio.run(
            score_batch(clusters, reconciled_by, client)
        )
        assert len(outcomes) == 2
        assert failures == []
        assert all(o.status == "scored" for o in outcomes)

    def test_missing_reconciled_yields_fallback_without_call(self) -> None:
        client = FakeClient()  # empty queue — would fail if called
        clusters = [_cluster(cluster_id="c0")]
        outcomes, failures = asyncio.run(
            score_batch(clusters, {}, client)
        )
        assert failures == []
        assert len(outcomes) == 1
        assert outcomes[0].status == "fallback"
        assert "no reconciled verdict" in (outcomes[0].reason or "")
        assert client.calls == []

    def test_transport_failure_isolated_per_cluster(self) -> None:
        client = FakeClient(
            responses=[_score_text(), _score_text()] * 2,
            raise_on={"cluster_bad": RuntimeError("boom")},
        )
        clusters = [
            _cluster(cluster_id="c0"),
            _cluster(
                cluster_id="c1",
                quotes=["something", "cluster_bad marker"],
            ),
        ]
        reconciled_by = {
            "c0": _reconciled(cluster_id="c0"),
            "c1": _reconciled(cluster_id="c1"),
        }
        outcomes, failures = asyncio.run(
            score_batch(clusters, reconciled_by, client)
        )
        assert len(outcomes) == 1
        assert len(failures) == 1
        assert failures[0][0] == "c1"


# =============================================================================
# build_provenance
# =============================================================================


class TestProvenance:
    def _outcome(
        self,
        *,
        cluster_id: str,
        status: str,
        dims: dict[str, int] | None = None,
        passes: int = 2,
        delta: float = 0.0,
    ) -> PriorityOutcome:
        d = dims if dims is not None else {k: 5 for k in DIMENSION_KEYS}
        p = PriorityScore(
            cluster_id=cluster_id,
            dimensions=d,
            meta_weights=dict(DEFAULT_META_WEIGHTS),
            weighted_total=weighted_total(d, DEFAULT_META_WEIGHTS),
            validation_passes=passes,
            validation_delta=delta,
        )
        return PriorityOutcome(
            cluster_id=cluster_id,
            priority=p,
            passes=[],
            status=status,  # type: ignore[arg-type]
            reason=None,
        )

    def test_counts(self) -> None:
        outcomes = [
            self._outcome(cluster_id="c0", status="scored"),
            self._outcome(cluster_id="c1", status="fallback"),
        ]
        prov = build_provenance(
            outcomes, failures=[], model=MODEL, meta_weights=DEFAULT_META_WEIGHTS,
        )
        assert prov["cluster_count"] == 2
        assert prov["scored_count"] == 1
        assert prov["fallback_count"] == 1

    def test_dimension_mean_min_max(self) -> None:
        outcomes = [
            self._outcome(
                cluster_id="c0", status="scored",
                dims={k: (3 if k == "severity" else 5) for k in DIMENSION_KEYS},
            ),
            self._outcome(
                cluster_id="c1", status="scored",
                dims={k: (9 if k == "severity" else 5) for k in DIMENSION_KEYS},
            ),
        ]
        prov = build_provenance(
            outcomes, failures=[], model=MODEL, meta_weights=DEFAULT_META_WEIGHTS,
        )
        assert prov["dimension_score_mean"]["severity"] == 6.0  # (3+9)/2
        assert prov["dimension_score_min"]["severity"] == 3
        assert prov["dimension_score_max"]["severity"] == 9

    def test_validation_pass_histogram(self) -> None:
        outcomes = [
            self._outcome(cluster_id="c0", status="scored", passes=2),
            self._outcome(cluster_id="c1", status="scored", passes=2),
            self._outcome(cluster_id="c2", status="scored", passes=3),
        ]
        prov = build_provenance(
            outcomes, failures=[], model=MODEL, meta_weights=DEFAULT_META_WEIGHTS,
        )
        assert prov["validation_passes_histogram"]["2"] == 2
        assert prov["validation_passes_histogram"]["3"] == 1
        assert prov["third_pass_triggered"] == 1

    def test_validation_delta_histogram(self) -> None:
        outcomes = [
            self._outcome(cluster_id="c0", status="scored", delta=0.0),
            self._outcome(cluster_id="c1", status="scored", delta=1.0),
            self._outcome(cluster_id="c2", status="scored", delta=3.0),
        ]
        prov = build_provenance(
            outcomes, failures=[], model=MODEL, meta_weights=DEFAULT_META_WEIGHTS,
        )
        assert prov["validation_delta_histogram"]["0"] == 1
        assert prov["validation_delta_histogram"]["1"] == 1
        assert prov["validation_delta_histogram"]["3"] == 1

    def test_weighted_total_mean(self) -> None:
        outcomes = [
            self._outcome(
                cluster_id="c0", status="scored",
                dims={k: 5 for k in DIMENSION_KEYS},
            ),
            self._outcome(
                cluster_id="c1", status="scored",
                dims={k: 8 for k in DIMENSION_KEYS},
            ),
        ]
        prov = build_provenance(
            outcomes, failures=[], model=MODEL, meta_weights=DEFAULT_META_WEIGHTS,
        )
        # weighted_total c0 = 5.0; c1 = 8.0; mean = 6.5
        assert prov["weighted_total_mean"] == 6.5

    def test_transport_failures(self) -> None:
        prov = build_provenance(
            outcomes=[], failures=[("c99", ValueError("oops"))],
            model=MODEL, meta_weights=DEFAULT_META_WEIGHTS,
        )
        assert prov["transport_failure_count"] == 1
        assert prov["transport_failures"][0]["cluster_id"] == "c99"


# =============================================================================
# CLI — main
# =============================================================================


class TestMain:
    def test_cli_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l6, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "clusters.jsonl"
        _write_jsonl(
            clusters_path,
            [_cluster(cluster_id="cluster_02").model_dump(mode="json")],
        )
        reconciled_path = data_dir / "reconciled.jsonl"
        _write_jsonl(
            reconciled_path,
            [_reconciled(cluster_id="cluster_02").model_dump(mode="json")],
        )
        output_path = data_dir / "priority.jsonl"
        native_path = data_dir / "priority.native.jsonl"

        fake = FakeClient(responses=[_score_text(), _score_text()])
        monkeypatch.setattr(l6, "Client", lambda **_k: fake)

        rc = main(
            [
                "--reconciled",
                str(reconciled_path),
                "--clusters",
                str(clusters_path),
                "--output",
                str(output_path),
                "--native-output",
                str(native_path),
                "--mode",
                "replay",
                "--run-id",
                "l6-test-run",
            ]
        )
        assert rc == 0
        assert output_path.exists()
        verdicts_raw = [
            json.loads(line) for line in output_path.read_text().splitlines()
        ]
        assert len(verdicts_raw) == 1
        for row in verdicts_raw:
            ps = PriorityScore.model_validate(row)
            assert ps.cluster_id == "cluster_02"
            assert ps.weighted_total > 0
        prov_path = output_path.with_suffix(".provenance.json")
        prov = json.loads(prov_path.read_text())
        assert prov["scored_count"] == 1

    def test_cli_empty_clusters_returns_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l6, "_resolve_repo_root", lambda: tmp_path)

        clusters_path = data_dir / "empty.jsonl"
        clusters_path.write_text("")
        reconciled_path = data_dir / "reconciled.jsonl"
        reconciled_path.write_text("")

        monkeypatch.setattr(l6, "Client", lambda **_k: FakeClient())
        rc = main(
            [
                "--reconciled",
                str(reconciled_path),
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
