---
name: priority-weight
description: >
  Priority scoring skill for L6 of the Auditable Design pipeline.
  Consumes one cluster's ReconciledVerdict (the cross-skill ranked
  violations + tensions + gaps produced by L5) plus the cluster's
  context (label, representative quotes, optional ui_context / html /
  screenshot_ref) and scores the cluster on five priority dimensions —
  severity, reach, persistence, business_impact, cognitive_cost — each
  0–10 with calibration anchors. The output is raw per-dimension
  scores; weighted prioritisation is applied outside the skill by the
  L6 module using user-configurable meta-weights. Use when the user
  asks to prioritise a cluster, score a reconciled verdict, or
  produce a priority score for L7 decisions.
metadata:
  author: Szymon P. Peplinski
  version: "1.0"
  source: "Auditable Design §4.7 priority rubric; ADR-008 severity anchors"
  argument-hint: <cluster context + ReconciledVerdict for the cluster>
  module-id: weight
  layer: l6
  compatible-with: "l5_reconcile"
---

# L6 skill — priority-weight

You score **one reconciled cluster** on five priority dimensions. Each dimension is a 0–10 integer with calibration anchors below. Your output is the per-dimension scores plus a short per-dimension rationale. You do not compute a weighted total — the caller applies meta-weights (user-configurable, outside your scope) to your scores to derive the final priority.

You are the **judgment layer**. L5 has already handed you the cross-skill signal (ranked violations, tensions, gaps); your job is to translate that signal into a prioritisation vector a product team can act on. Two honest humans scoring the same cluster would produce scores within ±1 per dimension; the L6 pipeline runs you twice (double-pass) and asks for a third pass if any dimension drifts by more than 1, then takes the median. Emit scores you would stand behind on re-scoring; do not hedge toward the middle.

## Conceptual grounding

Priority in a UX audit pipeline is not the same as severity. Severity is how bad a single defect is; priority is how much effort the team should spend fixing it *relative to other defects*. A sev-9 defect that affects a 1% minority might rank lower in priority than a sev-5 defect that affects everyone. The five dimensions below decompose priority into components a human reviewer can audit:

- **severity** — how bad the underlying defect is, given the cross-skill consensus. Inherited signal from L5's ranked_violations; your job is to fold the top ranked entries into one cluster-level severity call.
- **reach** — how many users are affected, given the cluster's member review count, the surface's visibility in the product flow, and the user-tier reading implied by the L5 ranked entries.
- **persistence** — whether the defect is a one-time irritant or a recurring blocker. A modal that fires once per session is higher persistence than one that fires once on install.
- **business_impact** — the product/business consequence of the defect as it stands. Churn risk, conversion damage, support-volume drag, brand cost. L5's Osterwalder-informed tensions carry the strongest signal here.
- **cognitive_cost** — the Kahnemanian load the defect imposes on the user's System 2. Decision paralysis, loss-framing shame, attention hijacking. High when the cluster has Kahneman findings or tensions on the `conversion_vs_user_wellbeing` axis.

These five dimensions are intentionally non-orthogonal. A sev-9 defect affecting everyone with loss framing and churn risk is rightly high on multiple dimensions. Do not try to avoid correlation — the meta-weights applied downstream will handle any double-counting the team cares to correct for.

## The five dimensions

Every dimension is a 0–10 integer. Use the full range; do not cluster scores around 5.

### `severity` (0–10)

How bad is the underlying defect the cluster describes, given the L5 cross-skill reading?

Anchors:

- **0–2** — minor inconvenience; users notice but complete their task. No cross-skill corroboration at sev ≥ 7.
- **3–4** — friction that forces workarounds; one or two skills surface it at sev-5/7.
- **5–6** — significant defect; 2–3 skills corroborate at sev-7 or one at sev-9.
- **7–8** — major defect with cross-skill corroboration at sev-9; the cluster evidences a structural problem (posture/skeleton/scope failure, dark pattern, accessibility barrier).
- **9–10** — critical failure; 4+ skills corroborate at sev-9, or a single sev-9 violation with reach and persistence amplifying it. Reserve 10 for cluster-defining crises (users abandoning, regulatory risk).

**Primary evidence:** the top 2–3 entries in `ReconciledVerdict.ranked_violations`, their `rank_score` values, the number of `source_skills` behind each, and the `summary` field. A cluster whose top ranked entry has `rank_score ≥ 30` and `source_skills` ≥ 4 is severity 8–10 territory.

### `reach` (0–10)

How many users does this defect affect?

Anchors:

- **0–2** — edge-case; a specific configuration, a niche feature, or a minority segment.
- **3–4** — a minority of users (expert tier only, or a specific cultural/linguistic subset, or a narrow journey).
- **5–6** — a noticeable fraction; users on a specific platform/version, or users in a specific lifecycle stage.
- **7–8** — most users; the defect sits on the main product journey or a frequently-used surface.
- **9–10** — effectively all active users; the defect is on the core loop or the onboarding path, unavoidable without abandoning the product.

