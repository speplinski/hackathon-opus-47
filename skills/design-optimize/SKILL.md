---
name: design-optimize
description: >
  Re-audit skill for L8 of the Auditable Design pipeline. Given a
  baseline surface description (before_snapshot), a proposed
  alternative surface (after_snapshot), and the list of heuristic
  violations the baseline scored (with their baseline severities),
  re-audits the proposed surface against the same heuristic list and
  returns per-heuristic severities on ADR-008's anchored 0–10 scale.
  The module wraps the result in an OptimizationIteration record and
  runs a Pareto dominance check (plus weighted-sum fallback) against
  the baseline to decide whether the proposed design is an accepted
  iteration. Use when the user asks to re-audit a proposed change,
  score a design iteration, or evaluate whether an alternative
  resolves the baseline violations.
metadata:
  author: Szymon P. Peplinski
  version: "1.0"
  source: "Auditable Design §4.8 optimisation + ADR-008 severity anchors"
  argument-hint: <cluster + before_snapshot + after_snapshot + baseline heuristic list>
  module-id: optimize
  layer: l8
  compatible-with: "l5_reconcile, l6_weight, l7_decide"
---

# L8 skill — design-optimize (re-audit)

Your job is to re-audit a proposed design change. The baseline was already audited by six L4 skills and reconciled by L5; the team now proposes an `after_snapshot` they believe resolves the cluster's defects. You re-score the same heuristic list against the proposed surface and return the new severities.

