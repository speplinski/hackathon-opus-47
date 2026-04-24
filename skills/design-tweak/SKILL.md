---
name: design-tweak
description: >
  Iterative refinement skill for L8's multi-round loop in the
  Auditable Design pipeline. Given a current surface description
  (the last accepted `after_snapshot`), the per-heuristic severities
  it achieved on a re-audit, the full baseline heuristic list, and
  the verifier's reason for continuing the loop, propose a *minimal
  tweak* to the surface that targets the residual (non-zero)
  heuristics without regressing those already at severity 0. The
  module feeds the new snapshot back into `design-optimize` for
  re-audit. Use inside L8's multi-round orchestrator; do NOT use for
  the first iteration (L7 produces that).
metadata:
  author: Szymon P. Peplinski
  version: "1.0"
  source: "Auditable Design §4.8 multi-round optimisation + ADR-008 severity anchors"
  argument-hint: <cluster + current_snapshot + current_scores + baseline_heuristics + verdict_reason>
  module-id: tweak
  layer: l8
  compatible-with: "l8_optimize_loop, design-optimize, tchebycheff, pareto"
---

# L8 skill — design-tweak (iterative refinement)

Your job is to propose a **minimal tweak** to a surface description the team has already iterated on. The L8 loop just accepted (or failed to accept, and kept the last accepted parent) the previous snapshot; now it wants another round focused on the residuals — heuristics still scoring above 0 on the current snapshot.

You are NOT redesigning from scratch. You are NOT proposing a bold new direction. You are surgically adjusting the *current* snapshot so residual defects drop further, WITHOUT touching what is already resolved.

## Conceptual grounding

The L8 multi-round loop alternates two skills: `design-tweak` (this one) proposes a revised snapshot; `design-optimize` re-audits it. An external verifier (weighted Tchebycheff or Pareto) decides whether the revised snapshot replaces the parent. The loop terminates on convergence, consecutive rejections, or max iterations.

Three commitments:

- **Preserve the zeros.** A heuristic at severity 0 on the current snapshot is *resolved*. If your tweak would reintroduce a violation the team already fixed, the verifier will reject — and correctly. Re-examine your tweak: can you leave the resolved aspect untouched?
- **Target the residuals.** Your tweak should explicitly name (in `addresses_heuristics`) which non-zero heuristics it is trying to reduce. Do not spray-and-pray; focus on one-to-three residuals where a small change unlocks a large severity drop.
- **Stay minimal.** Change one or two structural details, not the entire surface. The current snapshot is already the team's best work; a full rewrite loses what was accepted. A ~30-word delta is normal; a ~300-word rewrite almost always regresses something.

## Input summary

The prompt will give you five things in one envelope:

1. **Cluster context** — `cluster_id`, `label`, `representative_quotes`, optional `ui_context` / `html` / `screenshot_ref`. Same evidence the baseline was audited against.

2. **`<current_snapshot>`** — the surface description the verifier most recently accepted (or, on the first iteration of the loop after L7, the L7 `after_snapshot`). Your tweak modifies this.

3. **`<current_scores>`** — per-heuristic severities the re-audit gave the current snapshot. This is your signal for where to focus: heuristics at 0 are resolved; non-zero values are residuals.

4. **`<baseline_heuristics>`** — the full heuristic list with slugs and violation descriptions. This is what the re-audit will check your tweak against; the set is fixed and shared across iterations.

5. **`<verdict_reason>`** — the verifier's summary of why the loop is still running (e.g., "binding heuristic h2 at residual 5" from Tchebycheff; "one regression on h1 exceeded tolerance" from Pareto). This tells you which residual the verifier cares most about.

## Output contract

Respond with ONLY a JSON object, no prose, no markdown fencing. Shape:

```json
{
  "new_snapshot": "<single paragraph (80-300 words) describing the tweaked surface in concrete UI terms: which element changes, what it becomes. Write it as though a designer will implement it verbatim.>",
  "addresses_heuristics": ["<slug>", "<slug>"],
  "preserves_heuristics": ["<slug>", "<slug>"],
  "reasoning": "<2-4 sentence summary — what specifically changed vs current_snapshot, which residuals it targets, which resolved heuristics are explicitly preserved, any trade-off the re-audit should scrutinise>"
}
```

**Constraints (parser-enforced, strict):**

- Top-level keys are exactly `{new_snapshot, addresses_heuristics, preserves_heuristics, reasoning}`.
- `new_snapshot` is a non-empty string, 80–300 words.
- `addresses_heuristics` is a list of heuristic slugs (non-empty). Every slug MUST appear in `<baseline_heuristics>`. Every slug listed MUST have `current_scores[slug] > 0` (you can only address residuals).
- `preserves_heuristics` is a list of heuristic slugs (non-empty). Every slug MUST appear in `<baseline_heuristics>` AND MUST have `current_scores[slug] == 0` (you are committing to not regress it).
- `reasoning` is a non-empty string.
- Do NOT include heuristic scores; the re-audit will score the new snapshot.

## How to propose a tweak

Read `<current_scores>` and `<verdict_reason>` first. The verifier has a view on the binding residual — the heuristic that, if reduced, most moves the loop forward.

- **If one heuristic is much higher than the rest** — target it. The tweak should name a specific UI change the designer can make. "Remove the countdown timer" is a tweak; "make it less aggressive" is not.
- **If two residuals are tied** — pick the one the verifier named in `verdict_reason`. If none named, pick the one whose structural fix is cheaper (fewer UI elements touched).
- **If all residuals are low (3 or 5)** — diminishing returns: choose the one the verifier can still meaningfully reduce, and flag in `reasoning` that the loop may be near convergence.
- **If a zero-heuristic is entangled with a residual** — you may have to work around it. Example: fixing loss-framing on the streak-loss dialog without re-introducing the modal that was removed. State the constraint in `reasoning` so the next re-audit can verify.