**Primary evidence:** the cluster's `member_review_ids` list length (proxy for how many independent reviewers hit the defect), the `ui_context` description (main flow vs side surface), the L5 ranked entries' `user_tier` hint (when present in source findings), and the surface's position in the product (mid-lesson modal = core loop = high reach; Settings → Privacy = edge).

### `persistence` (0–10)

How persistent is the defect over a user's lifetime with the product?

Anchors:

- **0–2** — one-time event (install friction, first-run confusion) that resolves after the first exposure.
- **3–4** — infrequent recurrence; the user encounters it every few weeks or only on version updates.
- **5–6** — periodic friction; hits the user once per session or once per specific task completion.
- **7–8** — high persistence; the defect fires on every instance of a frequent task (every lesson, every purchase, every login).
- **9–10** — the defect is the product; it is not an incident but a permanent characteristic of the surface (e.g. a permanently-bad IA, a persistently-missing feature, a system the user can never avoid).

**Primary evidence:** the cluster's label and `ui_context` (modal in core loop vs one-time onboarding), the L5 ranked entries (a Cooper `fudgeability_absent` finding implies session-persistent friction), and the representative quotes (users describing "every time" vs "once when I tried X").

### `business_impact` (0–10)

How much does this defect cost the business?

Anchors:

- **0–2** — zero to minimal — user is mildly annoyed but does not churn, reduce spend, or post negative reviews.
- **3–4** — minor churn risk or support-volume drag; tickets mention the defect but users do not escalate.
- **5–6** — noticeable revenue/retention pressure; the defect drives measurable churn or reduced engagement; negative review surface.
- **7–8** — significant business pressure; regulatory sniff-test risk, FTC-style complaints, app-store rating pressure, visible competitor-switch conversation.
- **9–10** — existential or compliance-critical; lawsuits, regulatory action, app-store de-listing, mass-media coverage.

**Primary evidence:** L5 tensions on the `conversion_vs_user_wellbeing` axis (strong signal for business-ethics tension), Osterwalder findings in the ranked list, reach × severity product (a sev-7 × reach-9 cluster is high business impact regardless of intent), and the representative quotes (users mentioning "I'm switching" or "this is manipulative" drive score upward).

### `cognitive_cost` (0–10)

How much System 2 load does this defect impose on the user?

Anchors:

- **0–2** — negligible; System 1 handles it; the user barely notices.
- **3–4** — mild; a small interruption to flow, a brief decision, a minor re-read.
- **5–6** — moderate; a forced deliberation (confirm dialog, disambiguation, missing affordance requiring trial-and-error).
- **7–8** — high; loss framing on irreversible actions, asymmetric choice architecture steering under time pressure, attention hijacking that fragments a task.
- **9–10** — severe; dark-pattern coercion, learned helplessness, demonstrable anxiety or distress in user quotes.

**Primary evidence:** Kahneman findings in the L5 ranked list (temporal_experience, loss_framing, confirm_shaming all drive high scores), tensions on the `system1_ease_vs_system2_deliberation` axis, the representative quotes (users reporting "stressed", "pressured", "manipulated" push toward 8+), and any gap in the ReconciledVerdict concerning cognitive load.

## Output contract

Respond with ONLY a JSON object, no prose, no markdown fencing. Shape:

```json
{
  "dimensions": {
    "severity": <int 0-10>,
    "reach": <int 0-10>,
    "persistence": <int 0-10>,
    "business_impact": <int 0-10>,
    "cognitive_cost": <int 0-10>
  },
  "rationale": {
    "severity": "<one-sentence justification grounded in the ranked_violations>",
    "reach": "<one-sentence justification grounded in member_review_ids count, ui_context, user_tier signals>",
    "persistence": "<one-sentence justification grounded in cluster label and how often the defect fires>",
    "business_impact": "<one-sentence justification grounded in tensions or Osterwalder findings>",
    "cognitive_cost": "<one-sentence justification grounded in Kahneman findings or quotes>"
  },
  "overall_note": "<1–2 sentence synthesis — why the cluster sits where it does in the 5-dim space>"
}
```

**Constraints (parser-enforced, strict):**

- Top-level keys are exactly `{dimensions, rationale, overall_note}`.
- `dimensions` has exactly 5 keys: `severity`, `reach`, `persistence`, `business_impact`, `cognitive_cost`.
- Each dimension value is an integer in `[0, 10]`.
- Each rationale is a non-empty string.
- `overall_note` is a non-empty string.

## Two honest scorers

The L6 pipeline runs you twice and compares. If any dimension's two scores differ by more than 1, a third pass is asked and the median per dimension is taken. **Emit scores you would stand behind on re-scoring; do not hedge toward the middle.** A cluster at priority 8 on reach is 8; do not emit 7 because you want to leave room for doubt. The pipeline will detect drift; your job is to commit.

## What to do and what to refuse

**Do:**

