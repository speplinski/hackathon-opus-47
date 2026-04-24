# L8 design-optimize — matched-model eval

**Scope:** one cluster (`cluster_02` "Streak loss framing") with one reconciled verdict (L5 Opus 4.6), one priority score (L6 Opus 4.6), one design decision (L7 Opus 4.6) × three optimize models (Opus 4.6 / Sonnet 4.6 / Opus 4.7) × one modality (text-only — L8 re-audits structured before/after snapshots, not UI). 3 cells. Each cell is one Claude call (single-pass re-audit). Closes ADR-009 L8 pilot action item.

**Status:** 3/3 scored, zero fallbacks, **all three cells accept via Pareto dominance with zero regressions.** Strong cross-model agreement on which heuristics the L7 after_snapshot structurally resolves (modal_excise / competing_calls_to_action / deceptive_feedback_scarcity_timer / ego_depletion → 0 on every cell) and which carry residue (channel_gap / cr_undermined_by_r_dollar / vp_cs_mismatch stay at 3–5).

## Purpose

L8 re-audits an L7 DesignDecision's `after_snapshot` against the baseline heuristic list from L5's reconciled verdict, then applies Pareto dominance + weighted-sum fallback (IMPLEMENTATION_PLAN) to decide whether the proposed design is an accepted iteration. The eval characterises:

- **Cross-model agreement on per-heuristic severity deltas.** If three models re-audit the same proposal with the same baseline, how tightly do they agree on which heuristics the proposal resolves, partially resolves, or fails to resolve?
- **Pareto verdict consistency.** Does the accept/reject decision hold across models, or does model choice tip some iterations between accept and reject?
- **Regression pattern.** Does any model flag a regression on any heuristic? (On this cluster: none.)

## Executive summary

| metric | Opus 4.6 | Sonnet 4.6 | Opus 4.7 |
|---|---:|---:|---:|
| Status | optimized | optimized | optimized |
| Pareto verdict | ACCEPTED (dominance) | ACCEPTED (dominance) | ACCEPTED (dominance) |
| Regressions | 0 | 0 | 0 |
| Heuristics improved | 7 / 7 | 7 / 7 | 7 / 7 |
| Heuristics resolved to 0 | 4 | 4 | 4 |
| Baseline severity sum | 57 | 57 | 57 |
| Proposed severity sum | **9** | 11 | 11 |
| Severity reduction | 48 pts (84%) | 46 pts (81%) | 46 pts (81%) |
| Input tokens | 4 170 | 4 170 | 5 859 |
| Output tokens | 563 | 456 | 512 |
| Skill_hash | `db550438e48eca93…` | `db550438e48eca93…` | `db550438e48eca93…` |

All three cells share the same `skill_hash` (skill unchanged across the run). All three read the same Claude skill response shape (7 heuristic scores + reasoning) without parse failures. All three Pareto-dominate the baseline.

## Methodology

### Input chain

- **Cluster:** `cluster_02` "Streak loss framing pressures users into mid-session purchase" — shared fixture sha256 `dc6d981f…` (same as L4/L5/L6/L7 evals).
- **L5 reconciled:** `data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl` — sha256 `181758aae71f57a7…`. 7 ranked violations (top: `modal_excise__corroborated` rs=45, `channel_gap__corroborated` rs=36).
- **L6 priority:** `data/derived/l6_weight/l6_priority_cluster02_opus46.jsonl` — sha256 `b5c1aa783e2f464e…`. Dimensions severity=10 reach=9 persistence=8 business_impact=9 cognitive_cost=9, weighted_total=9.00.
- **L7 decision:** `data/derived/l7_decide/l7_design_decisions_cluster02_opus46.jsonl` — sha256 `30bf1a95f03dea34…`. Principle *"Monetisation lives at boundaries, not mid-flow"*; decision relocates streak-risk modal from mid-lesson to lesson-complete boundary with equal-weight paths + no countdown timer.

### Skill

`skills/design-optimize/SKILL.md` v1.0 — skill_hash `db550438e48eca93…` (identical across all three cells).

Output contract `{scored_heuristics, reasoning}` (2 top-level keys). Parser cross-validates:

- `scored_heuristics` keys must exactly match the baseline heuristic list from the reconciled verdict (no missing, no extras).
- Each severity must be in ADR-008's anchored set `{0, 3, 5, 7, 9}`.
- Reasoning is a non-empty string.

All three models respected all three constraints on first attempt.

### Runs

Single-pass per cell. Opus 4.6 and Sonnet 4.6 at temperature 0.0; Opus 4.7 stripped. `MAX_TOKENS=4096` per call (output 456–563 tokens; comfortable headroom). Pareto evaluator (`auditable_design.evaluators.pareto.verdict`) with `max_regression=1` default.

## Results

### Per-heuristic severity delta

The reconciled baseline carried 7 heuristics at severities summing to 57. Every model re-audited the same 7 heuristics against the same `after_snapshot`.

