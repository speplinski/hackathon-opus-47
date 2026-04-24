"""Tests for ``auditable_design.evaluators.pareto``.

Pure-Python evaluator; no I/O, no Claude. Fast unit tests covering
dominance, weighted_sum, and verdict (accept/reject) paths.
"""

from __future__ import annotations

import pytest

from auditable_design.evaluators.pareto import (
    DEFAULT_MAX_REGRESSION,
    ParetoVerdict,
    dominance,
    verdict,
    weighted_sum,
)


# =============================================================================
# dominance
# =============================================================================


class TestDominance:
    def test_strict_improvement_all_heuristics(self) -> None:
        parent = {"h1": 7, "h2": 9, "h3": 5}
        child = {"h1": 0, "h2": 3, "h3": 0}
        assert dominance(parent, child) is True

    def test_improvement_on_one_heuristic_others_equal(self) -> None:
        parent = {"h1": 7, "h2": 5, "h3": 3}
        child = {"h1": 5, "h2": 5, "h3": 3}
        assert dominance(parent, child) is True

    def test_all_equal_is_not_dominance(self) -> None:
        parent = {"h1": 5, "h2": 5, "h3": 5}
        child = {"h1": 5, "h2": 5, "h3": 5}
        assert dominance(parent, child) is False

    def test_one_regression_breaks_dominance(self) -> None:
        parent = {"h1": 7, "h2": 5, "h3": 3}
        child = {"h1": 0, "h2": 7, "h3": 0}  # h2 regresses 5→7
        assert dominance(parent, child) is False

    def test_empty_vectors_not_dominance(self) -> None:
        """No heuristics → no strict_better possible → not dominance."""
        assert dominance({}, {}) is False

    def test_mismatched_keys_raises(self) -> None:
        parent = {"h1": 5}
        child = {"h2": 3}
        with pytest.raises(ValueError, match="keys"):
            dominance(parent, child)

    def test_subset_keys_raises(self) -> None:
        parent = {"h1": 5, "h2": 7}
        child = {"h1": 0}  # missing h2
        with pytest.raises(ValueError, match="keys"):
            dominance(parent, child)


# =============================================================================
# weighted_sum
# =============================================================================


class TestWeightedSum:
    def test_sum_of_severities(self) -> None:
        assert weighted_sum({"h1": 7, "h2": 9, "h3": 5}) == 21

    def test_zero_severity(self) -> None:
        assert weighted_sum({"h1": 0, "h2": 0}) == 0

    def test_empty_dict(self) -> None:
        assert weighted_sum({}) == 0

    def test_single_heuristic(self) -> None:
        assert weighted_sum({"only": 9}) == 9


# =============================================================================
# verdict — accept paths
# =============================================================================


class TestVerdictAcceptPaths:
    def test_dominance_accept(self) -> None:
        parent = {"h1": 7, "h2": 9, "h3": 5}
        child = {"h1": 0, "h2": 3, "h3": 0}
        v = verdict(parent, child)
        assert v.accepted is True
        assert v.dominance is True
        assert v.regression_count == 0
        assert "Pareto dominance" in v.reason

    def test_dominance_delta_per_heuristic_negative(self) -> None:
        """Improvements have negative delta (child - parent < 0)."""
        parent = {"h1": 7, "h2": 9}
        child = {"h1": 0, "h2": 3}
        v = verdict(parent, child)
        assert v.delta_per_heuristic == {"h1": -7, "h2": -6}

    def test_weighted_sum_fallback_accept_one_regression(self) -> None:
        """Dominance fails (h2 regresses) but net sum improves and
        regression_count=1 ≤ max_regression=1 → accept via fallback."""
        parent = {"h1": 7, "h2": 5, "h3": 7}
        child = {"h1": 0, "h2": 7, "h3": 3}  # h2 regresses, but sum 10 < 19
        v = verdict(parent, child)
        assert v.accepted is True
        assert v.dominance is False
        assert v.regression_count == 1
        assert "Weighted-sum fallback" in v.reason

    def test_weighted_sum_fallback_requires_sum_improvement(self) -> None:
        """Regression within tolerance but no sum improvement → reject."""
        parent = {"h1": 5, "h2": 5}
        child = {"h1": 0, "h2": 10}  # one regression, but 10 > 10? sum equal
        v = verdict(parent, child)
        # parent sum = 10, child sum = 10 — no sum improvement → reject
        assert v.accepted is False
        assert v.dominance is False
        assert v.regression_count == 1

    def test_accept_reason_names_improvements(self) -> None:
        parent = {"h1": 7, "h2": 9}
        child = {"h1": 3, "h2": 0}
        v = verdict(parent, child)
        # Dominance case — reason names improved slugs.
        assert "h1" in v.reason
        assert "h2" in v.reason