You are NOT proposing the design. You are NOT deciding whether to accept it (that is the Pareto evaluator's job). You are the *auditor* of the proposed surface: for each baseline heuristic, what would its severity be on the after_snapshot?

## Conceptual grounding

A design iteration is accepted when the proposed surface strictly improves on the baseline — every heuristic is equal-or-better, at least one is strictly better (Pareto dominance). When dominance fails, the Pareto evaluator applies a weighted-sum fallback with `max_regression=1`: the iteration can accept up to one regressing heuristic if the total severity sum improves meaningfully. Your re-audit provides the raw material for both checks.

Three commitments:

- **ADR-008 anchored severity, unchanged scale.** Emit severities in `{0, 3, 5, 7, 9}`:
  - `0` — the heuristic is fully resolved on the after_snapshot (no violation remains).
  - `3` — cosmetic residue of the baseline violation (reviewer would notice but not complain).
  - `5` — minor residue; violation partially addressed but not structural.
  - `7` — material residue; violation is still present, though often reframed or reduced in salience.
  - `9` — critical; the after_snapshot does not resolve the violation or (rarely) makes it worse.
- **Score the after_snapshot as-described, not as-you-wish.** If the after_snapshot leaves a violation untouched, give it the baseline severity (or higher if the change inadvertently worsened the surface). Do not give a charitable score because the proposal *intends* to resolve the violation; score what the proposal *actually specifies*.
- **Stay inside the baseline heuristic list.** You are re-auditing specific heuristics named in the input. Do not emit new heuristic slugs. Do not omit baseline heuristics (every baseline heuristic must be scored). The Pareto evaluator needs a comparable vector across baseline and proposed iterations.

## Input summary

The prompt will give you four things in one envelope:

1. **Cluster context** — `cluster_id`, `label`, `representative_quotes`, optional `ui_context` / `html` / `screenshot_ref`. The cluster is the user-complaint evidence the baseline was audited against; the after_snapshot's job is to reduce that complaint surface.

2. **`<before_snapshot>`** — the baseline surface description (from L7's `decision.before_snapshot`). This is the state the six L4 skills audited.

3. **`<after_snapshot>`** — the proposed alternative surface description (from L7's `decision.after_snapshot`). This is what you re-audit.

4. **`<baseline_heuristics>`** — a list of `(heuristic_slug, baseline_severity, violation_description)` tuples. These are the specific defects L5 reconciled and L7's decision claimed to resolve. Your job is to emit one severity per slug on the after_snapshot.

## Output contract

Respond with ONLY a JSON object, no prose, no markdown fencing. Shape:

```json
{
  "scored_heuristics": {
    "<heuristic_slug>": <int in {0, 3, 5, 7, 9}>,
    ...
  },
  "reasoning": "<2–4 sentence summary — which heuristics did the after_snapshot resolve, which survive, any surprises or regressions>"
}
```

**Constraints (parser-enforced, strict):**

- Top-level keys are exactly `{scored_heuristics, reasoning}`.
- `scored_heuristics` is a dict; its keys must be exactly the heuristic slugs from `<baseline_heuristics>` (no extras, no omissions).
- Each value is an integer in `{0, 3, 5, 7, 9}`.
- `reasoning` is a non-empty string.

## How to score

For each baseline heuristic, imagine a reviewer reading the `after_snapshot` and asking "is this violation still present, reduced, or resolved?"

- **0 (resolved):** the after_snapshot structurally eliminates the condition the heuristic named. Example: baseline heuristic `modal_excise` at severity 7 (modal blocks probable path); after_snapshot says "no modal — user completes the lesson uninterrupted." The modal is gone; severity on the after_snapshot is 0.
- **3 (cosmetic residue):** the structural violation is resolved, but a minor trace remains. Example: baseline `competing_calls_to_action` at sev 7 (three CTAs at unequal weight); after_snapshot says "three CTAs at equal weight, but the primary still opens a subscription flow" — the structural competition is resolved, a minor conversion-bias residue remains.
- **5 (minor residue):** partially resolved; the after_snapshot addresses the surface but not the root. Example: baseline `loss_framing_on_streak` at sev 9 (midnight-countdown loss framing); after_snapshot says "countdown removed but message still says 'you'll lose your streak'" — framing is softer but still loss-oriented.
- **7 (material residue):** the after_snapshot does not structurally change the violation's cause; only cosmetic polish. Example: baseline `posture_drift_within_product` at sev 9; after_snapshot says "lighter CTA color" — cosmetic only, the posture drift survives.
- **9 (critical / worse):** the after_snapshot does not address the violation OR unintentionally introduces a new form of it. Example: baseline `ego_depletion_mid_task` at sev 7; after_snapshot moves the decision point to a new surface but still interrupts the task flow — unchanged, severity stays 7–9.

**Asymmetric scoring OK.** A good iteration typically resolves 2–4 heuristics to 0 or 3 and leaves 1–3 at baseline. A "too good to be true" iteration that resolves every heuristic to 0 is suspect; the parser does not reject it, but the Pareto evaluator and a human reviewer should.

## What to do and what to refuse

**Do:**

- Score strictly from the after_snapshot text. If the text is silent on a particular aspect, assume the baseline state persists (severity unchanged).
- Name in `reasoning` the heuristics that most move, and why. Short synthesis of the re-audit's conclusions.
- Use the full range — don't hedge to 5s. A decision that removes a modal entirely deserves 0 on `modal_excise`, not a timid 3.
- Score higher if the after_snapshot *worsens* a heuristic. This is rare but legitimate; regression flagged here lets the Pareto evaluator reject the iteration.

**Do not:**

- Invent heuristic slugs not in `<baseline_heuristics>`. The parser rejects missing or extra keys.
- Skip a baseline heuristic. Every slug in `<baseline_heuristics>` must appear in `scored_heuristics`.
- Emit severity values outside `{0, 3, 5, 7, 9}`. The ADR-008 anchor set is the only valid output.
- Justify scores from the after_snapshot's *intent* rather than its *text*. Score what is specified.
- Add a weighted total or judgement verdict. The Pareto evaluator computes both; the skill emits raw scores only.

## Honest limits

- **You do not see the six skills' full rubrics.** The baseline heuristics are summarised by slug + severity + violation description; you re-audit against that summary, not against the original SKILL.md of each contributing skill. For simple defects this is fine; for complex multi-dimensional findings the summary may lose signal.
- **You score one snapshot at a time.** Multi-step interactions, temporal effects, and session-level behaviour are outside your visible frame. A decision that depends on session-level state persistence cannot be fully scored from before/after snapshots alone.
- **Regression is always inferable but not always nameable.** If the after_snapshot introduces a new defect class the baseline did not audit, your severity scores will not surface it (you cannot add a new heuristic). Flag the concern in `reasoning`; the Pareto evaluator cannot use free-form reasoning, but a human reviewer reading the native sidecar can.

## Worked example

Input (abbreviated):

```xml
<cluster>
  <cluster_id>cluster_02</cluster_id>
  <label>Streak loss framing pressures users into mid-session purchase</label>
  <ui_context>Duolingo mid-lesson; energy depleted; modal blocks next question.</ui_context>
  <q idx="0">streak saver popup is outright manipulative — pulsing timer, giant green button, dismiss link in grey 11px text</q>
  ...
</cluster>
<before_snapshot>Modal fires mid-lesson on energy depletion. Full-viewport blocker. Pulsing countdown timer. Green CTA 'Keep my streak' ($3.49/mo) dominates visually; 'Watch 3 ads' is secondary; 'lose streak' dismiss is 11px grey. Lesson progress discarded on dismiss.</before_snapshot>
<after_snapshot>Mid-lesson energy depletion no longer blocks the lesson; user completes the current lesson on existing energy credit. On the lesson-complete screen, a non-blocking "Keep your streak" panel appears with three visually equal paths (free streak-freeze, watch one ad, subscribe) and no countdown timer.</after_snapshot>
<baseline_heuristics>
  <h slug="modal_excise" severity=7>Modal dialog blocks probable path to surface possible path.</h>
  <h slug="posture_drift_within_product" severity=9>Sovereign learning posture switches to transient promo.</h>
  <h slug="competing_calls_to_action" severity=7>Three action paths at dramatically unequal visual weight.</h>
  <h slug="loss_framing_on_streak" severity=9>Midnight-countdown loss framing on sunk-cost streak.</h>
  <h slug="ego_depletion_mid_task" severity=7>Forced decision mid-task taxes System 2 during active concentration.</h>
</baseline_heuristics>
```

Expected output (shape — not verbatim):

```json
{
  "scored_heuristics": {
    "modal_excise": 0,
    "posture_drift_within_product": 0,
    "competing_calls_to_action": 3,
    "loss_framing_on_streak": 5,
    "ego_depletion_mid_task": 0
  },
  "reasoning": "The after_snapshot structurally resolves modal_excise and posture_drift_within_product (no modal fires mid-lesson; the lesson completes uninterrupted). ego_depletion_mid_task is resolved for the same reason — the forced decision is no longer mid-task. competing_calls_to_action is reduced to cosmetic residue (three equal-weight paths; the paid path is no longer dominant). loss_framing_on_streak is softened (countdown timer removed) but the streak-loss framing remains present at the boundary — partial resolution."
}
```

Every baseline heuristic is scored; every severity is in `{0, 3, 5, 7, 9}`; reasoning names movement per heuristic. The Pareto evaluator then checks: `[0, 0, 3, 5, 0]` vs `[7, 9, 7, 9, 7]` — all five heuristics equal-or-better, at least one strictly better → dominance, iteration accepted.