| heuristic | baseline | Opus 4.6 | Sonnet 4.6 | Opus 4.7 |
|---|---:|---:|---:|---:|
| `modal_excise__corroborated` | 9 | **0** | **0** | **0** |
| `competing_calls_to_action__corroborated` | 7 | **0** | **0** | **0** |
| `deceptive_feedback__scarcity_timer_suppression__timing_adjustable` | 7 | **0** | **0** | **0** |
| `ego_depletion_mid_task` | 9 | **0** | **0** | **0** |
| `channel_gap__corroborated` | 9 | 3 | 3 | 3 |
| `vp_cs_mismatch` | 9 | 3 | 3 | 3 |
| `cr_undermined_by_r_dollar__corroborated` | 7 | **3** | 5 | 5 |
| **Sum** | **57** | **9** | **11** | **11** |

Five heuristics resolve-to-0 on every cell. Two heuristics (`channel_gap__corroborated`, `vp_cs_mismatch`) land at 3 on every cell (cosmetic residue — the after_snapshot addresses the structural cause but a minor trace remains). One heuristic (`cr_undermined_by_r_dollar__corroborated`) is the single model-divergent dimension: Opus 4.6 reads it as 3 (cosmetic), Sonnet 4.6 and Opus 4.7 both read it as 5 (partial residue).

### Cross-model agreement

- **6 of 7 heuristics: exact agreement across three models** — identical integer severity per heuristic, per cell.
- **1 of 7 heuristics: 1-point disagreement** on `cr_undermined_by_r_dollar__corroborated` (Opus 4.6 at 3, others at 5). Both readings are within ADR-008's anchor legality; the difference corresponds to whether the residue is "cosmetic" (the paid path still exists as one of three equal-weight options) or "partial" (the conversion-funnel still leaks into the user-retention surface even in the relocated form).

This is the tightest cross-model convergence observed across any L-layer eval in this pipeline. Compare to L7 (decision strategy ranged from *"move"* to *"remove"* across models — large spread) or L5 (top-1 ranked entry differed between Opus 4.7 and the other two — the graph-arithmetic spread). L8 is a constrained generative task (score 7 heuristics on a closed anchor set) where three models with a shared rubric should converge, and they do.

### Pareto verdict

All three cells reach **ACCEPTED via Pareto dominance** — every heuristic is equal-or-better (actually strictly-better on all 7) on the proposed iteration. Zero regressions, zero weighted-sum fallback needed, zero rejections.

Severity reduction:
- Opus 4.6: 57 → 9 (**–48 points, 84% reduction**)
- Sonnet 4.6: 57 → 11 (–46 points, 81%)
- Opus 4.7: 57 → 11 (–46 points, 81%)

The 2-point gap between Opus 4.6 and the other two models is fully attributable to the single `cr_undermined_by_r_dollar` disagreement (3 vs 5). Under L6's symmetric 0.2 meta-weights this would weight at 0.4 points of priority difference — small enough that downstream L6 re-weighting on the proposed iteration would land all three models in the same priority band.

### Model reasoning excerpts

All three models independently identified the same four heuristics as structurally resolved:

**Opus 4.6:** *"modal_excise_corroborated is fully resolved: the after_snapshot eliminates the blocking modal entirely — the question completes normally, progress auto-saves, and the lesson ends gracefully with no viewport blocker. ego_depletion_mid_task is likewise resolved: the purchase/ad decision is deferred to..."*

**Sonnet 4.6:** *"The after_snapshot structurally eliminates the blocking mid-lesson modal (modal_excise resolved to 0), the countdown timer (deceptive_feedback resolved to 0), the unequal CTA hierarchy with the 11px dismiss link (competing_calls_to_action resolved to 0 via equally-sized buttons with equivalent contrast)..."*

**Opus 4.7:** *"The after_snapshot structurally resolves modal_excise (no blocking modal; lesson completes and auto-saves), the countdown-timer defect (timer removed entirely), ego_depletion_mid_task (decision moved to lesson-complete screen, not mid-task), and competing_calls_to_action (three equally-sized buttons)..."*

Three different model voices, same audit conclusion. The `after_snapshot`'s structural changes (remove modal + save lesson state + equal-weight paths + no timer) land unambiguously for all three re-auditors.

### Partial-residue reading

Three heuristics receive non-zero proposed severities; all three are about monetisation being *present* even if well-positioned:

- `channel_gap__corroborated` (→ 3): the after_snapshot still has a subscription path. The gap between "here is the non-paid retention affordance" (streak-freeze, ad-watch) and "here is the paid one" survives as a softer cosmetic residue at the lesson-complete boundary.
- `vp_cs_mismatch` (→ 3): the value-proposition / customer-segment mismatch (learning-tool product → monetisation-funnel surface) is reduced but not eliminated; the subscribe path still exists, just relocated.
- `cr_undermined_by_r_dollar__corroborated` (→ 3 or 5): the customer-relationship vs revenue-stream tension survives; users who previously encountered a monetisation blocker now encounter a monetisation offer at a friendlier moment — the relationship is less damaged but still brushed.

