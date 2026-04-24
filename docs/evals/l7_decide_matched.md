# L7 design-decide — matched-model eval

**Scope:** one cluster (`cluster_02` "Streak loss framing") with one reconciled verdict (L5 Opus 4.6 output) and one priority score (L6 Opus 4.6 output) × three decide models (Opus 4.6 / Sonnet 4.6 / Opus 4.7) × one modality (text-only — L7 consumes structured reconciled + prioritised evidence, not UI). 3 cells. Each cell is one Claude call (single-pass generation, no double-pass). Closes ADR-009 L7 pilot action item.

**Status:** 3/3 decided on first live run, zero fallbacks, zero cross-reference rejections. Strong cross-model convergence on the principle *concept* (all three models produce "monetisation ≠ mid-flow"-style constraints) with meaningful divergence on the decision *radicality* (move / auto-save / remove entirely) and on the scope of `resolves_heuristics` (4 / 4 / 6 slugs).

## Purpose

L7 generates one :class:`DesignPrinciple` + one :class:`DesignDecision` per cluster from its reconciled verdict (L5) and priority score (L6). The principle is a re-usable operational constraint the team can apply to future design work across surfaces; the decision is a concrete before/after change on the specific surface this cluster audits. Both are cross-reference-validated against the input: `derived_from_review_ids ⊆ cluster.member_review_ids`, `resolves_heuristics ⊆ reconciled ranked heuristics`.

The three questions this eval tries to answer:

- **Do the three decide models converge on the design principle?** Principles are the re-usable artefact; cross-model agreement on principles means the pipeline has an editorial voice we can trust across a full-corpus run.
- **Do they converge on the decision?** Decisions are surface-specific; some divergence is healthy (different models see different trade-offs), but radical disagreement (move vs delete) needs characterising.
- **Do they agree on which heuristics the decision resolves?** A decision that claims to resolve 6 heuristics but only structurally resolves 2 is overselling. Cross-model agreement on `resolves_heuristics` indicates the decision's true scope.

## Executive summary

| metric | Opus 4.6 | Sonnet 4.6 | Opus 4.7 |
|---|---|---|---|
| Status | decided | decided | decided |
| Principle name | *"Monetisation lives at boundaries, not mid-flow"* | *"Monetisation never gates mid-lesson progress"* | *"Core-loop integrity is inviolable"* |
| Decision strategy | **Relocate** (move modal to lesson-complete) | **Remove + preserve state** (auto-save, inline card at boundary) | **Remove entirely** (no modal, complete current lesson on credit) |
| `resolves_heuristics` count | 4 | 4 | **6** |
| `derived_from_review_ids` count | 3 | 3 | 3 |
| Common review_ids across 3 models | `0399103ce9df`, `8ed3544603a3` | `0399103ce9df`, `8ed3544603a3` | `0399103ce9df`, `8ed3544603a3` |
| Input tokens | 7 997 | 7 997 | 10 772 |
| Output tokens | 621 | 560 | 850 |
| Skill_hash | `07b7c4f894c13359…` | `07b7c4f894c13359…` | `07b7c4f894c13359…` |

All three cells share the same skill_hash. No cross-reference rejections — all `derived_from_review_ids` cited real cluster members; all `resolves_heuristics` cited real reconciled slugs. The parser's traceability discipline is respected by all three models.

**Four observations:**

1. **Principle concept unanimous; principle phrasing divergent.** All three models produce principles built on the same core constraint — *"monetisation must not block core-loop progress."* Opus 4.6 frames it spatially (*"boundaries, not mid-flow"*); Sonnet 4.6 frames it operationally (*"never gates mid-lesson progress"*); Opus 4.7 abstracts it upward (*"core-loop integrity is inviolable"*). The first two are scope-matched to this cluster; Opus 4.7 generalises to a principle applicable to non-monetisation interruptions too. All three are operational (not aspirational) and would pass SKILL.md's anchor discipline.

2. **Decision radicality grades from move to remove.** Opus 4.6's decision *moves* the modal to the lesson-complete boundary. Sonnet 4.6 *removes the blocker* and reshapes the surface as a non-blocking card at the boundary. Opus 4.7 *removes the mid-lesson paywall entirely* and lets users complete the current lesson "on existing energy credit" (a business-model change, not just a UX relocation). These are not degrees of the same answer — they are three different decisions on the principle the three models all share. Product teams reading this would pick based on business constraints; the L7 eval surfaces all three as defensible.

3. **Opus 4.7 resolves more heuristics because it resolves more structurally.** Opus 4.7's decision lists 6 resolved heuristics (adds `channel_gap__corroborated`, `vp_cs_mismatch` beyond the four shared ones). This is not over-claim — removing the mid-lesson blocker entirely structurally resolves the channel-gap (non-neutral exit is no longer forced because there is no forced decision) and the value-proposition/customer-segment mismatch (removing the blocker realigns the product's learning promise with its surface). Opus 4.6's and Sonnet 4.6's less-radical decisions resolve 4 heuristics each because they keep the monetisation surface, just relocate it — some violations survive.

