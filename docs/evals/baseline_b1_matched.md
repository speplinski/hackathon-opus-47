# Baseline B1 — naive single-shot prompt, matched eval

The pitch differentiator for Auditable Design is not "LLM proposes
a better paywall" — a sufficiently capable LLM can do that in one
shot, especially on a tight cluster of complaints. The differentiator
is **traceability + iterative refinement**: every design decision
comes with a typed path back to audited user evidence, and every
refinement is verifier-gated.

B1 quantifies what the naive single-shot floor looks like, for
comparison with the full L8 thin-spine output and the L8 multi-round
loop.

## Method

For each of the three matched models (Opus 4.6, Sonnet 4.6, Opus
4.7):

1. **Naive generation.** One Claude call with a minimal system
   prompt (`"You are a senior product designer. Redesign this
   surface."`) and a user message carrying cluster_02's
   representative quotes + `ui_context` + the `before_snapshot`
   taken from L7 (for apples-to-apples with the pipeline's
   baseline framing). No audit skills, no reconciliation, no
   principles, no preserve-contract.
2. **Re-audit.** A second Claude call under the existing
   `design-optimize` skill, scoring B1's `after_snapshot` against
   the **same** 7-heuristic baseline list the full pipeline uses
   (L5 reconciled verdict, severities 9/9/7/9/7/9/7 → sum 57).
3. **Verdict.** Both Pareto (max_regression=1) and weighted
   Tchebycheff (min_improvement_pct=10.0) verdicts are computed
   against the L5 baseline scores — same verifiers the pipeline
   uses.

Grid: 1 cluster × 3 models = 3 cells. ~$0.50 total spend.

## Headline table

| Model       | B1 words | B1 severity sum | Reduction vs L5 | L8 thin-spine sum | L8-loop final sum | Verifier verdict |
|-------------|----------|-----------------|------------------|-------------------|-------------------|------------------|
| Opus 4.6    | 311      | 9               | −84 %            | 9                 | 0                 | Pareto ✓ / Tch ✓ |
| Sonnet 4.6  | 247      | 19              | −67 %            | 11                | 6                 | Pareto ✓ / Tch ✓ |
| Opus 4.7    | 248      | **3**           | **−95 %**        | 11                | 0                 | Pareto ✓ / Tch ✓ |

All three B1 outputs are accepted by both verifiers vs the L5
baseline — the naive prompt resolves most heuristics. The story is
**which heuristics** each approach leaves behind.

## Per-heuristic score matrix — all 7 heuristics × all 3 models

Baseline severities from L5 (iteration 0): **9 / 9 / 7 / 9 / 7 / 9 / 7 = 57.**
Delta in parentheses is `child − parent`.

| Heuristic                             | L5 (iter 0) | B1 Opus 4.6 | B1 Sonnet 4.6 | B1 Opus 4.7 | L8-loop Opus 4.7 |
|---------------------------------------|-------------|-------------|----------------|--------------|-------------------|
| `modal_excise__corroborated`          | 9           | **0** (−9)  | 3 (−6)         | **0** (−9)   | **0** (−9)        |
| `channel_gap__corroborated`           | 9           | **0** (−9)  | 5 (−4)         | **0** (−9)   | **0** (−9)        |
| `competing_calls_to_action__corroborated` | 7       | **0** (−7)  | **0** (−7)     | **0** (−7)   | **0** (−7)        |
| `cr_undermined_by_r_dollar__corroborated` | 9       | 3 (−6)      | 3 (−6)         | **0** (−9)   | **0** (−9)        |
| `deceptive_feedback__scarcity_timer`  | 7           | **0** (−7)  | **0** (−7)     | **0** (−7)   | **0** (−7)        |
| `vp_cs_mismatch`                      | 9           | 3 (−6)      | 5 (−4)         | 3 (−6)       | **0** (−9)        |
| `ego_depletion_mid_task`              | 7           | 3 (−4)      | 3 (−4)         | **0** (−7)   | **0** (−7)        |
| **Sum**                               | **57**      | **9**       | **19**         | **3**        | **0**             |

### L8 thin-spine side-by-side (same 3 models)