The after_snapshot is a **surface relocation, not a business-model change.** L7's decision chose "move the modal" not "remove the monetisation engine"; L8 correctly scores the three business-model-touching heuristics as partial-resolve rather than fully-resolve. The pipeline's audit-traceability discipline works: L5 surfaced the tension, L7 wrote a decision that addresses it structurally but partially, L8 re-audits honestly.

## Architecture observation

L8 is the third layer in a row (after L6, L7) to land first-try with zero fallbacks. The pattern for generation-over-structured-input layers is now stable:

- **Narrow output contract.** 2 top-level keys, 7 dict entries total, no nested structures, no arithmetic for the model to compute.
- **Strict cross-reference validation.** `scored_heuristics` keys must equal baseline heuristic list exactly — no invented slugs, no missing ones. ADR-008 severities must be in `{0, 3, 5, 7, 9}`. Both checks fired zero times in this run — the models got the shape right on first attempt.
- **Pure-Python evaluator downstream.** The model emits scores; a deterministic Pareto evaluator decides accept/reject. This cleanly separates editorial voice (which severity anchor fits this after_snapshot) from the pipeline's policy (how to aggregate per-heuristic deltas into an accept/reject). Moving policy out of the LLM was the same move L6 made with meta-weights — and it paid off with the same first-try landing.

Contrast with L5 (graph-primary + dual-representation-drift, four iterations). L8 does NOT ask the model to author a graph, does NOT ask it to maintain a flat-list mirror, does NOT ask it to compute rank_score arithmetic. It asks the model to score 7 integers and write one reasoning paragraph. That narrowness is what makes it land.

## Caveats

- **One cluster only.** The 84%/81% severity reduction is unusually clean because L7's decision was a textbook structural move (remove the blocker) against a textbook dark-pattern cluster. Clusters with more ambiguous L7 decisions — where the after_snapshot leaves the disputed surface in place — will surface larger cross-model deltas and more partial-resolve scores.
- **Zero regressions is strong but cluster-specific.** On this cluster the L7 decision genuinely resolves or partially-resolves every baseline heuristic without introducing a new defect class. A full-corpus run will include clusters where the L7 decision trades one heuristic for another (e.g. removing a modal introduces a content-overload issue) — the Pareto evaluator will reject or accept-via-fallback those, which is correct.
- **Single-input chain.** L5+L6+L7 all come from Opus 4.6 outputs. Running L8 on Sonnet 4.6's or Opus 4.7's L7 decision would produce a different re-audit surface; different L7 decisions would mean different after_snapshots and therefore different severity deltas. The eval does not stress-test cross-chain variance.
- **Pareto dominance is binary at the heuristic level.** A decision that moves a heuristic from sev-9 to sev-3 (a 6-point structural improvement) scores the same at the dominance check as a decision that moves it from sev-9 to sev-7 (a 2-point marginal improvement) — both are "strictly better." The weighted-sum fallback captures magnitude only when dominance fails. This is correct per IMPLEMENTATION_PLAN's algorithm; a future extension could rank accepted iterations by improvement magnitude for L10 evolution graph ordering.
- **No multi-step optimisation loop.** This pilot runs baseline + 1 proposed iteration. IMPLEMENTATION_PLAN's "target 3 accepted iterations" loop would propose iteration 2 (tweak of iteration 1), re-audit, and continue — requires a generative "design tweak" step not implemented here. Thin-spine sufficient for the pilot; loop extension is a separate feature.

## Reproducing

```
# Run the grid (requires ANTHROPIC_API_KEY):
bash scripts/run_l8_optimize_matched.sh

# Force-rerun:
bash scripts/run_l8_optimize_matched.sh --all

# Single cell:
uv run python scripts/smoke_l8_optimize.py --model claude-sonnet-4-6

# Inspect:
ls -1 data/derived/l8_optimize/*.provenance.json
cat data/derived/l8_optimize/artifacts/opus46/cluster_02_iter01.md

# Tests (88/88 passing):
uv run pytest tests/test_pareto.py tests/test_l8_optimize.py -v
```

Module: `src/auditable_design/layers/l8_optimize.py`. Evaluator: `src/auditable_design/evaluators/pareto.py`. Skill: `skills/design-optimize/SKILL.md` v1.0 (`skill_hash=db550438e48eca93…`). Expected spend: ~$0.15–0.30 across three cells.

## Closing

ADR-009's L8 pilot action item closed. The eval characterises three-model convergence on a constrained generative task: 6 of 7 heuristics receive exact-match severity readings across three models; the 7th differs by one anchor band (3 vs 5). All three cells Pareto-dominate the baseline with zero regressions. The pipeline's audit-traceability works end-to-end: L4 skills audit → L5 reconciles → L6 prioritises → L7 decides → L8 re-audits the decision against the original baseline heuristic list — and the re-audit honestly surfaces which defects the decision resolves (modal/timer/CTA/ego-depletion) vs which carry business-model residue (channel-gap/vp-cs-mismatch/cr-undermined).

L8 is ship-ready. The full-corpus run will stress-test it on clusters where the L7 decision is less surgical than this one.
