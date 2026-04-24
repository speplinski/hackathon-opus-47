"""Tests for ``auditable_design.layers.l8_optimize``.

Structure mirrors ``test_l7_decide.py`` — FakeClient + helper factories
for the four input artefacts (cluster, reconciled, priority,
decision), parser happy/failure tests, end-to-end optimize_cluster,
batch plumbing, provenance.

L8-specific:

* **Two iterations per cluster** (baseline + proposed) — tests verify
  both iterations are constructed with correct schema fields (index,
  parent_iteration_id, design_artifact_ref, accepted, regression_reason).
* **Design artifacts written to disk** — tests verify .md files are
  created under artifacts_dir with before/after snapshot content.
* **Pareto verdict wrapped in outcome** — tests verify accept / reject
  paths flow through to OptimizationIteration.accepted and
  regression_reason fields.
* **Fallback produces a no-op iteration 1** — parse failure on re-audit
  copies baseline scores, Pareto verdict correctly rejects.
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
from auditable_design.evaluators.pareto import ParetoVerdict
from auditable_design.layers import l8_optimize as l8
from auditable_design.layers.l8_optimize import (
    BASELINE_SKILL_ID,
    DEFAULT_ARTIFACTS_DIR,
    DEFAULT_CLUSTERS,
    DEFAULT_DECISIONS,
    DEFAULT_ITERATIONS,
    DEFAULT_MAX_REGRESSION,
    DEFAULT_NATIVE,
    DEFAULT_PRIORITY,
    DEFAULT_RECONCILED,
    LAYER_NAME,
    MAX_TOKENS,
    MODEL,
    SKILL_ID,
    SYSTEM_PROMPT,
    TEMPERATURE,
    VALID_ANCHORED_SEVERITIES,
    OptimizeOutcome,
    OptimizeParseError,
    build_baseline_iteration,
    build_provenance,
    build_user_message,
    load_decisions,
    main,
    optimize_batch,
    optimize_cluster,
    parse_optimize_response,
    reconciled_heuristic_list,
    skill_hash,
)
from auditable_design.schemas import (
    DesignDecision,
    HeuristicViolation,
    InsightCluster,
    OptimizationIteration,
    PriorityScore,
    ReconciledVerdict,
    SkillTension,
)


# =============================================================================
# Helpers
# =============================================================================


@dataclass
class FakeClient:
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
            output_tokens=150,
            cost_usd=0.0,
            timestamp="2026-04-23T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _cluster(
    *,
    cluster_id: str = "cluster_02",
    members: list[str] | None = None,
    ui_context: str | None = "Duolingo mid-lesson modal.",
) -> InsightCluster:
    return InsightCluster(
        cluster_id=cluster_id,
        label="Streak loss framing pressures users into mid-session purchase",
        member_review_ids=members or ["r1", "r2", "r3"],
        centroid_vector_ref="l3_centroids.npy#0",
        representative_quotes=["manipulative", "800-day streak", "mid-lesson"],
        ui_context=ui_context,
    )


def _reconciled(
    *,
    cluster_id: str = "cluster_02",
    heuristics: list[tuple[str, int]] | None = None,
) -> ReconciledVerdict:
    """ReconciledVerdict with (slug, severity) pairs for ranked_violations."""
    spec = heuristics or [
        ("modal_excise", 7),
        ("posture_drift_within_product", 9),
        ("competing_calls_to_action", 7),
        ("loss_framing_on_streak", 9),
    ]
    ranked = [
        HeuristicViolation(
            heuristic=slug,
            violation=f"violation of {slug}",
            severity=sev,
            evidence_review_ids=[],
            reasoning=f"rank info for {slug}",
        )
        for slug, sev in spec
    ]
    return ReconciledVerdict(
        cluster_id=cluster_id,
        ranked_violations=ranked,
        tensions=[
            SkillTension(
                skill_a="audit-interaction-design",
                skill_b="audit-decision-psychology",
                axis="efficiency_vs_safety",
                resolution="cooper if reversible, kahneman if irreversible",
            ),
        ],
    )


def _priority(*, cluster_id: str = "cluster_02") -> PriorityScore:
    dims = {
        "severity": 10,
        "reach": 9,
        "persistence": 8,
        "business_impact": 9,
        "cognitive_cost": 9,
    }
    weights = {k: 0.2 for k in dims}
    return PriorityScore(
        cluster_id=cluster_id,
        dimensions=dims,
        meta_weights=weights,
        weighted_total=sum(dims[k] * weights[k] for k in dims),
        validation_passes=2,
        validation_delta=0.0,
    )


def _decision(
    *,
    cluster_id: str = "cluster_02",
    resolves: list[str] | None = None,
) -> DesignDecision:
    return DesignDecision(
        decision_id=f"decision__{cluster_id}__1",
        principle_id=f"principle__{cluster_id}",
        description="Move modal to lesson-complete boundary.",
        before_snapshot="Modal fires mid-lesson. Full-viewport blocker.",
        after_snapshot=(
            "No mid-lesson modal; lesson completes uninterrupted; "
            "streak-risk surface appears on lesson-complete screen as "
            "non-blocking banner with three equal-weight paths."
        ),
        resolves_heuristics=resolves or [
            "modal_excise",
            "posture_drift_within_product",
            "competing_calls_to_action",
        ],
    )


def _optimize_payload(
    *,
    scored: dict[str, int] | None = None,
    reasoning: str = "The after_snapshot resolves modal/posture structurally.",
) -> dict[str, Any]:
    """Builds a structurally valid re-audit payload.

    Default scored_heuristics matches _reconciled default heuristic list.
    Default severities are improvements across the board (Pareto dominance).
    """
    return {
        "scored_heuristics": (
            scored if scored is not None
            else {
                "modal_excise": 0,
                "posture_drift_within_product": 0,
                "competing_calls_to_action": 3,
                "loss_framing_on_streak": 5,
            }
        ),
        "reasoning": reasoning,
    }


def _optimize_text(payload: dict[str, Any] | None = None) -> str:
    return json.dumps(payload or _optimize_payload())


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
        assert SKILL_ID == "design-optimize"

    def test_layer_name(self) -> None:
        assert LAYER_NAME == "l8_optimize"

    def test_default_model_is_opus_47(self) -> None:
        assert MODEL == "claude-opus-4-7"

    def test_temperature_is_zero(self) -> None:
        assert TEMPERATURE == 0.0

    def test_max_tokens_reasonable(self) -> None:
        assert 2048 <= MAX_TOKENS <= 8192

    def test_valid_anchored_severities(self) -> None:
        # ADR-008 anchors.
        assert VALID_ANCHORED_SEVERITIES == frozenset({0, 3, 5, 7, 9})

    def test_baseline_skill_id(self) -> None:
        assert BASELINE_SKILL_ID == "reconciled"

    def test_default_max_regression(self) -> None:
        assert DEFAULT_MAX_REGRESSION == 1

    def test_default_paths(self) -> None:
        assert DEFAULT_ITERATIONS == Path(
            "data/derived/l8_optimization_iterations.jsonl"
        )
        assert DEFAULT_NATIVE == Path(
            "data/derived/l8_optimization_iterations.native.jsonl"
        )
        assert DEFAULT_ARTIFACTS_DIR == Path("data/artifacts/iterations")

    def test_skill_hash_independent(self) -> None:
        from auditable_design.layers import (
            l5_reconcile,
            l6_weight,
            l7_decide,
        )

        assert skill_hash() != l5_reconcile.skill_hash()
        assert skill_hash() != l6_weight.skill_hash()
        assert skill_hash() != l7_decide.skill_hash()


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


# =============================================================================
# load_decisions
# =============================================================================


class TestLoadDecisions:
    def test_loads_by_cluster_id_extracted_from_decision_id(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "decisions.jsonl"
        d = _decision(cluster_id="cluster_02")
        _write_jsonl(path, [d.model_dump(mode="json")])
        loaded = load_decisions(path)
        assert set(loaded) == {"cluster_02"}

    def test_multiple(self, tmp_path: Path) -> None:
        path = tmp_path / "decisions.jsonl"
        _write_jsonl(
            path,
            [
                _decision(cluster_id="c0").model_dump(mode="json"),
                _decision(cluster_id="c1").model_dump(mode="json"),
            ],
        )
        loaded = load_decisions(path)
        assert set(loaded) == {"c0", "c1"}

    def test_duplicate_last_wins(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "dup.jsonl"
        first = _decision(cluster_id="c0")
        second = _decision(cluster_id="c0", resolves=["modal_excise"])
        _write_jsonl(
            path, [first.model_dump(mode="json"), second.model_dump(mode="json")]
        )
        with caplog.at_level("WARNING"):
            loaded = load_decisions(path)
        assert loaded["c0"].resolves_heuristics == ["modal_excise"]
        assert any("duplicate" in r.message for r in caplog.records)


# =============================================================================
# reconciled_heuristic_list
# =============================================================================


class TestReconciledHeuristicList:
    def test_order_preserved(self) -> None:
        r = _reconciled(
            heuristics=[("b", 5), ("a", 7), ("c", 9)]
        )
        assert reconciled_heuristic_list(r) == ["b", "a", "c"]

    def test_duplicates_collapsed(self) -> None:
        r = _reconciled(
            heuristics=[("a", 5), ("b", 7), ("a", 9)]
        )
        assert reconciled_heuristic_list(r) == ["a", "b"]


# =============================================================================
# build_baseline_iteration
# =============================================================================


class TestBuildBaselineIteration:
    def test_builds_valid_iteration_0(self, tmp_path: Path) -> None:
        c = _cluster()
        r = _reconciled()
        d = _decision()
        artifacts = tmp_path / "arts"
        it = build_baseline_iteration(c, r, d, artifacts, run_id="l8-test")
        assert it.iteration_index == 0
        assert it.parent_iteration_id is None
        assert it.accepted is True
        assert it.regression_reason is None
        assert it.run_id == "l8-test"
        # scores shape: {BASELINE_SKILL_ID: {heuristic: severity}}
        assert set(it.scores) == {BASELINE_SKILL_ID}
        assert set(it.scores[BASELINE_SKILL_ID]) == {
            "modal_excise",
            "posture_drift_within_product",
            "competing_calls_to_action",
            "loss_framing_on_streak",
        }

    def test_writes_design_artifact(self, tmp_path: Path) -> None:
        c = _cluster()
        r = _reconciled()
        d = _decision()
        artifacts = tmp_path / "arts"
        it = build_baseline_iteration(c, r, d, artifacts, run_id="l8-test")
        artifact = Path(it.design_artifact_ref)
        assert artifact.exists()
        body = artifact.read_text()
        assert "baseline" in body.lower()
        assert d.before_snapshot in body

    def test_max_severity_wins_for_duplicates(self, tmp_path: Path) -> None:
        c = _cluster()
        r = _reconciled(
            heuristics=[("h", 3), ("other", 5), ("h", 9)]
        )
        d = _decision()
        it = build_baseline_iteration(c, r, d, tmp_path / "arts", run_id="l8-test")
        assert it.scores[BASELINE_SKILL_ID]["h"] == 9


# =============================================================================
# build_user_message
# =============================================================================


class TestBuildUserMessage:
    def test_envelope_contains_all_blocks(self) -> None:
        c = _cluster()
        r = _reconciled()
        d = _decision()
        msg = build_user_message(c, r, d)
        assert "<cluster>" in msg
        assert "<before_snapshot>" in msg
        assert "<after_snapshot>" in msg
        assert "<baseline_heuristics>" in msg

    def test_baseline_heuristics_include_slug_severity_violation(self) -> None:
        c = _cluster()
        r = _reconciled()
        d = _decision()
        msg = build_user_message(c, r, d)
        assert 'slug="modal_excise"' in msg
        assert 'severity="7"' in msg  # modal_excise severity in _reconciled default
        assert "violation of modal_excise" in msg

    def test_cluster_ui_context_when_present(self) -> None:
        c = _cluster(ui_context="foo bar")
        msg = build_user_message(c, _reconciled(), _decision())
        assert "<ui_context>foo bar</ui_context>" in msg

    def test_escaping(self) -> None:
        d = _decision()
        d = DesignDecision(
            decision_id=d.decision_id,
            principle_id=d.principle_id,
            description=d.description,
            before_snapshot="has & ampersand < and > brackets",
            after_snapshot=d.after_snapshot,
            resolves_heuristics=d.resolves_heuristics,
        )
        msg = build_user_message(_cluster(), _reconciled(), d)
        assert "&amp;" in msg
        assert "&lt;" in msg
        assert "&gt;" in msg


# =============================================================================
# parse_optimize_response
# =============================================================================


class TestParseHappy:
    def test_basic_happy(self) -> None:
        baseline = ["a", "b", "c"]
        payload = {
            "scored_heuristics": {"a": 0, "b": 3, "c": 5},
            "reasoning": "some note",
        }
        parsed = parse_optimize_response(
            json.dumps(payload), baseline_heuristics=baseline
        )
        assert parsed["scored_heuristics"] == {"a": 0, "b": 3, "c": 5}

    def test_tolerates_prose_prefix(self) -> None:
        baseline = ["a"]
        text = "thinking...\n" + json.dumps({
            "scored_heuristics": {"a": 7},
            "reasoning": "note",
        })
        parse_optimize_response(text, baseline_heuristics=baseline)

    def test_all_anchor_values_legal(self) -> None:
        baseline = ["h0", "h3", "h5", "h7", "h9"]
        payload = {
            "scored_heuristics": {"h0": 0, "h3": 3, "h5": 5, "h7": 7, "h9": 9},
            "reasoning": "full anchor coverage",
        }
        parse_optimize_response(
            json.dumps(payload), baseline_heuristics=baseline
        )


class TestParseFailures:
    def test_no_json(self) -> None:
        with pytest.raises(OptimizeParseError, match="no JSON object"):
            parse_optimize_response("garbage", baseline_heuristics=["a"])

    def test_missing_top_level_key(self) -> None:
        payload = {"scored_heuristics": {"a": 0}}  # missing reasoning
        with pytest.raises(OptimizeParseError, match="missing required top-level"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=["a"]
            )

    def test_extra_top_level_key(self) -> None:
        payload = {
            "scored_heuristics": {"a": 0},
            "reasoning": "x",
            "extra": 1,
        }
        with pytest.raises(OptimizeParseError, match="unexpected top-level"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=["a"]
            )

    def test_scored_keys_missing_baseline(self) -> None:
        baseline = ["a", "b"]
        payload = {"scored_heuristics": {"a": 0}, "reasoning": "x"}  # missing b
        with pytest.raises(OptimizeParseError, match="missing heuristics"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=baseline
            )

    def test_scored_has_extras(self) -> None:
        baseline = ["a"]
        payload = {
            "scored_heuristics": {"a": 0, "b": 0},
            "reasoning": "x",
        }
        with pytest.raises(OptimizeParseError, match="extra heuristics"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=baseline
            )

    def test_non_anchored_severity_rejected(self) -> None:
        baseline = ["a"]
        payload = {"scored_heuristics": {"a": 4}, "reasoning": "x"}
        with pytest.raises(OptimizeParseError, match=r"not in"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=baseline
            )

    def test_severity_out_of_range(self) -> None:
        baseline = ["a"]
        payload = {"scored_heuristics": {"a": 11}, "reasoning": "x"}
        with pytest.raises(OptimizeParseError, match=r"not in"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=baseline
            )

    def test_severity_not_int(self) -> None:
        baseline = ["a"]
        payload = {"scored_heuristics": {"a": "zero"}, "reasoning": "x"}
        with pytest.raises(OptimizeParseError, match="must be int"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=baseline
            )

    def test_bool_not_int(self) -> None:
        baseline = ["a"]
        payload = {"scored_heuristics": {"a": True}, "reasoning": "x"}
        with pytest.raises(OptimizeParseError, match="must be int, got bool"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=baseline
            )

    def test_empty_reasoning_rejected(self) -> None:
        baseline = ["a"]
        payload = {"scored_heuristics": {"a": 0}, "reasoning": "   "}
        with pytest.raises(OptimizeParseError, match="reasoning.*non-empty"):
            parse_optimize_response(
                json.dumps(payload), baseline_heuristics=baseline
            )


# =============================================================================
# optimize_cluster
# =============================================================================


class TestOptimizeClusterHappy:
    def test_dominance_accept(self, tmp_path: Path) -> None:
        """Default payload has strict improvements on all heuristics →
        Pareto dominance → accepted."""
        client = FakeClient(default_response=_optimize_text())
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert outcome.status == "optimized"
        assert outcome.reason is None
        assert outcome.verdict.accepted is True
        assert outcome.verdict.dominance is True
        assert outcome.proposed.accepted is True
        assert outcome.proposed.regression_reason is None

    def test_baseline_and_proposed_iteration_linkage(self, tmp_path: Path) -> None:
        """Iteration 1's parent_iteration_id points at iteration 0."""
        client = FakeClient(default_response=_optimize_text())
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert outcome.baseline.iteration_index == 0
        assert outcome.baseline.parent_iteration_id is None
        assert outcome.proposed.iteration_index == 1
        assert outcome.proposed.parent_iteration_id == outcome.baseline.iteration_id

    def test_proposed_scores_reflect_payload(self, tmp_path: Path) -> None:
        scored = {
            "modal_excise": 3,
            "posture_drift_within_product": 0,
            "competing_calls_to_action": 5,
            "loss_framing_on_streak": 7,
        }
        payload = _optimize_payload(scored=scored)
        client = FakeClient(default_response=json.dumps(payload))
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert outcome.proposed.scores[BASELINE_SKILL_ID] == scored

    def test_delta_per_heuristic_populated(self, tmp_path: Path) -> None:
        client = FakeClient(default_response=_optimize_text())
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        # Default: baseline modal_excise=7, proposed=0 → delta=-7
        assert outcome.proposed.delta_per_heuristic["modal_excise"] == -7

    def test_artifacts_written(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "arts"
        client = FakeClient(default_response=_optimize_text())
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=artifacts,
            )
        )
        iter0_path = Path(outcome.baseline.design_artifact_ref)
        iter1_path = Path(outcome.proposed.design_artifact_ref)
        assert iter0_path.exists()
        assert iter1_path.exists()
        assert "iter00" in iter0_path.name
        assert "iter01" in iter1_path.name
        assert "baseline" in iter0_path.read_text().lower()

    def test_call_uses_layer_constants(self, tmp_path: Path) -> None:
        client = FakeClient(default_response=_optimize_text())
        asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["skill_id"] == SKILL_ID
        assert call["model"] == MODEL
        assert call["temperature"] == TEMPERATURE
        assert call["max_tokens"] == MAX_TOKENS