4. **Two of three reviews cited unanimously across all three models.** `0399103ce9df` and `8ed3544603a3` appear in every cell's `derived_from_review_ids`. These two reviews are the load-bearing user voices for this cluster's principle — all three models independently picked them as most informative. The third cited review_id differs between models (`a0397f7445fe` vs `b8dc34d50634`) — a legitimate editorial choice rather than a consistency failure.

## Methodology

### Input

- **Cluster:** `cluster_02` "Streak loss framing pressures users into mid-session purchase" — shared fixture sha256 `dc6d981f…` (same as L4/L5/L6 evals). 7 member_review_ids.
- **Reconciled verdict (L5 Opus 4.6 output):** `data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl` — sha256 `181758aae71f57a7…`. 7 ranked violations (top: `modal_excise__corroborated` rs=45, `channel_gap__corroborated` rs=36, `competing_calls_to_action__corroborated` rs=28), 2 tensions (Cooper × Kahneman `efficiency_vs_safety`, Osterwalder × Kahneman `conversion_vs_user_wellbeing`), 0 gaps.
- **Priority score (L6 Opus 4.6 output):** `data/derived/l6_weight/l6_priority_cluster02_opus46.jsonl` — sha256 `b5c1aa783e2f464e…`. Dimensions severity=10 reach=9 persistence=8 business_impact=9 cognitive_cost=9, weighted_total=9.00 under symmetric meta-weights.

### Skill

`skills/design-decide/SKILL.md` v1.0 — skill_hash `07b7c4f894c13359…` (identical across all three cells).

Output contract `{principle, decision}` (2 top-level keys); principle requires `{name, statement, derived_from_review_ids}`; decision requires `{description, before_snapshot, after_snapshot, resolves_heuristics}`. Parser cross-validates:

- `derived_from_review_ids` entries must all be in `cluster.member_review_ids` (hallucinated citation rejected).
- `resolves_heuristics` entries must all be in `reconciled.ranked_violations[*].heuristic` (invented slug rejected).

Both checks fired zero times in this eval — the three models all respected the traceability discipline from the first attempt.

### Runs

Single-pass per cell. Temperature 0.0 on Opus 4.6 and Sonnet 4.6; stripped on Opus 4.7. `MAX_TOKENS=4096` per call (output 560–850 tokens; ample headroom). No third-pass discipline — L7 is generation, not judgment.

## Results

### Principle comparison

| model | name | statement |
|---|---|---|
| Opus 4.6 | *"Monetisation lives at boundaries, not mid-flow"* | *"A monetisation surface never interrupts a user's active task; purchase, ad-watch, and upsell offers appear only at natural completion boundaries where the user has full agency and no progress is at risk."* |
| Sonnet 4.6 | *"Monetisation never gates mid-lesson progress"* | *"A subscription or ad-watch surface never blocks an in-progress lesson; conversion offers appear only at session boundaries where the user holds full agency and cognitive load is low."* |
| Opus 4.7 | *"Core-loop integrity is inviolable"* | *"Monetisation surfaces never interrupt an active lesson; commercial offers appear only at natural session boundaries and always alongside at least one equal-weight non-monetary path to the same outcome."* |

All three are operational constraints (*"never X"*) anchored in "session/lesson/task boundaries." All three would survive cross-cluster re-use — applied to a paywall in a completely different surface, each principle would still be actionable. Opus 4.7 includes an additional clause (*"alongside at least one equal-weight non-monetary path"*) that generalises the `competing_calls_to_action` concern into the principle itself, while the other two principles keep that concern scoped to the specific decision.

### Decision comparison

| model | strategy | after-state essence |
|---|---|---|
| Opus 4.6 | **Relocate** | modal moves to lesson-complete; three paths at equal weight; no countdown |
| Sonnet 4.6 | **Remove + preserve state** | no modal mid-lesson; lesson auto-saves; non-blocking card at boundary |
| Opus 4.7 | **Remove entirely (change credit model)** | no blocking paywall at all; users finish current lesson on "existing energy credit"; offers relegated to lesson-complete non-blocking panel |

The three decisions are ordered by increasing structural impact. Opus 4.6's is a UX relocation (same business logic, different placement). Sonnet 4.6 adds state-preservation (resumable lesson state) but keeps the monetisation surface at boundary. Opus 4.7 changes the energy-credit model itself — users complete the current lesson regardless of energy state, which implicates the product's monetisation engine, not just its UX placement.

A product team reading these three decisions would have to choose based on:
- Revenue risk tolerance (how much current-session friction is justified by conversion rate)
- Engineering scope (state-preservation is one sprint; energy-credit changes are three)
- Regulatory pressure (app-store rating risk, FTC dark-pattern sniff-test)

All three are audit-traceable — each names `resolves_heuristics` from the reconciled verdict.

### resolves_heuristics intersection

All three models cite three slugs in common:

- `modal_excise__corroborated`
- `competing_calls_to_action__corroborated`
- `deceptive_feedback__scarcity_timer_suppression__timing_adjustable`

