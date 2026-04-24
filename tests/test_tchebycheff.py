"""Tests for ``auditable_design.evaluators.tchebycheff``.

Pure-Python evaluator; no I/O, no Claude. Covers the weighted L∞
scalarization's accept/reject logic and convergence path.
"""

from __future__ import annotations

import pytest

from auditable_design.evaluators.tchebycheff import (
    DEFAULT_MIN_IMPROVEMENT_PCT,
    TchebycheffVerdict,
    tchebycheff_cost,
    verdict,
)


# =============================================================================
# tchebycheff_cost
# =============================================================================


class TestTchebycheffCost:
    def test_max_weighted_product(self) -> None:
        scores = {"h1": 3, "h2": 5, "h3": 7}
        weights = {"h1": 9, "h2": 5, "h3": 3}
        # 3*9=27, 5*5=25, 7*3=21 → max 27 on h1
        cost, binding = tchebycheff_cost(scores, weights)
        assert cost == 27
        assert binding == "h1"

    def test_tie_break_alphabetical(self) -> None:
        """On ties, alphabetically first key wins for determinism."""
        scores = {"z_h": 3, "a_h": 3}
        weights = {"z_h": 5, "a_h": 5}
        # Both 15 — tie.
        cost, binding = tchebycheff_cost(scores, weights)
        assert cost == 15
        assert binding == "a_h"

    def test_empty_returns_zero_and_none(self) -> None:
        cost, binding = tchebycheff_cost({}, {})
        assert cost == 0
        assert binding is None

    def test_all_zero_scores(self) -> None:
        scores = {"h1": 0, "h2": 0}
        weights = {"h1": 9, "h2": 5}
        cost, binding = tchebycheff_cost(scores, weights)
        assert cost == 0
        # Binding still well-defined (alphabetical first).
        assert binding == "h1"

    def test_all_zero_weights(self) -> None:
        scores = {"h1": 9, "h2": 5}
        weights = {"h1": 0, "h2": 0}
        cost, binding = tchebycheff_cost(scores, weights)
        assert cost == 0
        assert binding == "h1"

    def test_mismatched_keys_raises(self) -> None:
        with pytest.raises(ValueError, match="keys"):
            tchebycheff_cost({"h1": 5}, {"h2": 5})


# =============================================================================
# verdict — accept paths
# =============================================================================


class TestVerdictAcceptPaths:
    def test_strict_improvement_all_heuristics(self) -> None:
        """Classic dominance case — accept easily."""
        parent = {"h1": 9, "h2": 7, "h3": 5}
        child = {"h1": 0, "h2": 0, "h3": 0}
        v = verdict(parent, child)
        assert v.accepted is True
        assert v.child_cost == 0
        # parent_cost = max(81, 49, 25) = 81
        assert v.parent_cost == 81
        assert v.improvement_pct == 100.0

    def test_worst_heuristic_reduced_others_unchanged(self) -> None:
        """Fix the high-severity heuristic, others stay."""
        parent = {"h1": 9, "h2": 3, "h3": 3}
        child = {"h1": 0, "h2": 3, "h3": 3}
        v = verdict(parent, child)
        # parent_cost = 81 (h1*h1). child_cost = max(0, 3*3, 3*3) = 9.
        assert v.parent_cost == 81
        assert v.child_cost == 9
        assert v.accepted is True

    def test_partial_reduction_above_threshold(self) -> None:
        """Worst residual drops just enough to clear 10% threshold."""
        parent = {"h1": 9}
        child = {"h1": 8}  # 8*9=72 vs threshold 81*0.9 = 72.9
        v = verdict(parent, child)
        # 72 < 72.9 → accept barely
        assert v.accepted is True
        assert v.binding_heuristic == "h1"

    def test_reports_binding_heuristic(self) -> None:
        parent = {"h1": 9, "h2": 3}
        child = {"h1": 0, "h2": 3}
        v = verdict(parent, child)
        # child_cost = max(9*0, 3*3) = 9 on h2
        assert v.binding_heuristic == "h2"

    def test_accept_reason_mentions_binding_heuristic(self) -> None:
        parent = {"h1": 9, "h2": 3}
        child = {"h1": 0, "h2": 0}
        v = verdict(parent, child)
        assert "Binding" in v.reason or "binding" in v.reason

    def test_delta_per_heuristic_matches(self) -> None:
        parent = {"h1": 9, "h2": 7}
        child = {"h1": 3, "h2": 0}
        v = verdict(parent, child)
        assert v.delta_per_heuristic == {"h1": -6, "h2": -7}


# =============================================================================
# verdict — reject paths
# =============================================================================