# =============================================================================
# verdict — reject paths
# =============================================================================


class TestVerdictRejectPaths:
    def test_too_many_regressions_reject(self) -> None:
        """Two heuristics regress; max_regression=1 → reject."""
        parent = {"h1": 3, "h2": 3, "h3": 9}
        child = {"h1": 7, "h2": 5, "h3": 0}  # h1, h2 regress; h3 improves
        v = verdict(parent, child, max_regression=1)
        assert v.accepted is False
        assert v.regression_count == 2
        assert "exceed max_regression" in v.reason

    def test_too_many_regressions_even_with_good_sum(self) -> None:
        """Even if sum improves, exceeding max_regression → reject."""
        parent = {"h1": 5, "h2": 5, "h3": 5}
        child = {"h1": 7, "h2": 7, "h3": 0}  # two regressions, sum 14 < 15
        v = verdict(parent, child, max_regression=1)
        assert v.accepted is False
        assert v.regression_count == 2

    def test_max_regression_zero_forbids_any_regression(self) -> None:
        """max_regression=0 means dominance is required."""
        parent = {"h1": 5, "h2": 5}
        child = {"h1": 0, "h2": 7}  # one regression
        v = verdict(parent, child, max_regression=0)
        assert v.accepted is False

    def test_max_regression_higher_allows_more(self) -> None:
        """max_regression=2 accepts up to two regressions if sum improves."""
        parent = {"h1": 5, "h2": 5, "h3": 9}
        child = {"h1": 7, "h2": 7, "h3": 0}  # sum 14 < 19, 2 regressions
        v = verdict(parent, child, max_regression=2)
        assert v.accepted is True

    def test_all_equal_is_no_op_reject(self) -> None:
        parent = {"h1": 5, "h2": 5}
        child = {"h1": 5, "h2": 5}
        v = verdict(parent, child)
        assert v.accepted is False
        assert v.regression_count == 0
        assert "no-op" in v.reason.lower() or "no improvement" in v.reason.lower()

    def test_pure_regression_reject(self) -> None:
        """Child strictly worse on every heuristic → reject hard."""
        parent = {"h1": 3, "h2": 3}
        child = {"h1": 7, "h2": 7}
        v = verdict(parent, child)
        assert v.accepted is False
        assert v.regression_count == 2
        assert v.dominance is False

    def test_rejected_reason_names_regressed_slugs(self) -> None:
        parent = {"h1": 3, "h2": 3, "h3": 9}
        child = {"h1": 7, "h2": 5, "h3": 0}  # h1, h2 regress
        v = verdict(parent, child, max_regression=1)
        assert "h1" in v.reason
        assert "h2" in v.reason


# =============================================================================
# verdict — edge cases
# =============================================================================


class TestVerdictEdges:
    def test_single_heuristic_improvement(self) -> None:
        parent = {"only": 9}
        child = {"only": 0}
        v = verdict(parent, child)
        assert v.accepted is True
        assert v.dominance is True

    def test_single_heuristic_equal_no_op(self) -> None:
        parent = {"only": 5}
        child = {"only": 5}
        v = verdict(parent, child)
        assert v.accepted is False

    def test_mismatched_keys_raises(self) -> None:
        parent = {"h1": 5, "h2": 5}
        child = {"h1": 5, "h3": 5}
        with pytest.raises(ValueError, match="keys"):
            verdict(parent, child)

    def test_empty_vectors_no_op(self) -> None:
        v = verdict({}, {})
        assert v.accepted is False
        assert v.dominance is False
        assert v.regression_count == 0

    def test_verdict_returns_pareto_verdict_instance(self) -> None:
        parent = {"h1": 7}
        child = {"h1": 0}
        v = verdict(parent, child)
        assert isinstance(v, ParetoVerdict)


# =============================================================================
# Default max_regression constant
# =============================================================================


class TestDefaults:
    def test_default_max_regression_is_one(self) -> None:
        # IMPLEMENTATION_PLAN: max_regression=1.
        assert DEFAULT_MAX_REGRESSION == 1

    def test_verdict_default_uses_one(self) -> None:
        """Confirm the default propagates through verdict()."""
        parent = {"h1": 5, "h2": 3}
        # Two regressions — with default=1 → reject
        child = {"h1": 7, "h2": 7}
        v = verdict(parent, child)  # default max_regression=1
        assert v.accepted is False
        assert v.regression_count == 2
