"""Pareto dominance evaluator for L8 optimization iterations.

Pure-Python — no Claude calls, no I/O. Given two per-heuristic score
dicts (parent baseline and child proposed iteration) and a
regression tolerance, decides whether the child is an accepted
iteration.

Algorithm (IMPLEMENTATION_PLAN):

- **Primary check — Pareto dominance.** Child dominates parent iff
  every heuristic's child severity is ≤ parent severity AND at least
  one heuristic is strictly < parent severity. Dominance → ACCEPT.
- **Fallback — weighted-sum with max_regression.** If dominance
  fails, count heuristics where child > parent (regressions). If
  regression count ≤ ``max_regression`` AND ``sum(child) < sum(parent)``
  — a meaningful weighted-sum improvement tolerating a small
  regression — ACCEPT. Otherwise REJECT.
- **Tie — neither strict improvement nor regression.** Every
  heuristic equal between parent and child is a no-op iteration;
  REJECT with a "no improvement" reason.

Severity is ADR-008's anchored 0–10 scale (legal values
``{0, 3, 5, 7, 9}``); the evaluator treats severity as any int 0–10
because the caller may pass non-anchored values for unit-test edges.

The evaluator intentionally does NOT use the L6 meta-weights — the
weighted-sum fallback is unweighted total severity. Rationale:
meta-weights are per-dimension (severity / reach / ...); heuristics
live in a different dimensional space. Equal weighting across
heuristics is the simplest defensible default; a future extension
could accept per-heuristic weights, but none exist in the pipeline
today.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


DEFAULT_MAX_REGRESSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class ParetoVerdict:
    """Outcome of comparing a child iteration to its parent.

    ``accepted`` — whether the child should be recorded as an
    accepted optimization iteration.

    ``reason`` — always populated. On accept: names the mechanism
    ("Pareto dominance" or "weighted-sum fallback"). On reject: names
    which heuristic(s) regressed or why no improvement was detected.
    Suitable as the ``OptimizationIteration.regression_reason`` field
    on reject (recorded_at L8 module).

    ``regression_count`` — number of heuristics where child_severity
    > parent_severity.

    ``dominance`` — True iff child Pareto-dominates parent (every
    heuristic equal-or-better, at least one strictly better).

    ``delta_per_heuristic`` — ``child_severity - parent_severity``
    per key; negative = improvement, positive = regression.
    """

    accepted: bool
    reason: str
    regression_count: int
    dominance: bool
    delta_per_heuristic: dict[str, int]


def dominance(
    parent: dict[str, int], child: dict[str, int]
) -> bool:
    """True iff ``child`` Pareto-dominates ``parent``.

    Both dicts must share the same key set; the caller is expected
    to enforce this (:func:`verdict` does so). Caller-error patterns
    like mismatched keys raise :class:`ValueError` rather than
    silently returning False.
    """
    if set(parent) != set(child):
        raise ValueError(
            f"dominance: parent keys {sorted(parent)} != child keys {sorted(child)}"
        )
    strict_better = False
    for h, parent_sev in parent.items():
        child_sev = child[h]
        if child_sev > parent_sev:
            return False  # regression — cannot dominate
        if child_sev < parent_sev:
            strict_better = True
    return strict_better


def weighted_sum(scores: dict[str, int]) -> int:
    """Unweighted total severity across the heuristic vector.

    Equal-weighted across heuristics (see module docstring for
    rationale). Returns an int because all individual severities are
    ints; no rounding.
    """
    return sum(scores.values())


def verdict(
    parent: dict[str, int],
    child: dict[str, int],
    *,
    max_regression: int = DEFAULT_MAX_REGRESSION,
) -> ParetoVerdict:
    """Full accept/reject decision for one child-vs-parent pairing.

    Primary: Pareto dominance → ACCEPT.
    Fallback: weighted-sum improvement with at most ``max_regression``
    regressing heuristics → ACCEPT.
    Else: REJECT.

    The ``reason`` field is always populated (even on accept) with a
    human-readable sentence the caller can write into
    ``OptimizationIteration.reasoning`` or ``.regression_reason``.
    """
    if set(parent) != set(child):
        raise ValueError(
            f"verdict: parent keys {sorted(parent)} != child keys {sorted(child)}"
        )

    delta = {h: child[h] - parent[h] for h in parent}
    regressions = {h: d for h, d in delta.items() if d > 0}
    improvements = {h: d for h, d in delta.items() if d < 0}

    dominates = dominance(parent, child)

    if dominates:
        improved_slugs = sorted(improvements.keys())
        reason = (
            f"Pareto dominance: child improves on "
            f"{len(improvements)} heuristic(s) "
            f"({', '.join(improved_slugs)}) "
            f"without regressing on any."
        )
        return ParetoVerdict(
            accepted=True,
            reason=reason,
            regression_count=0,
            dominance=True,
            delta_per_heuristic=delta,
        )

    # Dominance failed — check weighted-sum fallback.
    parent_sum = weighted_sum(parent)
    child_sum = weighted_sum(child)
    regression_count = len(regressions)

    if regression_count == 0 and not improvements:
        # All heuristics equal → no-op iteration.
        return ParetoVerdict(
            accepted=False,
            reason=(
                "No improvement: every heuristic's severity is identical "
                "to the parent iteration; the child is a no-op."
            ),
            regression_count=0,
            dominance=False,
            delta_per_heuristic=delta,
        )

    if regression_count <= max_regression and child_sum < parent_sum:
        improved_slugs = sorted(improvements.keys())
        regressed_slugs = sorted(regressions.keys())
        reason = (
            f"Weighted-sum fallback: child sum {child_sum} < parent sum "
            f"{parent_sum} ({parent_sum - child_sum} severity units "
            f"reduced); {regression_count} regression "
            f"({', '.join(regressed_slugs)}) tolerated at "
            f"max_regression={max_regression}. Improvements: "
            f"{', '.join(improved_slugs)}."
        )
        return ParetoVerdict(
            accepted=True,
            reason=reason,
            regression_count=regression_count,
            dominance=False,
            delta_per_heuristic=delta,
        )

    # Reject.
    regressed_slugs = sorted(regressions.keys())
    if regression_count > max_regression:
        reason = (
            f"Rejected: {regression_count} heuristic regressions "
            f"({', '.join(regressed_slugs)}) exceed max_regression="
            f"{max_regression}."
        )
    else:
        # Exactly one regression (or few), but weighted sum did not
        # improve.
        reason = (
            f"Rejected: child sum {child_sum} >= parent sum "
            f"{parent_sum} — no net severity improvement despite "
            f"{regression_count} regression "
            f"({', '.join(regressed_slugs)})."
        )
    return ParetoVerdict(
        accepted=False,
        reason=reason,
        regression_count=regression_count,
        dominance=False,
        delta_per_heuristic=delta,
    )


__all__ = [
    "DEFAULT_MAX_REGRESSION",
    "ParetoVerdict",
    "dominance",
    "verdict",
    "weighted_sum",
]