class TestVerdictRejectPaths:
    def test_no_change_insufficient(self) -> None:
        parent = {"h1": 9, "h2": 5}
        child = {"h1": 9, "h2": 5}
        v = verdict(parent, child)
        assert v.accepted is False
        assert v.improvement_pct == 0.0
        assert v.regression_count == 0

    def test_pure_regression_reject(self) -> None:
        parent = {"h1": 3, "h2": 3}
        child = {"h1": 7, "h2": 7}
        v = verdict(parent, child)
        # parent_cost = 9, child_cost = max(3*7, 3*7) = 21
        assert v.accepted is False
        assert v.child_cost > v.parent_cost
        assert v.regression_count == 2

    def test_tiny_improvement_below_threshold(self) -> None:
        """9 → 8.5 (which rounds to 9 in ints, but suppose 9 → 9 with
        tiny residual fix — improvement must clear 10%)."""
        parent = {"h1": 9, "h2": 3}
        child = {"h1": 9, "h2": 0}  # worst still 9*9=81; only h2 improves
        v = verdict(parent, child)
        # parent_cost=81, child_cost=max(81,0)=81 → 0% improvement.
        assert v.accepted is False
        assert v.child_cost == 81

    def test_regression_on_high_weight_dominates(self) -> None:
        """The key property: regression on a prior-severity-9
        heuristic cannot be hidden by improvements elsewhere."""
        parent = {"h1": 9, "h2": 9, "h3": 9}
        # Child improves h1 and h2 fully, but h3 regresses 9→10.
        child = {"h1": 0, "h2": 0, "h3": 10}
        v = verdict(parent, child)
        # parent_cost = 81, child_cost = 9*10 = 90 → worse.
        assert v.accepted is False
        assert v.child_cost > v.parent_cost
        assert v.binding_heuristic == "h3"

    def test_tradeoff_that_weighted_sum_would_accept(self) -> None:
        """Weighted sum would LOVE this: total severity drops a lot.
        Tchebycheff rejects because worst residual got worse."""
        parent = {"h1": 9, "h2": 9, "h3": 5}
        # Child fixes h1, h2 but h3 climbs 5 → 10.
        child = {"h1": 0, "h2": 0, "h3": 10}
        v = verdict(parent, child)
        # parent_cost = max(81, 81, 25) = 81.
        # child_cost = max(0, 0, 5*10) = 50.
        # 50 < 81*0.9=72.9 → actually Tchebycheff ACCEPTS here.
        # Total severity dropped 23→10, and binding residual
        # 81→50 also dropped. Both methods agree.
        assert v.accepted is True
        assert v.binding_heuristic == "h3"

    def test_regression_on_high_without_compensating_fix(self) -> None:
        """No compensating fix elsewhere → reject cleanly."""
        parent = {"h1": 9, "h2": 3}
        # Regression on h1 (prior severity 9), h2 unchanged.
        child = {"h1": 10, "h2": 3}
        v = verdict(parent, child)
        # parent_cost = 81, child_cost = 90. Reject.
        assert v.accepted is False

    def test_reject_reason_includes_cost_numbers(self) -> None:
        parent = {"h1": 9}
        child = {"h1": 9}
        v = verdict(parent, child)
        assert "81" in v.reason  # parent_cost in message
        # No-op is reported as no-net-improvement.
        assert "no net improvement" in v.reason.lower() or "below" in v.reason.lower()


# =============================================================================
# verdict — converged path
# =============================================================================


class TestVerdictConverged:
    def test_parent_all_zero_converged(self) -> None:
        parent = {"h1": 0, "h2": 0}
        child = {"h1": 0, "h2": 0}
        v = verdict(parent, child)
        assert v.converged is True
        assert v.accepted is False
        assert v.parent_cost == 0
        assert "converged" in v.reason.lower()

    def test_empty_vectors_converged(self) -> None:
        v = verdict({}, {})
        assert v.converged is True
        assert v.accepted is False
        assert v.parent_cost == 0

    def test_converged_even_if_child_nonzero(self) -> None:
        """If parent cost is 0, any child regression is still
        reported as converged (the loop should stop regardless —
        the parent is our best result)."""
        parent = {"h1": 0}
        child = {"h1": 5}  # full regression
        v = verdict(parent, child)
        assert v.converged is True
        assert v.accepted is False


# =============================================================================
# verdict — min_improvement_pct parameter
# =============================================================================


class TestMinImprovement:
    def test_default_is_ten_pct(self) -> None:
        assert DEFAULT_MIN_IMPROVEMENT_PCT == 10.0

    def test_stricter_threshold_rejects_marginal(self) -> None:
        parent = {"h1": 10}
        child = {"h1": 8}  # 80 < 100*0.9=90 → default accepts
        v_default = verdict(parent, child)
        assert v_default.accepted is True
        # With 25% threshold: need child_cost < 75; 80 ≥ 75 → reject.
        v_strict = verdict(parent, child, min_improvement_pct=25.0)
        assert v_strict.accepted is False

    def test_looser_threshold_accepts_marginal(self) -> None:
        parent = {"h1": 10}
        child = {"h1": 9}  # 90 vs threshold 100*0.9=90 → default rejects
        v_default = verdict(parent, child)
        assert v_default.accepted is False
        # With 5% threshold: need child_cost < 95; 90 < 95 → accept.
        v_loose = verdict(parent, child, min_improvement_pct=5.0)
        assert v_loose.accepted is True


# =============================================================================
# verdict — type + validation
# =============================================================================


class TestVerdictTypes:
    def test_returns_tchebycheff_verdict_instance(self) -> None:
        v = verdict({"h1": 5}, {"h1": 0})
        assert isinstance(v, TchebycheffVerdict)

    def test_mismatched_keys_raises(self) -> None:
        with pytest.raises(ValueError, match="keys"):
            verdict({"h1": 5}, {"h2": 5})