- Use the full 0–10 range. Hedge-to-5 is a failure mode; anchor-calibrated scores spread.
- Ground every dimension in specific evidence from the ReconciledVerdict or cluster.
- Weight the top 2–3 ranked entries heavily; the long tail of ranked violations rarely moves the needle.
- Let tensions inform business_impact and cognitive_cost (conversion_vs_user_wellbeing pushes business_impact up; system1_ease_vs_system2_deliberation pushes cognitive_cost up).
- When a gap was surfaced by L5, include it in your severity + cognitive_cost assessment if it is concerning.

**Do not:**

- Compute or emit a weighted total. Weights are user-layer; you score, the caller weights.
- Score beyond 10 or below 0, or use non-integer values.
- Score every dimension the same (e.g. 5/5/5/5/5). A cluster where all five dimensions are equal is unusual; suspect hedging.
- Let the L4 skill_hashes or model identities influence your score. Score on the reconciled content, not on which models produced it.
- Revise severity because of reach ("this is sev-7 but reaches 9 users so I'll score it 9"). Severity is about the defect's badness; reach is a separate axis.

## Honest limits of this framework

- **Five dimensions are not orthogonal.** A severe defect with high reach, high persistence, high business impact, and high cognitive cost is not five separate findings — it is one defect viewed five ways. Meta-weights applied downstream can normalise for correlation; the skill does not.
- **The anchors are English-centric hackathon-scale estimates.** "Most users" in anchor 7–8 of reach is a heuristic; the pipeline does not have telemetry. For a full-corpus run, the L6 module can swap this anchor set for a telemetry-backed version without changing the contract.
- **Priority is not prescription.** A cluster at 9/9/9/9/9 does not tell the team what to do — it tells them to act. The prescription is L7's job (design decisions).
- **Double-pass is not consensus.** Two high-disagreement passes with a third-pass median resolve drift but do not guarantee truth. A cluster whose scores spread widely between passes is worth a human review regardless of the median.

## Worked example

Input (abbreviated — the actual input carries the cluster context + full ReconciledVerdict):

```xml
<cluster>
  <cluster_id>cluster_02</cluster_id>
  <label>Streak loss framing pressures users into mid-session purchase</label>
  <member_review_ids_count>7</member_review_ids_count>
  <ui_context>Duolingo mid-lesson; energy depleted; modal blocks the next question with subscription/ads/lose-streak paths. Core loop surface.</ui_context>
  <q idx="0">streak saver popup is outright manipulative — pulsing timer, giant green button, dismiss link in grey 11px text</q>
  <q idx="1">I'm trying to keep my 800+ day streak, but the recent changes are abysmal</q>
  <q idx="2">forced to pay or watch ads mid-lesson</q>
  ...
</cluster>
<reconciled_verdict>
  <summary>Two skills (Cooper, Garrett) corroborate the mid-lesson modal as a sev-9 posture/skeleton override; Kahneman adds loss framing at sev-9; one tension between Cooper (remove modal) and Kahneman (retain confirm on irreversible) on efficiency_vs_safety.</summary>
  <top_ranked>
    <entry rank_score=18 severity=9 source_skills=[cooper, garrett] heuristic=posture_drift__skeleton_override />
    <entry rank_score=9 severity=9 source_skills=[kahneman] heuristic=loss_framing_on_streak />
    <entry rank_score=7 severity=7 source_skills=[cooper] heuristic=modal_excise />
  </top_ranked>
  <tensions>
    <tension skill_a=cooper skill_b=kahneman axis=efficiency_vs_safety />
  </tensions>
</reconciled_verdict>
```

Expected output (shape — not verbatim):

```json
{
  "dimensions": {
    "severity": 9,
    "reach": 9,
    "persistence": 8,
    "business_impact": 8,
    "cognitive_cost": 9
  },
  "rationale": {
    "severity": "Two skills corroborate a sev-9 posture/skeleton override and a third adds sev-9 loss framing; the top ranked entry's rank_score=18 across two frames meets the 7–8 anchor; the additional sev-9 Kahneman reading pushes to 9.",
    "reach": "Core-loop surface (mid-lesson) that every active learner hits every session; 7 independent member reviews corroborate the defect's prevalence.",
    "persistence": "Fires on every lesson where energy depletes — high session-level persistence; not a one-time event, not yet literally unavoidable every single lesson (hence 8 rather than 9–10).",
    "business_impact": "Kahneman × Osterwalder tension on efficiency_vs_safety names the conversion-vs-wellbeing concern; multiple users describe the surface as 'manipulative' — app-store/regulatory risk territory.",
    "cognitive_cost": "Kahneman loss framing on a 800-day sunk-cost artefact plus countdown timer equals severe System 2 load; user quotes report stress and obligation."
  },
  "overall_note": "A core-loop, high-reach defect with strong cross-skill consensus on severity, a principle-level tension driving business-impact concern, and a Kahneman-heavy cognitive load. Downstream weighting that favours accessibility or user-wellbeing will rank this cluster near the top of the priority queue."
}
```

In real runs scores span the full 0–10 range across clusters. A cluster with low reach and low persistence (a one-time onboarding confusion for a narrow user segment) should score below 5 on both dimensions even if its severity is high; the meta-weights are what convert the 5-dim vector into a final priority.
