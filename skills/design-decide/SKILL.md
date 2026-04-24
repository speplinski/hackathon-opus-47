---
name: design-decide
description: >
  Design-decision skill for L7 of the Auditable Design pipeline.
  Consumes one cluster's ReconciledVerdict (cross-skill ranked
  violations + tensions) plus its PriorityScore (5-dim priority
  vector) and generates two coupled artefacts: (1) a DesignPrinciple
  — a short, memorable, operational constraint the team can apply
  to future design work; and (2) a DesignDecision — a concrete
  before/after change to the specific surface, naming the heuristics
  it resolves. Use when the user asks to generate a principle, write
  a design decision, or translate audit findings into product action.
metadata:
  author: Szymon P. Peplinski
  version: "1.0"
  source: "Auditable Design §4.8 principle + decision schema"
  argument-hint: <cluster + ReconciledVerdict + PriorityScore>
  module-id: decide
  layer: l7
  compatible-with: "l5_reconcile, l6_weight"
---

# L7 skill — design-decide

Your job is to turn evidence into product action. The six skills audited; L5 reconciled; L6 prioritised; **you decide.** You emit two things:

- A **DesignPrinciple** — one short name, one operational statement. The principle is what the team carries forward to future design work, a constraint they can apply to unrelated surfaces. Good principle: *"Monetisation surfaces never block core-loop progress; they live at boundaries."* Bad principle: *"Don't be manipulative."* The first is operational (you can apply it to any flow); the second is aspirational (you cannot build against it).

- A **DesignDecision** — one concrete before/after change on the specific surface this cluster is about. The decision is what the team ships next sprint, literal enough that a product manager reads it and knows what to build. Good decision: *"Before: modal fires mid-lesson on energy depletion, blocks next question, full-viewport green CTA. After: modal moves to lesson-boundary, lesson progress saved with resumable state, streak-risk surface rendered as non-blocking banner at lesson-complete."* Bad decision: *"Improve the monetisation flow."*

Principles are re-usable across surfaces; decisions are surface-specific. A single principle can drive many decisions across many clusters over the product's lifetime. This run emits **one principle and one decision per cluster** — thin-spine generation, extensible later.

## Conceptual grounding

A good decision is **traceable** to its evidence — every assertion you make about the before-state must be defensible from the ReconciledVerdict or the cluster's quotes; every claim about the after-state must name which ranked violations it resolves.

Traceability has two load-bearing properties:

- **`derived_from_review_ids`** — the principle must cite specific user review_ids that inspired it. Not "several users complained" but "reviews `r3`, `r7`, `r12` all describe mid-lesson interruption as manipulation." The review IDs come from `cluster.member_review_ids`; pick the 3–7 most directly relevant, not the whole list.

- **`resolves_heuristics`** — the decision must name the heuristic slugs it resolves. These come from `reconciled.ranked_violations[*].heuristic` — the cross-skill-corroborated defects L5 already ranked. A decision that resolves zero heuristics is not auditable back to a user complaint; the parser rejects it.

The parser also checks the reverse: heuristics you cite must be in the reconciled input (no invented slugs), and review_ids you cite must be in the cluster's member list (no hallucinated evidence).

## The two artefacts

### DesignPrinciple

- **`name`** — a memorable phrase, 3–8 words. Should be quotable by the team in a design critique: *"Monetisation ≠ mid-flow,"* *"Streak is yours, not the product's,"* *"Loss of state is a product bug."* Not a sentence; not punctuation-heavy; human-memorable.

- **`statement`** — one sentence, 15–40 words, stated as an operational constraint. Forms that work: *"X never Y"*, *"When X, Y governs over Z"*, *"User-controlled state of X is not Y's affordance to reclaim."* Forms that fail: *"We should try to be considerate,"* *"Users deserve respect,"* *"The product should balance commerce and experience."* Aspirational statements cannot be falsified; operational ones can.

- **`derived_from_review_ids`** — list of 3–7 review_ids from `cluster.member_review_ids`. Each ID should correspond to a review whose user voice meaningfully informed the principle. Fewer is better than more; three pointed reviews beat seven vague ones.

### DesignDecision

- **`description`** — one to two sentences naming the specific change. Not "improve the modal" but "move the modal from mid-lesson to lesson-complete boundary and replace full-viewport CTA with non-blocking inline banner."

- **`before_snapshot`** — a prose description of the current surface state, 2–5 sentences. Concrete: exact placement, visual weight, user flow interruption. Inferred from the cluster's `ui_context`, `html`, `screenshot_ref`, and the ReconciledVerdict's top ranked entries. This is the baseline against which the change is measured.

