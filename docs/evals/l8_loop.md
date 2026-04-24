# L8 multi-round optimization loop — matched eval

Extension of the thin-spine L8 stack (see `l8_optimize_matched.md`). The thin spine runs a single re-audit of L7's proposed `after_snapshot` and accepts/rejects via Pareto. This eval covers the multi-round loop: iteration 2+ alternates `design-tweak` (propose a minimal surgical refinement) and `design-optimize` (re-audit), gated by an external verifier with two alternatives — Pareto (the thin-spine default) and weighted Tchebycheff (Wierzbicki 1980, Steuer 1986; see `src/auditable_design/evaluators/tchebycheff.py`).

## Scope and grid

- **Cluster:** `cluster_02` (Duolingo streak-loss framing pressures users into mid-session purchase).
- **Input per model:** the thin-spine L8 iterations file for that model (`l8_optimization_iterations_cluster02_{modelshort}.jsonl`) — iter 0 baseline + iter 1 proposed. Loop appends iter 2+.
- **Models:** Opus 4.6, Sonnet 4.6, Opus 4.7. Matched — the same model is used for both `design-tweak` and `design-optimize` within a cell. Convention matches L3b / L4×6 / L5 / L6 / L7 / L8 thin-spine evals.
- **Verifiers:** `pareto` and `tchebycheff` — separate runs per model.
- **Loop budget:** `max_iterations=5` (inclusive of iter 0+1 from the thin spine), `stall_limit=2`, `severity_threshold=5`, `min_improvement_pct=10.0`.

Total cells: 6 (1 cluster × 3 models × 2 verifiers).

## Headline results

| Model    | Verifier     | New iters | Accepted | Rejected | Final sum | Δ vs iter 1 | Termination |
|----------|--------------|-----------|----------|----------|-----------|-------------|-------------|
| Opus 4.6 | pareto       | 1         | 1        | 0        | 0         | −9          | converged   |
| Opus 4.6 | tchebycheff  | 1         | 1        | 0        | 0         | −9          | converged   |
| Sonnet 4.6 | pareto     | 3         | 1        | 2        | 6         | −5          | **stall**   |
| Sonnet 4.6 | tchebycheff | 3         | 1        | 2        | 6         | −5          | **stall**   |
| Opus 4.7 | pareto       | 1         | 1        | 0        | 0         | −11         | converged   |
| Opus 4.7 | tchebycheff  | 1         | 1        | 0        | 0         | −11         | converged   |

Both Opus models converge the loop in one extra iteration to a fully resolved surface (severity sum 0). Sonnet 4.6 stalls — iteration 2 partially improves the residuals (sum 11 → 6), and iterations 3 and 4 produce tweaks the re-audit scores identically to the parent, triggering two consecutive rejections and loop termination at sum 6.

## Reductions across the stack

From L5 reconciled baseline to final parent, per model:

| Stage                     | Opus 4.6      | Sonnet 4.6     | Opus 4.7      |
|---------------------------|---------------|----------------|---------------|
| Iter 0 (L5 baseline)      | 57            | 57             | 57            |
| Iter 1 (thin-spine L8)    | 9 (−84%)      | 11 (−81%)      | 11 (−81%)     |
| Final parent after loop   | **0** (−100%) | **6** (−89%)   | **0** (−100%) |

The loop adds ~10 % additional severity reduction on the two Opus models (closing the last 9–11 severity units that single-round L8 left behind). On Sonnet 4.6 it adds ~9 % (11 → 6) but cannot finish the job — the residual structure of `cr_undermined_by_r_dollar` and `vp_cs_mismatch` was beyond Sonnet's tweak capacity with the current SKILL.md.

## Pareto vs Tchebycheff — identical verdicts on every cell

This is the eval's most important finding, and it wasn't obvious up front:

**Every accept/reject decision is the same under Pareto and Tchebycheff, iteration by iteration, across every cell.**

The Opus cells converge at iter 2 under both; the Sonnet cells accept iter 2, reject iter 3, reject iter 4 under both. The reasoning strings differ (Pareto reports "no improvement: every heuristic's severity identical — child is a no-op"; Tchebycheff reports "weighted Tchebycheff cost N ≥ parent cost N, binding heuristic cr_undermined_by_r_dollar"), but the verdict columns are identical.

Why? The two verifiers diverge only when a candidate iteration **trades a regression on one heuristic for a larger improvement on another** — Pareto rejects such trades (max_regression=1 fallback requires a strict total improvement), Tchebycheff may accept if the weighted-max residual drops enough.

None of the six cells produced such a trade. Opus 4.6/4.7 produced Pareto-dominant tweaks in one shot. Sonnet 4.6 produced a partial-improvement tweak at iter 2 (channel_gap 3→0, cr_undermined 5→3, vp_cs 3→3 — all monotonic or equal) and then at iter 3/4 produced tweaks the re-audit scored identically to the parent (structurally equivalent, scored as a no-op). Neither failure mode is a Pareto-vs-Tchebycheff distinguishing case.

The practical implication: **LLM tweak-generators under temperature 0 do not naturally propose heuristic trade-offs.** They either resolve things Pareto-dominantly, or they fail to change the re-audit outcome at all. A tweak model that deliberately barters (e.g. accept a small accessibility cost to eliminate a major dark-pattern) would be the case where Tchebycheff shines. The current `design-tweak` SKILL.md does not nudge the model toward that kind of proposal, and it's unclear that encouraging it would be valuable — preserved-heuristic regressions are almost always a bug, not a strategy.