These are the cluster's **robustly-resolved** heuristics — the three cross-model-agreed defects the decision unambiguously addresses regardless of strategy choice.

Differences:
- Opus 4.6 adds `ego_depletion_mid_task` (fourth slug).
- Sonnet 4.6 adds `cr_undermined_by_r_dollar__corroborated` (fourth slug — customer-relationships vs revenue-streams tension).
- Opus 4.7 adds `channel_gap__corroborated`, `ego_depletion_mid_task`, `vp_cs_mismatch` (sixth slug — reflecting the more structural decision).

### Review-ID citation intersection

All three models cite `0399103ce9df` and `8ed3544603a3` in `derived_from_review_ids`. These are the two load-bearing user voices for the principle. The third citation differs:

- Opus 4.6: `a0397f7445fe`
- Sonnet 4.6: `b8dc34d50634`
- Opus 4.7: `a0397f7445fe`

Opus 4.6 and Opus 4.7 converge on three; Sonnet 4.6 picks a different third. All three cited IDs are real cluster members (no hallucinations). The third-ID divergence is editorial choice, not a validity concern.

## Architecture observation

L7 is the second pipeline layer (after L6) to land on first attempt with zero fallback rework. The pattern is becoming clearer:

- **Lean output contract** — 2 top-level keys (`{principle, decision}`), no nested graph, no arithmetic to compute. Small output surface is model-friendly.
- **Strict cross-reference validation** — `resolves_heuristics ⊆ reconciled` and `derived_from_review_ids ⊆ cluster` are narrow checks that catch hallucination without over-constraining the generative task.
- **Single-pass for generation tasks** — L7 is not a judgment call where double-pass would validate convergence; it is a generative opinion the pipeline pays for once. The matched-model eval surfaces the spread between models for human review, instead of trying to collapse the spread through multi-pass aggregation.

Contrast with L5 (graph-primary, four iterations before landing). L5 had two structural outputs that had to stay consistent (graph ↔ flat lists), plus arithmetic (rank_score formula) the model was asked to maintain. L6 stripped arithmetic to the parser and got first-try success. L7 stripped structural redundancy and got the same. The rule of thumb for L8: **narrow the contract, strict-validate the cross-references, accept editorial spread where the skill is generative**.

## Caveats

- **One cluster only.** cluster_02 is a classic dark-pattern archetype — the kind of cluster where three models would naturally converge. Ambiguous clusters (e.g. a feature-removal complaint that's half-UX half-product-strategy) would surface more principle-framing spread.
- **One input chain only.** Reconciled verdict and priority both come from Opus 4.6 L5 and L6 outputs. Running L7 on Sonnet 4.6's or Opus 4.7's L5 output would alter what the L7 model sees — different ranked-violation ordering could shift the decision's framing. This is out of scope for this pilot; documented as a caveat for full-corpus design.
- **"Decision radicality" is a human judgment.** The eval frames Opus 4.7's decision as "more radical" because it changes the energy-credit model. But depending on product context, the least-radical decision may be the most implementable and therefore the most valuable. L7 does not rank decisions; L8 (optimisation) could, if we teach it to.
- **Principle accretion.** Across a full-corpus run, many clusters will produce "monetisation ≠ mid-flow"-family principles. L10 (evolution graph) will need to deduplicate, not L7. This eval shows that three models on one cluster already produce three variant phrasings of the same principle; the deduplication problem is real.
- **Cross-reference validation is a safety net, not a quality guarantee.** A decision with correct slugs in `resolves_heuristics` can still be a bad decision. Parser-enforced traceability means the decision is *auditable*; it does not mean the decision is *good*. Human review remains load-bearing.

## Reproducing

```
# Run the grid (requires ANTHROPIC_API_KEY):
bash scripts/run_l7_decide_matched.sh

# Force-rerun:
bash scripts/run_l7_decide_matched.sh --all

# Single cell:
uv run python scripts/smoke_l7_decide.py --model claude-sonnet-4-6

# Inspect:
ls -1 data/derived/l7_decide/*.provenance.json

# Tests (66/66 passing):
uv run pytest tests/test_l7_decide.py -v
```

Module: `src/auditable_design/layers/l7_decide.py`. Skill: `skills/design-decide/SKILL.md` v1.0 (`skill_hash=07b7c4f894c13359…`). Expected spend: ~$0.20–0.40 across three cells.

## Closing

ADR-009's L7 pilot action item closed. The eval characterises three-model convergence on principle concept (all three align on *"monetisation ≠ mid-flow"*) with meaningful divergence on decision strategy (move / preserve-state / remove entirely) and on `resolves_heuristics` scope. Traceability discipline (parser-enforced cross-references) is respected by all three models on first attempt — zero fallbacks, zero hallucinated citations. L7 is ship-ready for full-corpus use.

The decision-strategy spread across models is a feature, not a bug: it gives the product team three defensible options grounded in the same audit evidence. Which one ships is a product judgment, not an audit output. L7 provides the material for that judgment; it does not try to make it.