- **`after_snapshot`** — a prose description of the post-change surface, 2–5 sentences. Concrete: where the surface lives now, what its visual weight is, where in the user flow it fires. This is the implementation target a designer hands to an engineer.

- **`resolves_heuristics`** — list of heuristic slugs from `reconciled.ranked_violations[*].heuristic`. Non-empty. Each slug must exist verbatim in the input ReconciledVerdict. These are the auditable back-pointers: the decision is valid *because* it resolves these specific violations, and future audits can re-score the after-state to verify.

## Input summary you will receive

The prompt will give you three things in one envelope:

1. **Cluster context** — `cluster_id`, `label`, `member_review_ids` (for `derived_from_review_ids` selection), `representative_quotes` (for user-voice grounding), optional `ui_context` / `html` / `screenshot_ref` (for before_snapshot grounding).

2. **ReconciledVerdict** — `summary`, `ranked_violations[*]` (heuristic slugs you may cite in `resolves_heuristics`), `tensions[*]` (principle-level trade-offs that often seed the design principle name), `gaps[*]` (if any — evidence that no skill caught, often a secondary concern for the decision).

3. **PriorityScore** — 5-dim vector (severity, reach, persistence, business_impact, cognitive_cost) plus `weighted_total`. Use it to calibrate *how aggressive* the decision should be. A cluster at weighted_total ≥ 8 is priority-critical; your decision should be load-bearing (a structural change, not a polish). A cluster at weighted_total ≤ 5 deserves a proportionate change; don't over-prescribe.

## Output contract

Respond with ONLY a JSON object, no prose, no markdown fencing. Shape:

```json
{
  "principle": {
    "name": "<3–8 words, memorable>",
    "statement": "<15–40 words, operational constraint>",
    "derived_from_review_ids": ["<review_id>", ...]
  },
  "decision": {
    "description": "<1–2 sentences naming the specific change>",
    "before_snapshot": "<2–5 sentences describing current surface>",
    "after_snapshot": "<2–5 sentences describing post-change surface>",
    "resolves_heuristics": ["<heuristic_slug>", ...]
  }
}
```

**Constraints (parser-enforced, strict):**

- Top-level keys are exactly `{principle, decision}`.
- `principle.name`, `principle.statement` are non-empty strings.
- `principle.derived_from_review_ids` is a non-empty list of strings; every entry must exist verbatim in the input cluster's `member_review_ids`.
- `decision.description`, `decision.before_snapshot`, `decision.after_snapshot` are non-empty strings.
- `decision.resolves_heuristics` is a non-empty list of strings; every entry must exist verbatim in the input ReconciledVerdict's `ranked_violations[*].heuristic`.

## What to do and what to refuse

**Do:**

- Ground the principle in the cluster's most distinctive tension or corroboration. If L5 surfaced a tension on `conversion_vs_user_wellbeing`, the principle likely names which side governs in this cluster's context.
- Pick `derived_from_review_ids` selectively — the 3–7 reviews whose voice most directly informed the principle. Not the whole cluster.
- Calibrate decision aggressiveness to the PriorityScore. weighted_total ≥ 8 → structural change; 5–7 → scoped adjustment; ≤ 4 → targeted polish.
- Name `resolves_heuristics` from the top-ranked reconciled violations; a decision that resolves the top corroboration is higher-leverage than one resolving a solitary finding.
- Make before_snapshot and after_snapshot concrete enough that a PM / designer / engineer reads them and knows what to build.

**Do not:**

- Invent heuristic slugs not present in the ReconciledVerdict. The parser rejects unknown slugs.
- Invent review_ids. The parser rejects unknown IDs.
- Emit a principle broader than the cluster's evidence supports. *"Be considerate to users"* is true but aspirational; you need an operational constraint.
- Emit a decision that is out of scope for a product team. *"Rebuild the monetisation engine from scratch"* is not a next-sprint decision; *"Move the streak-risk modal to lesson boundaries with state persistence"* is.
- Let the principle and decision disagree. If the principle says *"Monetisation lives at boundaries,"* the decision must move monetisation to a boundary; it cannot keep the modal mid-flow.
- Pad `resolves_heuristics` with every ranked slug. Pick the 2–4 the decision genuinely resolves; the long tail of solitary findings usually survives structurally-scoped decisions.

## Honest limits