class TestOptimizeClusterRejectPaths:
    def test_weighted_sum_fallback_accept(self, tmp_path: Path) -> None:
        """One regression, sum still improves → accept via fallback."""
        # Default baseline: modal=7, posture=9, competing=7, loss=9 → sum 32
        scored = {
            "modal_excise": 0,
            "posture_drift_within_product": 0,
            "competing_calls_to_action": 9,  # regresses 7→9
            "loss_framing_on_streak": 5,
        }  # sum 14 < 32 → accept via fallback
        payload = _optimize_payload(scored=scored)
        client = FakeClient(default_response=json.dumps(payload))
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert outcome.verdict.accepted is True
        assert outcome.verdict.dominance is False
        assert outcome.verdict.regression_count == 1

    def test_too_many_regressions_reject(self, tmp_path: Path) -> None:
        scored = {
            "modal_excise": 9,  # regresses
            "posture_drift_within_product": 9,  # equal (no regression)
            "competing_calls_to_action": 9,  # regresses
            "loss_framing_on_streak": 0,  # improves
        }
        payload = _optimize_payload(scored=scored)
        client = FakeClient(default_response=json.dumps(payload))
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert outcome.verdict.accepted is False
        assert outcome.verdict.regression_count == 2
        assert outcome.proposed.accepted is False
        assert outcome.proposed.regression_reason is not None