| Heuristic                             | L5 | L8-thin opus46 | L8-thin sonnet46 | L8-thin opus47 |
|---------------------------------------|----|----------------|------------------|----------------|
| `modal_excise`                        | 9  | 0              | 0                | 0              |
| `channel_gap`                         | 9  | 3              | 3                | 3              |
| `competing_calls_to_action`           | 7  | 0              | 0                | 0              |
| `cr_undermined_by_r_dollar`           | 9  | 3              | 5                | 5              |
| `deceptive_feedback__scarcity_timer`  | 7  | 0              | 0                | 0              |
| `vp_cs_mismatch`                      | 9  | 3              | 3                | 3              |
| `ego_depletion_mid_task`              | 7  | 0              | 0                | 0              |
| **Sum**                               | 57 | **9**          | **11**           | **11**         |

## What the numbers say — qualitative reading

Seven heuristics form **two clusters of difficulty**:

**Structurally easy** — every approach resolves them to 0, regardless of model:

- `modal_excise` — remove the blocking modal; obvious structural fix.
- `competing_calls_to_action` — equal-weight CTAs; spec-able in one sentence.
- `deceptive_feedback__scarcity_timer` — remove the countdown; spec-able in one sentence.

On these three (severity 9+7+7 = 23 of the 57 baseline), every
model under every approach scores 0. These are the low-hanging
fruit a competent designer fixes automatically.

**Structurally hard** — persistent residuals, where approach matters:

| Heuristic                             | Why it persists                                                                         |
|---------------------------------------|-----------------------------------------------------------------------------------------|
| `channel_gap`                         | Needs a free recovery path the user can find; not just "remove modal"                   |
| `cr_undermined_by_r_dollar`           | Needs loss-framing to stop adjoining the paywall CTA — structural rearrangement         |
| `vp_cs_mismatch`                      | VP vs CS is a model-of-product question; the surface only reflects it                   |
| `ego_depletion_mid_task`              | Needs the decision to move OFF the fatigued moment; a location shift, not a copy change |

These four heuristics (sum 34 of 57) are where the models and
approaches diverge.

## Per-model narrative

### Opus 4.6 — pipeline and naive prompt tie

B1 scored 9 exactly — matching the L8 thin-spine. But the
distribution is **different**:

- L8 thin-spine opus46 residuals: `channel_gap=3`, `cr_undermined=3`, `vp_cs=3`. Sum 9.
- B1 opus46 residuals: `cr_undermined=3`, `vp_cs=3`, `ego_depletion=3`. Sum 9.