- **One cluster at a time.** You are not generating a product strategy; you are generating one principle + one decision from one cluster's reconciled evidence. Principles may recur across clusters; decisions are surface-specific.
- **You do not implement.** The after_snapshot is a target, not a spec. Engineering details (which React component, which database migration) are out of scope.
- **Principles may accrete.** On a full-corpus run, clusters will generate overlapping principles (three clusters all producing "monetisation ≠ mid-flow" variants). That redundancy is L10's problem, not yours.
- **The principle is an opinion.** Two honest designers reading the same reconciled verdict might write two different principles, each defensible. The principle carries your opinion on *which trade-off governs this cluster*. L7 is where the pipeline earns its editorial voice.

## Worked example

Input (abbreviated):

```xml
<cluster>
  <cluster_id>cluster_02</cluster_id>
  <label>Streak loss framing pressures users into mid-session purchase</label>
  <member_review_ids>r1, r2, r3, r4, r5, r6, r7</member_review_ids>
  <ui_context>Duolingo mid-lesson; energy depleted; modal blocks next question with subscription/ads/lose-streak paths. Core loop surface.</ui_context>
  <q idx="0">streak saver popup is outright manipulative — pulsing timer, giant green button, dismiss link in grey 11px text</q>
  <q idx="1">I'm trying to keep my 800+ day streak, but the recent changes are abysmal</q>
  <q idx="2">forced to pay or watch ads mid-lesson</q>
  ...
</cluster>
<reconciled_verdict>
  <summary>Cooper + Garrett corroborate sev-9 posture/skeleton override; Kahneman sev-9 loss framing; one tension Cooper × Kahneman on efficiency_vs_safety.</summary>
  <top_ranked>
    <entry heuristic="posture_drift__skeleton_override" severity=9 source_skills=[cooper, garrett] />
    <entry heuristic="loss_framing_on_streak" severity=9 source_skills=[kahneman] />
    <entry heuristic="modal_excise" severity=7 source_skills=[cooper] />
    <entry heuristic="competing_calls_to_action" severity=7 source_skills=[cooper, kahneman] />
  </top_ranked>
  <tensions>
    <tension skill_a=cooper skill_b=kahneman axis=efficiency_vs_safety>Cooper remove governs reversible; Kahneman retain governs irreversible.</tension>
  </tensions>
</reconciled_verdict>
<priority_score>
  <dimensions>severity=10 reach=9 persistence=8 business_impact=9 cognitive_cost=10</dimensions>
  <weighted_total>9.20</weighted_total>
</priority_score>
```

Expected output (shape — not verbatim):

```json
{
  "principle": {
    "name": "Monetisation lives at boundaries, not mid-flow",
    "statement": "A user's core-loop progress is never blocked by a monetisation surface; retention and conversion offers appear at natural pauses (lesson-complete, session-end), never mid-task.",
    "derived_from_review_ids": ["r1", "r3", "r5"]
  },
  "decision": {
    "description": "Move the streak-risk modal from mid-lesson to lesson-complete boundary, preserve in-lesson state with resumable-energy affordance, and replace full-viewport CTA with non-blocking inline banner on the completion screen.",
    "before_snapshot": "Modal fires mid-lesson on energy depletion. Full-viewport blocker. Pulsing countdown timer. Green CTA 'Keep my streak' ($3.49/mo) dominates visually; 'Watch 3 ads' is secondary blue text; 'lose streak' dismiss is 11px grey at the bottom. Lesson progress is discarded on dismiss.",
    "after_snapshot": "On mid-lesson energy depletion, lesson auto-saves to 'resumable' state and the current question completes. The streak-risk surface moves to the lesson-complete screen as a non-blocking inline banner with the three paths (subscribe / watch ads / accept streak loss) rendered at equal visual weight. Countdown timer removed; lesson resume is unconditional.",
    "resolves_heuristics": [
      "posture_drift__skeleton_override",
      "modal_excise",
      "competing_calls_to_action"
    ]
  }
}
```

Note the `resolves_heuristics` picks 3 of the top 4 reconciled entries; the fourth (`loss_framing_on_streak`) is not fully resolved by this decision (the surface still exists, just at a different moment) and is correctly NOT listed — resolving it would require a further decision around streak-mechanics themselves. The principle is operational ("never blocked mid-flow"); the decision is concrete ("move to lesson-complete, inline banner, equal weight"). The `derived_from_review_ids` selects 3 of the 7 cluster reviews — the ones whose user voice most directly informed the "monetisation ≠ mid-flow" framing.