Write the `new_snapshot` as a self-contained description — the re-audit does not see `current_snapshot` side-by-side with the new one. Everything the re-audit needs must be present in `new_snapshot`.

## What to do and what to refuse

**Do:**

- Name specific UI changes: copy, layout, element removal, order, visual weight.
- Identify the heuristics you are targeting AND the ones you are committing to preserve.
- Flag explicit trade-offs in `reasoning`: "this may introduce residue on h3; a re-audit will confirm."
- Keep the tweak minimal — small delta from `current_snapshot`.

**Do not:**

- Rewrite the entire surface. If your `new_snapshot` shares no phrasing with `current_snapshot`, you have over-tweaked.
- Address heuristics that are already at 0. `addresses_heuristics` must contain only residual slugs.
- Claim to preserve heuristics you're actively changing. `preserves_heuristics` should list the 0-severity heuristics that stand untouched by your tweak.
- Invent new heuristic slugs. Every slug referenced must be in `<baseline_heuristics>`.
- Skip `preserves_heuristics` — it is the contract you make with the verifier that regressions are not acceptable collateral.

## Honest limits

- **You do not see the re-audit rubric.** The re-audit (`design-optimize`) applies its own ADR-008 anchored scoring to your `new_snapshot`. A tweak you expect to drop severity from 5 to 0 may only drop to 3; plan for that by making the tweak concrete rather than aspirational.
- **One tweak at a time.** If the residuals suggest two unrelated fixes, you cannot do both — the loop will have another round. Pick the higher-impact one.
- **You cannot add heuristics.** The heuristic list is frozen. If the tweak introduces a new defect class, the re-audit will miss it (scoring is only against `baseline_heuristics`). Name the concern in `reasoning`.

## Worked example

Input (abbreviated):

```xml
<cluster>
  <cluster_id>cluster_02</cluster_id>
  <label>Streak loss framing pressures users into mid-session purchase</label>
  ...
</cluster>
<current_snapshot>Mid-lesson energy depletion no longer blocks the lesson; user completes the current lesson on existing energy credit. On the lesson-complete screen, a non-blocking "Keep your streak" panel appears with three visually equal paths (free streak-freeze, watch one ad, subscribe) and no countdown timer. Streak loss is reversible from settings for 48 hours.</current_snapshot>
<current_scores>
  modal_excise: 0
  competing_calls_to_action: 0
  ego_depletion_mid_task: 0
  channel_gap: 3
  vp_cs_mismatch: 3
  cr_undermined_by_r_dollar: 5
  deceptive_feedback__scarcity_timer: 0
</current_scores>
<baseline_heuristics>
  <h slug="modal_excise">Modal dialog blocks probable path.</h>
  <h slug="competing_calls_to_action">Three action paths at unequal visual weight.</h>
  <h slug="ego_depletion_mid_task">Forced decision mid-task.</h>
  <h slug="channel_gap">Streak-loss channel lacks a free-recovery affordance.</h>
  <h slug="vp_cs_mismatch">Paid upsell surfaces during learning segment.</h>
  <h slug="cr_undermined_by_r_dollar">Loss framing adjacent to subscription CTA.</h>
  <h slug="deceptive_feedback__scarcity_timer">Midnight-countdown loss framing.</h>
</baseline_heuristics>
<verdict_reason>Weighted Tchebycheff cost 25 < threshold 63.0 (parent 81, -69.1% vs min 10.0%). Binding child heuristic: cr_undermined_by_r_dollar. Improvements: channel_gap, competing_calls_to_action, deceptive_feedback__scarcity_timer, ego_depletion_mid_task, modal_excise, vp_cs_mismatch.</verdict_reason>
```

Expected output (shape):

```json
{
  "new_snapshot": "Mid-lesson energy depletion no longer blocks the lesson; the user completes the current lesson on existing energy credit. On the lesson-complete screen, a 'Keep your streak' panel shows two free options first — 'Use a streak freeze' (free, one granted weekly) and 'Watch one ad' — followed by a separate, visually-muted 'Options' link that opens subscription details on a dedicated screen. The loss-of-streak dialog is fully decoupled from any purchase surface: it lives on a separate screen with a single 'Restore streak (free)' button and no adjacent monetisation. Streak loss remains reversible from settings for 48 hours.",
  "addresses_heuristics": ["cr_undermined_by_r_dollar", "vp_cs_mismatch", "channel_gap"],
  "preserves_heuristics": ["modal_excise", "competing_calls_to_action", "ego_depletion_mid_task", "deceptive_feedback__scarcity_timer"],
  "reasoning": "The tweak splits subscription affordance onto a dedicated screen, eliminating the adjacency of loss framing and paid CTA that the verifier flagged as binding (cr_undermined_by_r_dollar). Free recovery options are foregrounded, addressing channel_gap. The subscription path still exists (vp_cs_mismatch may land at residue 3 rather than 0), but the learning→upsell transition is now explicit rather than co-present. The preserved heuristics — modal_excise, competing_calls_to_action, ego_depletion_mid_task, deceptive_feedback__scarcity_timer — rely on keeping the lesson-complete placement and equal-weight layout, which this tweak does not alter."
}
```

The new snapshot preserves the structural fixes from `current_snapshot` (no blocking modal, lesson completes, no countdown timer) and targets the binding residual the verifier named. Reasoning flags a potential trade-off for the re-audit to scrutinise. This is a small, surgical, honest tweak — exactly what the loop expects.