**So the Tchebycheff verifier ships as a design-time option, not a practical improvement.** The weighted Tchebycheff cost is still a more informative reporting metric (it surfaces the *binding heuristic* on every verdict, which Pareto does not), which is enough reason to keep it available.

## What the tweaks did — per-model qualitative notes

### Opus 4.7 (iter 2, severity 11 → 0)

`design-tweak` identified `cr_undermined_by_r_dollar` as the binding residual (Tchebycheff's max: 5² = 25). It moved the subscription CTA off the lesson-complete screen entirely to a neutral "Plans" entry in the profile menu — severing loss-framing/paywall adjacency. Simultaneously addressed `channel_gap` (weekly streak-freeze + earnable freezes + 48-hour free "Restore streak" path surfaced in copy) and `vp_cs_mismatch` (no paid upsell surfaces in the learning flow). The four already-resolved heuristics (modal_excise, competing_calls_to_action, ego_depletion_mid_task, scarcity_timer) were preserved verbatim.

One cross-cutting change hitting three residuals. This is the kind of fix the loop can find but single-round L8 cannot — L7 generated one decision per principle, so residual entanglement was not its natural output.

### Opus 4.6 (iter 2, severity 9 → 0)

Functionally equivalent tweak to Opus 4.7 — different surface prose, same structural moves (subscription relocated, free recovery foregrounded). Re-audit scored it as a full resolution. Both verifiers ACCEPT with identical termination (`converged`). Matches Opus 4.7 qualitatively even though iter 1 scores differed (Opus 4.6 got sum 9 at iter 1, Opus 4.7 got sum 11).

### Sonnet 4.6 (iter 2 accept + iter 3/4 reject, stall at severity 6)

Iter 2 was a partial win: `channel_gap` fully resolved (3→0), `cr_undermined_by_r_dollar` softened but not removed (5→3), `vp_cs_mismatch` not addressed (3→3, unchanged). The preserve-contract held on the four resolved heuristics.

Iter 3 and iter 4: the model proposed further refinements (distinct prose from iter 2, explicitly targeting `cr_undermined` and `vp_cs_mismatch`), but the re-audit scored the resulting snapshots with identical severities to iter 2's parent. The re-audit's reasoning on these rejected rounds (from the native payload) notes that the structural change was present in iter 2 already — subsequent tweaks surface-polished phrasing without moving the underlying architecture, so the auditor correctly scored them as no-ops.

This is Sonnet 4.6 hitting the ceiling of its ability to generate *structurally novel* tweaks under the design-tweak SKILL.md. Opus-class models can compose one additional architectural move (subscription-to-separate-screen); Sonnet cannot for this cluster.

## Costs

- Opus cells: 1 new iteration = 2 Claude calls (design-tweak + design-optimize re-audit), ≈ $0.15 per cell × 4 cells = ~$0.60.
- Sonnet cells: 3 new iterations = 6 Claude calls per cell (one per round × two per round) × 2 verifiers = 12 Sonnet calls, ≈ $0.20.

Rough total: **~$0.80** for the 6-cell grid. Well under the ~$2.5–3 upper bound.

## Scope gaps and next steps

1. **One cluster.** cluster_02 was the pilot throughout L4→L8. Running the loop on cluster_06 ("onboarding friction") or cluster_11 ("social-comparison spirals") — clusters with more inter-heuristic tension in L5 — would stress the verifier-switch more aggressively. Those clusters are more likely to surface a Pareto/Tchebycheff divergence on a mid-round iteration.
2. **Tweak-model regression-injection test.** A deliberately-prompted tweak model that barters (fix A by regressing B) would directly demonstrate the verifier divergence. Not run in this eval; would need a tweak-prompt variant.
3. **No ablation on `min_improvement_pct`.** The 10 % default was never exercised as a soft-accept threshold — rejections here are all 0 % improvement (ties or no-ops), so any non-zero threshold would reject. Lowering to 1 % or a percentile-of-parent-sum formulation would let Tchebycheff accept "tiny but real" improvements Pareto still rejects as no-op.
4. **Sonnet 4.6 loop ceiling.** The Sonnet stall at sum 6 is an interesting bench for future prompt-engineering on `design-tweak` — targeted coaching on "produce architecturally novel tweaks, not surface polish" might close the last 6 units.

These are straightforward extensions; the thin-spine + loop stack closes ADR-009's multi-round follow-up action item, and each of the gaps above is a bounded matched-model rerun.

## Artifacts

- Iterations (iter 2+ only, not the thin-spine input): `data/derived/l8_loop/l8_loop_iterations_cluster02_{modelshort}_{verifier}.jsonl`
- Native payloads: `…native.jsonl`
- Provenance (including all loop parameters): `…provenance.json`
- Per-verifier artifact markdown: `data/derived/l8_loop/artifacts/{modelshort}_{verifier}/cluster_02_iter{NN}.md`
- Module: `src/auditable_design/layers/l8_optimize_loop.py`
- Verifier: `src/auditable_design/evaluators/tchebycheff.py` (new) and existing `…/pareto.py`
- Skill: `skills/design-tweak/SKILL.md`
- Tests: `tests/test_tchebycheff.py` (27), `tests/test_l8_optimize_loop.py` (42)

## Skill hashes

- `design-tweak`: `1f328ac141e728d6ba38ba71d695fa0a9154cc1881bccc894f990dc3c911b81b`
- `design-optimize` (re-used from thin spine): `db550438e48eca9302e40a7244d1c0cf85616f0c5b0f49af77898df9f696fdee`
