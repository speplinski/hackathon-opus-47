"""Tests for ``auditable_design.layers.l8_optimize_loop``.

Covers design-tweak parser, verifier dispatch, and the multi-round
orchestrator's termination conditions via a replay-mode FakeClient.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from auditable_design.claude_client import ClaudeResponse
from auditable_design.evaluators.pareto import ParetoVerdict
from auditable_design.evaluators.tchebycheff import TchebycheffVerdict
from auditable_design.layers import l8_optimize_loop as loop_mod
from auditable_design.layers.l8_optimize_loop import (
    CONVERGENCE_SEVERITY_THRESHOLD,
    DEFAULT_MAX_ITERATIONS,
    DEFAULT_STALL_LIMIT,
    LoopOutcome,
    LoopVerdict,
    TweakParseError,
    apply_verifier,
    build_tweak_user_message,
    parse_tweak_response,
    run_loop,
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
# Test helpers / fixtures
# =============================================================================


@dataclass
class FakeClient:
    """Routes calls by SKILL_ID: different scripted responses for
    design-tweak vs design-optimize. Scripted-by-queue: each skill
    has a FIFO of responses; raising if empty."""

    tweak_responses: list[str] = field(default_factory=list)
    reaudit_responses: list[str] = field(default_factory=list)
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
                "skill_id": skill_id,
                "model": model,
                "user_excerpt": user[:120],
            }
        )
        if skill_id == "design-tweak":
            if not self.tweak_responses:
                raise RuntimeError("FakeClient: out of tweak responses")
            text = self.tweak_responses.pop(0)
        elif skill_id == "design-optimize":
            if not self.reaudit_responses:
                raise RuntimeError("FakeClient: out of reaudit responses")
            text = self.reaudit_responses.pop(0)
        else:
            raise RuntimeError(f"FakeClient: unknown skill_id {skill_id!r}")

        return ClaudeResponse(
            call_id="fake",
            key_hash="0" * 64,
            skill_id=skill_id,
            skill_hash=skill_hash,
            model=model,
            temperature=float(temperature),
            prompt=f"SYSTEM:\t{system[:40]}\tUSER:\t{user[:40]}",
            response=text,
            input_tokens=500,
            output_tokens=200,
            cost_usd=0.0,
            timestamp="2026-04-24T12:00:00+00:00",
            cache_hit=False,
            elapsed_s=0.0,
        )


def _cluster() -> InsightCluster:
    return InsightCluster(
        cluster_id="cluster_02",
        label="Streak loss framing",
        member_review_ids=["r1", "r2"],
        centroid_vector_ref="l3_centroids.npy#0",
        representative_quotes=["manipulative", "streak saver"],
        ui_context="Duolingo mid-lesson modal",
    )


def _reconciled(
    slugs: list[tuple[str, int]] | None = None,
) -> ReconciledVerdict:
    spec = slugs or [
        ("modal_excise", 9),
        ("competing_calls_to_action", 7),
        ("loss_framing", 5),
        ("channel_gap", 3),
    ]
    return ReconciledVerdict(
        cluster_id="cluster_02",
        ranked_violations=[
            HeuristicViolation(
                heuristic=slug,
                violation=f"violation of {slug}",
                severity=sev,
                evidence_review_ids=[],
                reasoning=f"reason {slug}",
            )
            for slug, sev in spec
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


def _priority() -> PriorityScore:
    dims = {"severity": 9, "reach": 9, "persistence": 8,
            "business_impact": 9, "cognitive_cost": 9}
    w = {k: 0.2 for k in dims}
    return PriorityScore(
        cluster_id="cluster_02",
        dimensions=dims,
        meta_weights=w,
        weighted_total=sum(dims[k] * w[k] for k in dims),
        validation_passes=2,
        validation_delta=0.0,
    )


def _decision() -> DesignDecision:
    return DesignDecision(
        decision_id="decision__cluster_02__1",
        principle_id="principle__cluster_02",
        description="Remove mid-lesson modal.",
        before_snapshot="Modal fires mid-lesson. Full viewport.",
        after_snapshot=(
            "No mid-lesson modal; lesson completes; streak surface "
            "appears on lesson-complete screen with equal paths."
        ),
        resolves_heuristics=["modal_excise"],
    )


def _iter0(scores: dict[str, int]) -> OptimizationIteration:
    from datetime import UTC, datetime
    return OptimizationIteration(
        iteration_id="iteration__cluster_02__00",
        run_id="fixture",
        iteration_index=0,
        parent_iteration_id=None,
        design_artifact_ref="/tmp/iter00.md",
        scores={"reconciled": scores},
        reasoning="Baseline from L5 reconciled.",
        accepted=True,
        regression_reason=None,
        delta_per_heuristic={},
        informing_review_ids=["r1"],
        recorded_at=datetime.now(UTC),
    )


def _iter1(
    parent_id: str,
    scores: dict[str, int],
    *,
    accepted: bool = True,
) -> OptimizationIteration:
    from datetime import UTC, datetime
    return OptimizationIteration(
        iteration_id="iteration__cluster_02__01",
        run_id="fixture",
        iteration_index=1,
        parent_iteration_id=parent_id,
        design_artifact_ref="/tmp/iter01.md",
        scores={"reconciled": scores},
        reasoning="L7 re-audit outcome.",
        accepted=accepted,
        regression_reason=None if accepted else "some reject reason",
        delta_per_heuristic={},
        informing_review_ids=["r1"],
        recorded_at=datetime.now(UTC),
    )


def _tweak_payload(
    *,
    snapshot_words: int = 100,
    addresses: list[str] | None = None,
    preserves: list[str] | None = None,
    reasoning: str = "Minimal tweak focusing on binding residual.",
) -> str:
    return json.dumps(
        {
            "new_snapshot": " ".join(["word"] * snapshot_words),
            "addresses_heuristics": (
                addresses if addresses is not None else ["loss_framing"]
            ),
            "preserves_heuristics": (
                preserves if preserves is not None else ["modal_excise"]
            ),
            "reasoning": reasoning,
        }
    )


def _reaudit_payload(
    scored: dict[str, int],
    *,
    reasoning: str = "re-audit",
) -> str:
    return json.dumps({"scored_heuristics": scored, "reasoning": reasoning})


# =============================================================================
# parse_tweak_response
# =============================================================================


class TestParseTweak:
    CURRENT = {"h1": 5, "h2": 3, "h3": 0, "h4": 0}

    def test_valid_payload(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": ["h1", "h2"],
            "preserves_heuristics": ["h3", "h4"],
            "reasoning": "tweaking",
        }
        out = parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)
        assert out["addresses_heuristics"] == ["h1", "h2"]
        assert out["preserves_heuristics"] == ["h3", "h4"]

    def test_missing_top_level_key(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": ["h1"],
            # preserves missing
            "reasoning": "oops",
        }
        with pytest.raises(TweakParseError, match="missing"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_extra_top_level_key(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": ["h1"],
            "preserves_heuristics": ["h3"],
            "reasoning": "ok",
            "rogue": 42,
        }
        with pytest.raises(TweakParseError, match="unexpected"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_snapshot_too_short(self) -> None:
        data = {
            "new_snapshot": "short",  # 1 word
            "addresses_heuristics": ["h1"],
            "preserves_heuristics": ["h3"],
            "reasoning": "x",
        }
        with pytest.raises(TweakParseError, match="outside"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_snapshot_too_long(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 500),
            "addresses_heuristics": ["h1"],
            "preserves_heuristics": ["h3"],
            "reasoning": "x",
        }
        with pytest.raises(TweakParseError, match="outside"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_addresses_contains_non_residual(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": ["h3"],  # h3 is at 0 — not a residual
            "preserves_heuristics": ["h4"],
            "reasoning": "x",
        }
        with pytest.raises(TweakParseError, match="severity 0"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_preserves_contains_non_zero(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": ["h1"],
            "preserves_heuristics": ["h2"],  # h2 is residual, not resolved
            "reasoning": "x",
        }
        with pytest.raises(TweakParseError, match="NOT at severity 0"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_addresses_unknown_slug(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": ["ghost"],
            "preserves_heuristics": ["h3"],
            "reasoning": "x",
        }
        with pytest.raises(TweakParseError, match="unknown slugs"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_preserves_unknown_slug(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": ["h1"],
            "preserves_heuristics": ["ghost"],
            "reasoning": "x",
        }
        with pytest.raises(TweakParseError, match="unknown slugs"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_malformed_json(self) -> None:
        with pytest.raises(TweakParseError, match="JSON"):
            parse_tweak_response(
                "{not really json", current_scores=self.CURRENT
            )

    def test_empty_addresses_rejected(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": [],
            "preserves_heuristics": ["h3"],
            "reasoning": "x",
        }
        with pytest.raises(TweakParseError, match="non-empty"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)

    def test_non_string_reasoning(self) -> None:
        data = {
            "new_snapshot": " ".join(["x"] * 80),
            "addresses_heuristics": ["h1"],
            "preserves_heuristics": ["h3"],
            "reasoning": 42,
        }
        with pytest.raises(TweakParseError, match="reasoning"):
            parse_tweak_response(json.dumps(data), current_scores=self.CURRENT)


# =============================================================================
# apply_verifier
# =============================================================================


class TestApplyVerifier:
    def test_pareto_dispatch(self) -> None:
        v = apply_verifier(
            {"h1": 9, "h2": 5}, {"h1": 0, "h2": 0}, verifier="pareto"
        )
        assert v.verifier == "pareto"
        assert v.accepted is True
        assert isinstance(v.raw, ParetoVerdict)

    def test_tchebycheff_dispatch(self) -> None:
        v = apply_verifier(
            {"h1": 9, "h2": 5}, {"h1": 0, "h2": 0}, verifier="tchebycheff"
        )
        assert v.verifier == "tchebycheff"
        assert v.accepted is True
        assert isinstance(v.raw, TchebycheffVerdict)

    def test_pareto_convergence_synthetic(self) -> None:
        """Pareto has no native converged flag; the loop shim marks
        all-zero parent as converged for termination parity."""
        v = apply_verifier(
            {"h1": 0, "h2": 0}, {"h1": 0, "h2": 0}, verifier="pareto"
        )
        assert v.converged is True

    def test_tchebycheff_converged_passthrough(self) -> None:
        v = apply_verifier(
            {"h1": 0}, {"h1": 0}, verifier="tchebycheff"
        )
        assert v.converged is True

    def test_unknown_verifier_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown verifier"):
            apply_verifier({"h": 5}, {"h": 0}, verifier="bogus")  # type: ignore[arg-type]


# =============================================================================
# build_tweak_user_message
# =============================================================================


class TestBuildTweakUserMessage:
    def test_includes_all_sections(self) -> None:
        msg = build_tweak_user_message(
            _cluster(),
            _reconciled(),
            current_snapshot="current surface",
            current_scores={"modal_excise": 0, "loss_framing": 5},
            verdict_reason="binding heuristic loss_framing",
        )
        assert "<current_snapshot>" in msg
        assert "<current_scores>" in msg
        assert "<verdict_reason>" in msg
        assert "<baseline_heuristics>" in msg
        assert "loss_framing: 5" in msg

    def test_escapes_xml(self) -> None:
        cluster = _cluster()
        cluster = cluster.model_copy(update={"label": "a < b & c > d"})
        msg = build_tweak_user_message(
            cluster,
            _reconciled(),
            current_snapshot="s",
            current_scores={"modal_excise": 0},
            verdict_reason="r",
        )
        assert "&lt;" in msg
        assert "&amp;" in msg
        assert "&gt;" in msg


# =============================================================================
# run_loop — termination paths
# =============================================================================


class TestRunLoopTermination:
    def _setup(
        self,
        *,
        iter1_scores: dict[str, int],
        max_iterations: int = 5,
    ) -> tuple[FakeClient, list[OptimizationIteration]]:
        baseline_scores = {
            "modal_excise": 9,
            "competing_calls_to_action": 7,
            "loss_framing": 5,
            "channel_gap": 3,
        }
        iter0 = _iter0(baseline_scores)
        iter1 = _iter1(iter0.iteration_id, iter1_scores)
        return FakeClient(), [iter0, iter1]

    async def _drive(
        self,
        client: FakeClient,
        existing: list[OptimizationIteration],
        *,
        verifier: str = "tchebycheff",
        max_iterations: int = 5,
        stall_limit: int = 2,
        severity_threshold: int = 5,
        tmp_path: Path,
    ) -> LoopOutcome:
        return await run_loop(
            cluster=_cluster(),
            reconciled=_reconciled(),
            decision=_decision(),
            priority=_priority(),
            existing_iterations=existing,
            client=client,  # type: ignore[arg-type]
            verifier=verifier,  # type: ignore[arg-type]
            max_iterations=max_iterations,
            stall_limit=stall_limit,
            severity_threshold=severity_threshold,
            run_id="test",
            artifacts_dir=tmp_path / "artifacts",
        )

    def test_severity_threshold_halts_without_claude(
        self, tmp_path: Path
    ) -> None:
        """Iter 1 already at sum 5 — loop stops before any tweak call."""
        client, existing = self._setup(
            iter1_scores={"modal_excise": 0,
                          "competing_calls_to_action": 0,
                          "loss_framing": 5,
                          "channel_gap": 0}
        )
        # sum = 5, equal to threshold → halt (<=)
        outcome = asyncio.run(
            self._drive(client, existing, tmp_path=tmp_path)
        )
        assert outcome.termination_reason == "severity_threshold"
        assert len(outcome.new_iterations) == 0
        assert len(client.calls) == 0

    def test_max_iterations_limit(self, tmp_path: Path) -> None:
        """Each round accepts; loop caps at max_iterations.

        Progression chosen so Tchebycheff accepts each child strictly:
        binding residual drops monotonically.
        """
        client, existing = self._setup(
            iter1_scores={"modal_excise": 7,
                          "competing_calls_to_action": 5,
                          "loss_framing": 5,
                          "channel_gap": 3}
        )
        client.tweak_responses = [
            _tweak_payload(
                addresses=["modal_excise"], preserves=[]
            ),
            _tweak_payload(
                addresses=["competing_calls_to_action", "loss_framing"],
                preserves=["modal_excise"],
            ),
            _tweak_payload(
                addresses=["channel_gap", "loss_framing"],
                preserves=["modal_excise", "competing_calls_to_action"],
            ),
        ]
        # Iter 2 reaudit: big drop on modal_excise → 0.
        # Iter 3 reaudit: loss_framing 5→3, ccta 5→0.
        # Iter 4 reaudit: channel_gap 3→0, loss_framing 3→0.
        client.reaudit_responses = [
            _reaudit_payload({
                "modal_excise": 0,
                "competing_calls_to_action": 5,
                "loss_framing": 5,
                "channel_gap": 3,
            }),
            _reaudit_payload({
                "modal_excise": 0,
                "competing_calls_to_action": 0,
                "loss_framing": 3,
                "channel_gap": 3,
            }),
            _reaudit_payload({
                "modal_excise": 0,
                "competing_calls_to_action": 0,
                "loss_framing": 0,
                "channel_gap": 0,
            }),
        ]
        outcome = asyncio.run(
            self._drive(
                client, existing, max_iterations=5,
                severity_threshold=-1,  # disable threshold
                tmp_path=tmp_path,
            )
        )
        # 5 max iterations, 2 pre-existing → 3 new iterations.
        # After iter 4 the parent is all-zero, so the loop exits
        # 'converged' on the pre-loop check of iter 5, not max.
        assert len(outcome.new_iterations) == 3
        assert outcome.termination_reason in {"max_iterations", "converged"}
        assert all(it.accepted for it in outcome.new_iterations)

    def test_stall_two_consecutive_rejects(self, tmp_path: Path) -> None:
        client, existing = self._setup(
            iter1_scores={"modal_excise": 9,
                          "competing_calls_to_action": 7,
                          "loss_framing": 5,
                          "channel_gap": 3}
        )
        client.tweak_responses = [
            _tweak_payload(addresses=["modal_excise"], preserves=[]),
            _tweak_payload(addresses=["modal_excise"], preserves=[]),
        ]
        # Both re-audits: child identical to parent → reject (no improvement).
        same = {"modal_excise": 9,
                "competing_calls_to_action": 7,
                "loss_framing": 5,
                "channel_gap": 3}
        client.reaudit_responses = [
            _reaudit_payload(same),
            _reaudit_payload(same),
        ]
        outcome = asyncio.run(
            self._drive(
                client, existing, max_iterations=10,
                stall_limit=2, severity_threshold=0, tmp_path=tmp_path,
            )
        )
        assert outcome.termination_reason == "stall"
        assert len(outcome.new_iterations) == 2
        assert all(not it.accepted for it in outcome.new_iterations)

    def test_tchebycheff_converged(self, tmp_path: Path) -> None:
        """Reaudit returns all zeros → Tchebycheff converged."""
        client, existing = self._setup(
            iter1_scores={"modal_excise": 9,
                          "competing_calls_to_action": 7,
                          "loss_framing": 5,
                          "channel_gap": 3}
        )
        client.tweak_responses = [
            _tweak_payload(addresses=["modal_excise"], preserves=[]),
            _tweak_payload(  # Should not be called, but just in case.
                addresses=["modal_excise"], preserves=[]
            ),
        ]
        client.reaudit_responses = [
            _reaudit_payload({
                "modal_excise": 0,
                "competing_calls_to_action": 0,
                "loss_framing": 0,
                "channel_gap": 0,
            }),
            _reaudit_payload({
                "modal_excise": 0,
                "competing_calls_to_action": 0,
                "loss_framing": 0,
                "channel_gap": 0,
            }),
        ]
        outcome = asyncio.run(
            self._drive(
                client, existing, max_iterations=10,
                severity_threshold=-1, tmp_path=tmp_path,
            )
        )
        # Sum-threshold=-1 disables that check, so only converged/stall/max
        # can fire. After the accepted all-zero iter, next round sees parent
        # sum=0 and threshold=-1 won't halt, but apply_verifier will mark
        # converged on the NEXT loop iteration from parent=[0,0,0,0].
        # The iter we just accepted has sum=0 → next loop tick,
        # severity_threshold=-1 doesn't fire, but tcheb fires converged.
        # So termination is 'converged'.
        assert outcome.termination_reason == "converged"

    def test_tweak_parse_failure_halts(self, tmp_path: Path) -> None:
        client, existing = self._setup(
            iter1_scores={"modal_excise": 9,
                          "competing_calls_to_action": 7,
                          "loss_framing": 5,
                          "channel_gap": 3}
        )
        client.tweak_responses = [
            "not valid json at all",
        ]
        client.reaudit_responses = []
        outcome = asyncio.run(
            self._drive(
                client, existing, severity_threshold=0, tmp_path=tmp_path,
            )
        )
        assert outcome.termination_reason == "tweak_parse_fail"
        assert len(outcome.new_iterations) == 0

    def test_reaudit_parse_failure_halts(self, tmp_path: Path) -> None:
        client, existing = self._setup(
            iter1_scores={"modal_excise": 9,
                          "competing_calls_to_action": 7,
                          "loss_framing": 5,
                          "channel_gap": 3}
        )
        client.tweak_responses = [
            _tweak_payload(addresses=["modal_excise"], preserves=[]),
        ]
        client.reaudit_responses = [
            '{"bogus": true}',
        ]
        outcome = asyncio.run(
            self._drive(
                client, existing, severity_threshold=0, tmp_path=tmp_path,
            )
        )
        assert outcome.termination_reason == "reaudit_parse_fail"

    def test_verifier_switch_pareto(self, tmp_path: Path) -> None:
        """Pareto rejects any regression beyond max_regression, even
        if weighted cost improves — this test sets up a regression
        that Pareto rejects."""
        client, existing = self._setup(
            iter1_scores={"modal_excise": 9,
                          "competing_calls_to_action": 7,
                          "loss_framing": 5,
                          "channel_gap": 3}
        )
        client.tweak_responses = [
            _tweak_payload(addresses=["modal_excise"], preserves=[]),
            _tweak_payload(addresses=["modal_excise"], preserves=[]),
        ]
        # child regresses 2 heuristics (max_regression=1 default) → reject
        client.reaudit_responses = [
            _reaudit_payload({
                "modal_excise": 0,
                "competing_calls_to_action": 9,  # regression 7→9
                "loss_framing": 9,  # regression 5→9
                "channel_gap": 3,
            }),
            _reaudit_payload({
                "modal_excise": 0,
                "competing_calls_to_action": 9,
                "loss_framing": 9,
                "channel_gap": 3,
            }),
        ]
        outcome = asyncio.run(
            self._drive(
                client, existing, verifier="pareto", stall_limit=2,
                severity_threshold=0, tmp_path=tmp_path,
            )
        )
        # 2 reject → stall.
        assert outcome.termination_reason == "stall"
        assert all(not it.accepted for it in outcome.new_iterations)


# =============================================================================
# run_loop — parent advance, ids, artifacts
# =============================================================================


class TestRunLoopMechanics:
    async def _one_round(self, tmp_path: Path) -> LoopOutcome:
        baseline = {
            "modal_excise": 9,
            "competing_calls_to_action": 7,
            "loss_framing": 5,
            "channel_gap": 3,
        }
        iter0 = _iter0(baseline)
        iter1 = _iter1(iter0.iteration_id, baseline)  # no improvement yet
        client = FakeClient()
        client.tweak_responses = [
            _tweak_payload(addresses=["modal_excise"], preserves=[]),
        ]
        client.reaudit_responses = [
            _reaudit_payload({
                "modal_excise": 0,
                "competing_calls_to_action": 7,
                "loss_framing": 5,
                "channel_gap": 3,
            }),
        ]
        return await run_loop(
            cluster=_cluster(),
            reconciled=_reconciled(),
            decision=_decision(),
            priority=_priority(),
            existing_iterations=[iter0, iter1],
            client=client,  # type: ignore[arg-type]
            verifier="tchebycheff",
            max_iterations=3,  # allows only one new iter
            stall_limit=2,
            severity_threshold=0,
            run_id="mech",
            artifacts_dir=tmp_path / "arts",
        )

    def test_iteration_id_shape(self, tmp_path: Path) -> None:
        outcome = asyncio.run(self._one_round(tmp_path))
        assert len(outcome.new_iterations) == 1
        assert outcome.new_iterations[0].iteration_id == "iteration__cluster_02__02"

    def test_iteration_index_continues(self, tmp_path: Path) -> None:
        outcome = asyncio.run(self._one_round(tmp_path))
        assert outcome.new_iterations[0].iteration_index == 2

    def test_parent_pointer(self, tmp_path: Path) -> None:
        outcome = asyncio.run(self._one_round(tmp_path))
        # Parent is iter 1 (most recent accepted in existing).
        assert outcome.new_iterations[0].parent_iteration_id == "iteration__cluster_02__01"

    def test_artifact_written(self, tmp_path: Path) -> None:
        outcome = asyncio.run(self._one_round(tmp_path))
        path = Path(outcome.new_iterations[0].design_artifact_ref)
        assert path.exists()
        body = path.read_text(encoding="utf-8")
        assert "## new_snapshot" in body
        assert "## Re-audit severities" in body
        assert "## Verifier verdict" in body

    def test_native_payload_count_matches(self, tmp_path: Path) -> None:
        outcome = asyncio.run(self._one_round(tmp_path))
        assert len(outcome.native_payloads) == len(outcome.new_iterations)

    def test_final_parent_id_reports_latest_accepted(self, tmp_path: Path) -> None:
        outcome = asyncio.run(self._one_round(tmp_path))
        # Iter 2 accepted → final parent is iter 2.
        assert outcome.final_parent_id == "iteration__cluster_02__02"


# =============================================================================
# Helper functions
# =============================================================================


class TestHelpers:
    def test_flat_scores_single_bucket(self) -> None:
        nested = {"reconciled": {"h1": 5, "h2": 3}}
        flat = loop_mod._flat_scores(nested)
        assert flat == {"h1": 5, "h2": 3}

    def test_severity_sum_sums_across_buckets(self) -> None:
        nested = {"reconciled": {"h1": 5, "h2": 3}}
        assert loop_mod._severity_sum(nested) == 8

    def test_latest_accepted_picks_most_recent(self) -> None:
        baseline = {
            "modal_excise": 9, "competing_calls_to_action": 7,
            "loss_framing": 5, "channel_gap": 3,
        }
        i0 = _iter0(baseline)
        i1 = _iter1(i0.iteration_id, baseline, accepted=False)
        result = loop_mod._latest_accepted([i0, i1])
        assert result is i0

    def test_latest_accepted_none_when_all_rejected(self) -> None:
        baseline = {
            "modal_excise": 9, "competing_calls_to_action": 7,
            "loss_framing": 5, "channel_gap": 3,
        }
        i0 = _iter0(baseline)
        # Force i0 to rejected for this synthetic case.
        from datetime import UTC, datetime
        i0_rejected = OptimizationIteration(
            iteration_id="iteration__cluster_02__00",
            run_id="f",
            iteration_index=0,
            parent_iteration_id=None,
            design_artifact_ref="/tmp/x.md",
            scores={"reconciled": baseline},
            reasoning="r",
            accepted=False,
            regression_reason="rejected",
            delta_per_heuristic={},
            informing_review_ids=["r1"],
            recorded_at=datetime.now(UTC),
        )
        result = loop_mod._latest_accepted([i0_rejected])
        assert result is None

    def test_cluster_id_of(self) -> None:
        baseline = {"h1": 5}
        i = _iter0(baseline)
        assert loop_mod._cluster_id_of(i) == "cluster_02"

    def test_snapshot_of_iter0_uses_before(self) -> None:
        i = _iter0({"h1": 5})
        d = _decision()
        assert loop_mod._snapshot_of(i, d) == d.before_snapshot

    def test_snapshot_of_iter1_uses_after(self) -> None:
        baseline = {"h1": 5}
        i0 = _iter0(baseline)
        i1 = _iter1(i0.iteration_id, baseline)
        d = _decision()
        assert loop_mod._snapshot_of(i1, d) == d.after_snapshot

    def test_child_iteration_id_formatted(self) -> None:
        assert loop_mod._child_iteration_id("cluster_02", 7) == "iteration__cluster_02__07"


# =============================================================================
# build_provenance
# =============================================================================


class TestProvenance:
    def test_provenance_counts_accepted_rejected(self, tmp_path: Path) -> None:
        baseline = {
            "modal_excise": 9, "competing_calls_to_action": 7,
            "loss_framing": 5, "channel_gap": 3,
        }
        iter0 = _iter0(baseline)
        iter1 = _iter1(iter0.iteration_id, baseline, accepted=False)
        # Synthesize an accepted + rejected new iteration for counts.
        from datetime import UTC, datetime
        acc = OptimizationIteration(
            iteration_id="iteration__cluster_02__02",
            run_id="t",
            iteration_index=2,
            parent_iteration_id="iteration__cluster_02__01",
            design_artifact_ref="/tmp/a.md",
            scores={"reconciled": {"modal_excise": 0, "competing_calls_to_action": 0, "loss_framing": 0, "channel_gap": 0}},
            reasoning="ok",
            accepted=True,
            regression_reason=None,
            delta_per_heuristic={},
            informing_review_ids=["r1"],
            recorded_at=datetime.now(UTC),
        )
        rej = OptimizationIteration(
            iteration_id="iteration__cluster_02__03",
            run_id="t",
            iteration_index=3,
            parent_iteration_id="iteration__cluster_02__02",
            design_artifact_ref="/tmp/b.md",
            scores={"reconciled": {"modal_excise": 0, "competing_calls_to_action": 0, "loss_framing": 0, "channel_gap": 0}},
            reasoning="r",
            accepted=False,
            regression_reason="x",
            delta_per_heuristic={},
            informing_review_ids=["r1"],
            recorded_at=datetime.now(UTC),
        )
        outcome = LoopOutcome(
            cluster_id="cluster_02",
            new_iterations=[acc, rej],
            verdicts=[],
            native_payloads=[],
            termination_reason="stall",
            final_parent_id="iteration__cluster_02__02",
        )
        prov = loop_mod.build_provenance(
            outcome,
            verifier="tchebycheff",
            tweak_model="m",
            reaudit_model="m",
            tweak_skill_id="design-tweak",
            reaudit_skill_id="design-optimize",
            tweak_skill_hash_value="h1",
            reaudit_skill_hash_value="h2",
            run_id="t",
            max_iterations=5,
            stall_limit=2,
            severity_threshold=5,
            max_regression=1,
            min_improvement_pct=10.0,
        )
        assert prov["accepted_count"] == 1
        assert prov["rejected_count"] == 1
        assert prov["new_iteration_count"] == 2
        assert prov["termination_reason"] == "stall"
        assert prov["final_parent_id"] == "iteration__cluster_02__02"
        assert prov["verifier"] == "tchebycheff"


# =============================================================================
# Constants sanity
# =============================================================================


class TestConstants:
    def test_defaults(self) -> None:
        assert DEFAULT_MAX_ITERATIONS >= 3
        assert DEFAULT_STALL_LIMIT == 2
        assert CONVERGENCE_SEVERITY_THRESHOLD >= 0