class TestOptimizeClusterFallback:
    def test_parse_failure_yields_no_op_iteration(self, tmp_path: Path) -> None:
        client = FakeClient(default_response="not json")
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert outcome.status == "fallback"
        assert outcome.reason is not None
        # No-op iteration → scores equal baseline → rejected
        assert outcome.verdict.accepted is False
        assert outcome.proposed.scores[BASELINE_SKILL_ID] == (
            outcome.baseline.scores[BASELINE_SKILL_ID]
        )

    def test_missing_heuristic_in_scored_yields_fallback(self, tmp_path: Path) -> None:
        # Scored payload misses one of the baseline heuristics.
        payload = {
            "scored_heuristics": {
                "modal_excise": 0,
                "posture_drift_within_product": 0,
                # missing competing_calls_to_action, loss_framing_on_streak
            },
            "reasoning": "incomplete re-audit",
        }
        client = FakeClient(default_response=json.dumps(payload))
        outcome = asyncio.run(
            optimize_cluster(
                _cluster(), _reconciled(), _priority(), _decision(),
                client,
                skill_hash_value=skill_hash(),
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert outcome.status == "fallback"
        assert "missing" in (outcome.reason or "").lower()

    def test_transport_failure_propagates(self, tmp_path: Path) -> None:
        client = FakeClient(
            raise_on={"Streak loss framing": RuntimeError("replay miss")}
        )
        with pytest.raises(RuntimeError, match="replay miss"):
            asyncio.run(
                optimize_cluster(
                    _cluster(), _reconciled(), _priority(), _decision(),
                    client,
                    skill_hash_value=skill_hash(),
                    run_id="l8-test",
                    artifacts_dir=tmp_path / "arts",
                )
            )


# =============================================================================
# optimize_batch
# =============================================================================


class TestOptimizeBatch:
    def test_processes_all_clusters(self, tmp_path: Path) -> None:
        client = FakeClient(default_response=_optimize_text())
        clusters = [_cluster(cluster_id=f"c{i}") for i in range(2)]
        rec = {f"c{i}": _reconciled(cluster_id=f"c{i}") for i in range(2)}
        pri = {f"c{i}": _priority(cluster_id=f"c{i}") for i in range(2)}
        dec = {f"c{i}": _decision(cluster_id=f"c{i}") for i in range(2)}
        outcomes, failures = asyncio.run(
            optimize_batch(
                clusters, rec, pri, dec, client,
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert len(outcomes) == 2
        assert failures == []

    def test_missing_input_yields_fallback(self, tmp_path: Path) -> None:
        client = FakeClient()
        clusters = [_cluster(cluster_id="c0")]
        outcomes, failures = asyncio.run(
            optimize_batch(
                clusters, {}, {}, {}, client,
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert failures == []
        assert outcomes[0].status == "fallback"

    def test_transport_failure_isolated(self, tmp_path: Path) -> None:
        client = FakeClient(
            default_response=_optimize_text(),
            raise_on={"cluster_bad": RuntimeError("boom")},
        )
        clusters = [
            _cluster(cluster_id="c0"),
            _cluster(
                cluster_id="c1",
                members=["cluster_bad_marker"],
            ),
        ]
        # Give c1 a quote that matches so FakeClient raises
        clusters[1] = InsightCluster(
            cluster_id="c1",
            label="label",
            member_review_ids=["r1"],
            centroid_vector_ref="x",
            representative_quotes=["cluster_bad marker here"],
            ui_context=None,
        )
        rec = {f"c{i}": _reconciled(cluster_id=f"c{i}") for i in range(2)}
        pri = {f"c{i}": _priority(cluster_id=f"c{i}") for i in range(2)}
        dec = {f"c{i}": _decision(cluster_id=f"c{i}") for i in range(2)}
        outcomes, failures = asyncio.run(
            optimize_batch(
                clusters, rec, pri, dec, client,
                run_id="l8-test",
                artifacts_dir=tmp_path / "arts",
            )
        )
        assert len(outcomes) == 1
        assert len(failures) == 1


# =============================================================================
# build_provenance
# =============================================================================


class TestProvenance:
    def _outcome(
        self,
        *,
        cluster_id: str,
        status: str = "optimized",
        accepted: bool = True,
        dominance: bool = True,
        regression_count: int = 0,
        delta: dict[str, int] | None = None,
    ) -> OptimizeOutcome:
        v = ParetoVerdict(
            accepted=accepted,
            reason="test reason",
            regression_count=regression_count,
            dominance=dominance,
            delta_per_heuristic=delta or {"h1": -7, "h2": -6},
        )
        baseline = OptimizationIteration(
            iteration_id=f"iteration__{cluster_id}__00",
            run_id="l8-test",
            iteration_index=0,
            parent_iteration_id=None,
            design_artifact_ref=f"/tmp/{cluster_id}_iter00.md",
            scores={BASELINE_SKILL_ID: {"h1": 7, "h2": 9}},
            reasoning="baseline",
            accepted=True,
            regression_reason=None,
            delta_per_heuristic={},
            informing_review_ids=["r1"],
            recorded_at=datetime.now(UTC),
        )
        proposed = OptimizationIteration(
            iteration_id=f"iteration__{cluster_id}__01",
            run_id="l8-test",
            iteration_index=1,
            parent_iteration_id=baseline.iteration_id,
            design_artifact_ref=f"/tmp/{cluster_id}_iter01.md",
            scores={BASELINE_SKILL_ID: {"h1": 0, "h2": 3}},
            reasoning="proposed",
            accepted=accepted,
            regression_reason=None if accepted else "test reason",
            delta_per_heuristic=v.delta_per_heuristic,
            informing_review_ids=["r1"],
            recorded_at=datetime.now(UTC),
        )
        return OptimizeOutcome(
            cluster_id=cluster_id,
            baseline=baseline,
            proposed=proposed,
            verdict=v,
            native_payload={"_": "ok"},
            status=status,  # type: ignore[arg-type]
            reason=None if status == "optimized" else "fallback",
        )

    def test_counts(self) -> None:
        outs = [
            self._outcome(cluster_id="c0", accepted=True),
            self._outcome(cluster_id="c1", accepted=False, dominance=False, regression_count=1),
            self._outcome(cluster_id="c2", status="fallback", accepted=False),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL, max_regression=1)
        assert prov["cluster_count"] == 3
        assert prov["optimized_count"] == 2
        assert prov["fallback_count"] == 1
        assert prov["accepted_count"] == 1
        assert prov["rejected_count"] == 2

    def test_dominance_vs_weighted_sum_breakdown(self) -> None:
        outs = [
            self._outcome(cluster_id="c0", accepted=True, dominance=True),
            self._outcome(cluster_id="c1", accepted=True, dominance=False, regression_count=1),
            self._outcome(cluster_id="c2", accepted=False, dominance=False, regression_count=3),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL, max_regression=1)
        assert prov["dominance_accepted_count"] == 1
        assert prov["weighted_sum_accepted_count"] == 1

    def test_regression_count_histogram(self) -> None:
        outs = [
            self._outcome(cluster_id="c0", regression_count=0, accepted=True),
            self._outcome(cluster_id="c1", regression_count=1, accepted=True, dominance=False),
            self._outcome(cluster_id="c2", regression_count=1, accepted=False, dominance=False),
            self._outcome(cluster_id="c3", regression_count=3, accepted=False, dominance=False),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL, max_regression=1)
        hist = prov["regression_count_histogram"]
        assert hist["0"] == 1
        assert hist["1"] == 2
        assert hist["3"] == 1

    def test_mean_delta_per_heuristic(self) -> None:
        outs = [
            self._outcome(cluster_id="c0", delta={"h1": -7, "h2": -4}),
            self._outcome(cluster_id="c1", delta={"h1": -5, "h2": -2}),
        ]
        prov = build_provenance(outs, failures=[], model=MODEL, max_regression=1)
        assert prov["mean_delta_per_heuristic"]["h1"] == -6.0
        assert prov["mean_delta_per_heuristic"]["h2"] == -3.0

    def test_transport_failures(self) -> None:
        prov = build_provenance(
            outcomes=[], failures=[("c99", ValueError("oops"))],
            model=MODEL, max_regression=1,
        )
        assert prov["transport_failure_count"] == 1


# =============================================================================
# CLI — main
# =============================================================================


class TestMain:
    def test_cli_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l8, "_resolve_repo_root", lambda: tmp_path)

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
        priority_path = data_dir / "priority.jsonl"
        _write_jsonl(
            priority_path,
            [_priority(cluster_id="cluster_02").model_dump(mode="json")],
        )
        decisions_path = data_dir / "decisions.jsonl"
        _write_jsonl(
            decisions_path,
            [_decision(cluster_id="cluster_02").model_dump(mode="json")],
        )
        output_path = data_dir / "iterations.jsonl"
        native_path = data_dir / "iterations.native.jsonl"
        artifacts_dir = data_dir / "arts"

        fake = FakeClient(default_response=_optimize_text())
        monkeypatch.setattr(l8, "Client", lambda **_k: fake)

        rc = main(
            [
                "--clusters", str(clusters_path),
                "--reconciled", str(reconciled_path),
                "--priority", str(priority_path),
                "--decisions", str(decisions_path),
                "--output", str(output_path),
                "--native-output", str(native_path),
                "--artifacts-dir", str(artifacts_dir),
                "--mode", "replay",
                "--run-id", "l8-test-run",
            ]
        )
        assert rc == 0
        # 1 cluster × 2 iterations = 2 rows
        iterations = [
            json.loads(line) for line in output_path.read_text().splitlines()
        ]
        assert len(iterations) == 2
        for row in iterations:
            OptimizationIteration.model_validate(row)
        # Provenance.
        prov = json.loads(
            output_path.with_suffix(".provenance.json").read_text()
        )
        assert prov["cluster_count"] == 1
        assert prov["optimized_count"] == 1

    def test_cli_empty_clusters(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        monkeypatch.setattr(l8, "_resolve_repo_root", lambda: tmp_path)

        (data_dir / "empty.jsonl").write_text("")
        (data_dir / "reconciled.jsonl").write_text("")
        (data_dir / "priority.jsonl").write_text("")
        (data_dir / "decisions.jsonl").write_text("")

        monkeypatch.setattr(l8, "Client", lambda **_k: FakeClient())
        rc = main(
            [
                "--clusters", str(data_dir / "empty.jsonl"),
                "--reconciled", str(data_dir / "reconciled.jsonl"),
                "--priority", str(data_dir / "priority.jsonl"),
                "--decisions", str(data_dir / "decisions.jsonl"),
                "--output", str(data_dir / "out.jsonl"),
                "--native-output", str(data_dir / "n.jsonl"),
                "--artifacts-dir", str(data_dir / "arts"),
                "--mode", "replay",
            ]
        )
        assert rc == 1
