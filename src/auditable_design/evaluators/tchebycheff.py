"""Weighted Tchebycheff evaluator for L8 multi-round iterations.

Pure-Python — no Claude calls, no I/O. Alternative to
:mod:`auditable_design.evaluators.pareto` for use inside the L8 loop.

Motivation. Pareto dominance is a strong gate for a single tweak
("do no harm") but chokes in multi-round optimization: once a
parent has pushed several heuristics to 0, any further tweak that
trades a tiny regression on one dimension for a big win on another
is rejected, even with ``max_regression=1`` (the weighted-sum
fallback requires strict total improvement).

This evaluator uses the **weighted Tchebycheff (L∞) scalarization**
from Wierzbicki 1980 and Steuer 1986 (see also Miettinen 1999,
*Nonlinear Multiobjective Optimization*, Ch. 3.4). Classical form:

    min  max_h ( w_h · |f_h(x) − z_h^ideal| )

For L8 the ideal is ``z_h^ideal = 0`` (every heuristic fully
resolved) and residuals are non-negative (severity ≥ 0), so the
absolute value collapses:

    cost(scores, weights) = max_h ( w_h · scores[h] )

Self-weighting. Each heuristic's weight is its ``parent[h]``
severity — the severity anchor the parent was judged against (ADR-
008 anchored scale, legal values ``{0, 3, 5, 7, 9}``; pipeline
tolerates any int 0–10). A severity-9 heuristic weights 9 against
its own residual, a severity-3 weights 3; so regressions on prior-
high-severity heuristics dominate the max and are nearly impossible
to compensate by improvements elsewhere. This is the key property
for multi-round: a genuinely bad regression cannot hide behind a
favourable total.

Accept rule. Child is accepted iff

    child_cost < parent_cost · (1 − min_improvement_pct / 100)

where ``parent_cost = max_h(parent[h]²)`` and
``child_cost = max_h(parent[h] · child[h])``. Default
``min_improvement_pct = 10.0``.

Non-convex Pareto coverage. Unlike the linear weighted-sum
scalarization, the Tchebycheff metric can identify every Pareto-
optimal solution — including those in non-convex regions of the
front (Miettinen 1999, Th. 3.4.5). For L8 this matters because the
heuristic-reduction frontier is rarely convex: one snapshot may
resolve 2/7 heuristics completely while leaving 5 untouched; a
different snapshot resolves 5/7 partially. Weighted sum with equal
weights prefers the partial-but-broad snapshot; Tchebycheff
prioritises whichever option shrinks the worst remaining residual.

Edge cases.

- ``parent_cost == 0`` (every heuristic already at 0, or empty
  vector) → the verdict is "converged" (``accepted=False``,
  ``converged=True``), so loop orchestrators can terminate cleanly.
- Child == parent → improvement 0 % < 10 % → reject as
  "insufficient improvement".
- Pure regression (every child[h] > parent[h]) → child_cost >
  parent_cost → reject.

This module intentionally does NOT import from
:mod:`auditable_design.evaluators.pareto` — keeping the two
verifiers independent means the L8 loop can switch via a
``--verifier`` flag without touching either module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


DEFAULT_MIN_IMPROVEMENT_PCT: Final[float] = 10.0


@dataclass(frozen=True, slots=True)
class TchebycheffVerdict:
    """Outcome of comparing a child iteration to its parent under
    the weighted Tchebycheff (L∞) cost rule.

    ``accepted`` — whether the child should be recorded as an
    accepted iteration.

    ``reason`` — always populated. Suitable for
    ``OptimizationIteration.reasoning`` on accept or
    ``.regression_reason`` on reject.

    ``parent_cost`` — ``max_h(parent[h] * parent[h])``, i.e. the
    square of the heuristic with the highest parent severity.

    ``child_cost`` — ``max_h(parent[h] * child[h])``, the weighted
    max residual under parent weights.

    ``binding_heuristic`` — the heuristic slug whose weighted cost
    equals ``child_cost`` (the L∞ "binding constraint"; when there
    are ties, the alphabetically first is returned for determinism).
    Named so the loop orchestrator can surface "this is the one you
    still need to fix" without recomputing.

    ``improvement_pct`` — ``100 * (parent_cost - child_cost) /
    parent_cost``, or ``0.0`` when ``parent_cost == 0``.

    ``converged`` — True iff ``parent_cost == 0`` (the parent
    already scores 0 on every heuristic). Loop terminates cleanly
    on converged.

    ``regression_count`` — number of heuristics where ``child[h] >
    parent[h]``. Reported for parity with :class:`ParetoVerdict`
    and for debugging; the accept rule itself does not gate on it
    (regressions show up instead by increasing the weighted max).

    ``delta_per_heuristic`` — ``child[h] - parent[h]`` per key;
    negative = improvement, positive = regression.
    """

    accepted: bool
    reason: str
    parent_cost: int
    child_cost: int
    binding_heuristic: str | None
    improvement_pct: float
    converged: bool
    regression_count: int
    delta_per_heuristic: dict[str, int]


def tchebycheff_cost(
    scores: dict[str, int], weights: dict[str, int]
) -> tuple[int, str | None]:
    """Return ``(max_h(scores[h] * weights[h]), binding_heuristic)``.

    The second element is the heuristic slug whose weighted
    contribution attains the maximum; ``None`` iff the scores dict
    is empty. On ties, the alphabetically first slug is returned
    (deterministic).

    Raises ``ValueError`` on mismatched key sets.
    """
    if set(scores) != set(weights):
        raise ValueError(
            f"tchebycheff_cost: scores keys {sorted(scores)} != weights keys {sorted(weights)}"
        )
    if not scores:
        return 0, None
    # Deterministic tie-break: iterate sorted keys.
    best_h: str | None = None
    best_val = -1
    for h in sorted(scores):
        val = scores[h] * weights[h]
        if val > best_val:
            best_val = val
            best_h = h
    # best_val == -1 only if scores was empty, handled above.
    return best_val, best_h


def verdict(
    parent: dict[str, int],
    child: dict[str, int],
    *,
    min_improvement_pct: float = DEFAULT_MIN_IMPROVEMENT_PCT,
) -> TchebycheffVerdict:
    """Full accept/reject decision under the weighted Tchebycheff
    (L∞) cost rule.

    See module docstring for the algorithm. Keys must match between
    parent and child — caller error otherwise.
    """
    if set(parent) != set(child):
        raise ValueError(
            f"verdict: parent keys {sorted(parent)} != child keys {sorted(child)}"
        )

    delta = {h: child[h] - parent[h] for h in parent}
    regression_count = sum(1 for d in delta.values() if d > 0)

    parent_cost, _parent_binding = tchebycheff_cost(parent, parent)
    child_cost, child_binding = tchebycheff_cost(child, parent)

    if parent_cost == 0:
        # Parent is all-zero (or empty) — no further improvement
        # measurable. Signal convergence so the loop terminates.
        return TchebycheffVerdict(
            accepted=False,
            reason=(
                "Converged: parent weighted-max residual is 0 "
                "(every heuristic already at severity 0 — no "
                "further improvement measurable under weighted "
                "Tchebycheff cost)."
            ),
            parent_cost=0,
            child_cost=child_cost,
            binding_heuristic=child_binding,
            improvement_pct=0.0,
            converged=True,
            regression_count=regression_count,
            delta_per_heuristic=delta,
        )

    improvement_pct = 100.0 * (parent_cost - child_cost) / parent_cost
    threshold_cost = parent_cost * (1.0 - min_improvement_pct / 100.0)

    if child_cost < threshold_cost:
        regressions = sorted(h for h, d in delta.items() if d > 0)
        improvements = sorted(h for h, d in delta.items() if d < 0)
        parts: list[str] = [
            f"Weighted Tchebycheff cost {child_cost} < threshold "
            f"{threshold_cost:.1f} "
            f"(parent {parent_cost}, "
            f"-{improvement_pct:.1f}% vs min "
            f"{min_improvement_pct:.1f}%). Binding child "
            f"heuristic: {child_binding}.",
        ]
        if improvements:
            parts.append(f"Improvements: {', '.join(improvements)}.")
        if regressions:
            parts.append(
                f"Tolerated regressions: {', '.join(regressions)}."
            )
        return TchebycheffVerdict(
            accepted=True,
            reason=" ".join(parts),
            parent_cost=parent_cost,
            child_cost=child_cost,
            binding_heuristic=child_binding,
            improvement_pct=improvement_pct,
            converged=False,
            regression_count=regression_count,
            delta_per_heuristic=delta,
        )

    # Reject.
    regressions = sorted(h for h, d in delta.items() if d > 0)
    if child_cost >= parent_cost:
        reason = (
            f"Rejected: weighted Tchebycheff cost {child_cost} >= "
            f"parent cost {parent_cost} — no net improvement "
            f"({improvement_pct:+.1f}%). Binding heuristic: "
            f"{child_binding}."
        )
        if regressions:
            reason += f" Regressions: {', '.join(regressions)}."
    else:
        reason = (
            f"Rejected: weighted Tchebycheff cost drop "
            f"{improvement_pct:.1f}% below required "
            f"{min_improvement_pct:.1f}% threshold "
            f"(parent {parent_cost} -> child {child_cost}, "
            f"threshold {threshold_cost:.1f}). Binding heuristic: "
            f"{child_binding}."
        )
    return TchebycheffVerdict(
        accepted=False,
        reason=reason,
        parent_cost=parent_cost,
        child_cost=child_cost,
        binding_heuristic=child_binding,
        improvement_pct=improvement_pct,
        converged=False,
        regression_count=regression_count,
        delta_per_heuristic=delta,
    )


__all__ = [
    "DEFAULT_MIN_IMPROVEMENT_PCT",
    "TchebycheffVerdict",
    "tchebycheff_cost",
    "verdict",
]