The thin-spine fixed `ego_depletion` structurally (moved the
decision off mid-task via L7's principle-first decomposition) but
left `channel_gap` at 3. B1 fixed `channel_gap` (the naive prompt
surfaced a free streak-freeze path) but kept `ego_depletion` at 3
(naive redesign didn't move the decision off mid-task). **Neither
is uniformly better; they trade different residuals for each
other.**

This matters for the pitch: "same severity number" hides
distributions that audit-trace can surface.

### Sonnet 4.6 — pipeline helps weaker models most

B1 on Sonnet scored 19 — substantially worse than L8 thin-spine's
11 or L8-loop's 6. Particularly weak on:

- `modal_excise` left at 3 — Sonnet's B1 output kept some
  modal-like surface; the pipeline's L7 decision explicitly spec-d
  its removal.
- `channel_gap` left at 5 — half-measure: Sonnet surfaced a
  recovery but buried it in copy the re-audit flagged.
- `vp_cs_mismatch` left at 5 — Sonnet couldn't sustain the VP/CS
  framing without the reconciliation step.

On Sonnet, **every stage of the pipeline beats naive**. This is
the expected textbook result; not every bench on every model is a
surprise.

### Opus 4.7 — honest surprise

**B1 on Opus 4.7 scored 3, beating L8 thin-spine's 11.** The single
residual: `vp_cs_mismatch=3`.

Two plausible explanations, both consistent with the numbers:

1. **L8 thin-spine is architecturally constrained.** L7 generates
   one decision per design principle; the `after_snapshot` is
   therefore scoped to "what this principle would change." On
   Opus 4.7's thin spine, L7 resolved the three easy heuristics
   (modal, ccta, scarcity) plus ego_depletion (moved decision to
   post-lesson screen), but left channel_gap / cr_undermined /
   vp_cs at residuals 3/5/3 — residue of framing the decision as
   "resolve one principle" rather than "address every complaint in
   this cluster."
2. **Opus 4.7 tweak-capacity exceeds L7's decomposition.** The
   pipeline's L7→L8 split is valuable when a single model cannot
   hold every heuristic in mind simultaneously. Opus 4.7
   evidently can for this cluster — the naive prompt produced one
   paragraph that addressed all 7 heuristics, with only
   `vp_cs_mismatch` stubbornly landing at 3 because the prose
   could not fully reconcile "free learning VP" against "paid
   upsell still reachable elsewhere in the redesigned flow."

The **L8 loop** then closes the gap: iter 2 on Opus 4.7 drops
`vp_cs_mismatch` to 0 by moving subscription to a separate "Plans"
screen accessed only via the profile menu. This is a structural
move the naive prompt didn't make because naive had no pressure
to polish the 3-residual it already scored.

### Ranking (Opus 4.7)

```
L8-loop  (0)  <  B1 naive  (3)  <  L8 thin-spine  (11)
```

**The loop is the differentiator, not the audit alone.** Naive
single-shot beats a single audit re-pass on a strong model; only
iterative refinement keeps the pipeline's lead.

## What this means for the pitch

The honest framing — already codified in `concept.md` §15 as a
non-negotiable — is:

- **For the strongest models, naive prompting is a strong floor.**
  An Opus-class model handed a tight cluster of complaints plus
  the current surface will produce a coherent redesign in one shot
  that passes a re-audit well.
- **The pipeline's differentiator is not "better severity sum at
  step 1."** It is: (a) every step is traceable to audited
  evidence, (b) every step's severity drop is quantified
  per-heuristic, (c) the iteration log preserves rejected attempts
  for audit, (d) the loop closes the remaining gap and consistently
  beats the naive floor at the **final** parent.
- **On weaker/cheaper models the pipeline's value shows earlier.**
  Sonnet 4.6 benefits from audit-perspective decomposition at
  every step; B1 on Sonnet is substantially worse than the
  pipeline at any stage (19 vs 11 thin-spine vs 6 loop).
- **"Same severity sum" hides distribution differences.** Opus 4.6
  thin-spine and B1 both scored 9, but on different heuristics.
  Audit-trace shows which; a naive pitch showing only sums
  conceals this.

Deliberately not spun — this eval gives the judge ammunition on
both sides, and the writeup keeps that honest. "Beats naive"
belongs in the pitch only as "final-state dominance + traceability
guarantee," not as "we always win from the first call."

## Why B1 can still be rejected — even when scored well

B1 produces a prose `after_snapshot` with **zero audit trail**:

- No record of which user complaints the redesign addresses.
- No named reconciliation of competing perspectives (Norman vs
  WCAG vs Kahneman vs Osterwalder vs Cooper vs Garrett).
- No priority reasoning ("why this cluster over the other nine?").
- No preserved rejections ("what did the loop try and why did the
  verifier refuse?").
- No `informing_review_ids` that link the design back to the
  people whose complaints it answers.

The pipeline produces a matching `after_snapshot` plus every one
of those artifacts. Review the repository's
`data/derived/l8_loop/artifacts/opus47_tchebycheff/cluster_02_iter02.md`
side-by-side with
`data/derived/baseline_b1/baseline_b1_cluster02_opus47.md` — the
severity deltas are comparable; the audit trails are not.

## Costs

- 3 cells × 2 Claude calls (generation + re-audit) = 6 calls.
- Opus calls ≈ $0.15 each, Sonnet ≈ $0.05 each.
- Total: ~$0.50.

## Artifacts

- Script: `scripts/baseline_b1.py`
- Runner: `scripts/run_baseline_b1_matched.sh`
- Outputs per cell:
  - `data/derived/baseline_b1/baseline_b1_cluster02_{modelshort}.jsonl`
  - `data/derived/baseline_b1/baseline_b1_cluster02_{modelshort}.native.jsonl`
  - `data/derived/baseline_b1/baseline_b1_cluster02_{modelshort}.md`
  - `data/derived/baseline_b1/baseline_b1_cluster02_{modelshort}.provenance.json`

## Scope gaps

- **One cluster only.** B1 was not run on cluster_06 or cluster_11
  (clusters with higher internal tension). Those might flip the
  Opus 4.7 result — naive prompting is generally expected to
  struggle more on clusters that combine competing design
  concerns.
- **B2 (manual clustering + single-shot generation) not
  implemented.** B2 was planned as a middle ground between B1 and
  the full pipeline; deferred for time. Its omission means we
  cannot isolate which pipeline stages contribute how much over
  naive — but for the pitch's purpose the B1 vs full-pipeline
  delta is sufficient.
- **Re-audit uses the same model that generated.** Cross-model
  re-audit (generate with Sonnet, re-audit with Opus) would
  decouple "model produces defensible snapshot" from "model
  defends its own snapshot." Not tested; known soft spot.
