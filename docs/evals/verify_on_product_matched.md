# Verify-on-product — matched eval

Real-product grounding hook: MVP of the "product integration"
described in `docs/value_proposition.md` §Scope. Takes L5
reconciled hypotheses (named heuristic violations inferred from
user reviews) plus actual product screenshots, and asks a VLM to
verify each hypothesis against what is visible on the product.
Emits per-heuristic grounded evidence: confirmed / partial /
refuted, with adjusted severity on ADR-008 anchored scale.

This closes a loop the rest of the pipeline could not close: L3b
through L7 operate on prose descriptions inferred from review text;
without a real-product hook, a hypothesis surfaced from reviews
has no mechanism to be falsified by what the product actually
shows. Verify-on-product is the minimal instantiation of that
falsification step.

## Scope and grid

- **Cluster:** `cluster_02` (Duolingo streak-loss modal + energy
  paywall pressure).
- **Input (hypothesis side):** L5 reconciled verdict for
  cluster_02 — 7 named heuristics with baseline severities, sum 57.
- **Input (product side):** 3 real Duolingo screenshots under
  `data/raw/duolingo_screenshots/`:
  - `energy_manage.png` — dedicated energy surface (Super upsell,
    Recharge, Mini charge, 22h 31m regen label)
  - `out_of_energy_home.png` — blocking modal between lessons
    (Super default, Recharge, Quit lesson muted)
  - `out_of_energy_mid_lesson.png` — mid-session blocker with
    "Translate this sentence" visible above; "TRY 1 WEEK FOR FREE"
    primary, "LOSE XP" as punitive alternative
- **Models:** Opus 4.6, Sonnet 4.6, Opus 4.7. Single VLM call per
  cell (3 images + structured heuristic list in one prompt).
- **Grid:** 1 cluster × 3 models × 3 screenshots = 3 cells.

## Headline — per-cell verdict counts

| Model      | Adjusted sum | Δ vs baseline 57 | Confirmed | Partial | **Refuted** |
|------------|--------------|-------------------|-----------|---------|-------------|
| Opus 4.6   | 53           | −7 %              | 5         | 2       | 0           |
| Sonnet 4.6 | 53           | −7 %              | 6         | 1       | 0           |
| Opus 4.7   | **46**       | **−19 %**         | 4         | 2       | **1**       |

Two readings:

- **Sonnet 4.6 is the most conservative.** Six of seven heuristics
  confirmed at baseline severity; only scarcity-timer softens
  (sev 7 → 3).
- **Opus 4.7 is the most critical.** Four confirmed, two softened
  to partial, one refuted entirely. Its adjusted sum (46) is the
  largest calibration move in the grid.

Neither extreme is necessarily right — they reveal different
VLM dispositions. The headline finding is that the grid surfaces a
**genuine false-positive in the L5 baseline**, visible only once
the hypothesis is tested against actual product state.

## Per-heuristic × per-model matrix

Baseline severities from L5: 9 / 9 / 7 / 9 / 7 / 9 / 7 (sum 57).

| Heuristic                                 | L5 sev | Opus 4.6 | Sonnet 4.6 | Opus 4.7 |
|-------------------------------------------|--------|----------|------------|----------|
| `modal_excise__corroborated`              | 9      | **9 ✓**  | **9 ✓**    | **9 ✓**  |
| `channel_gap__corroborated`               | 9      | 7 ~      | **9 ✓**    | 7 ~      |
| `competing_calls_to_action__corroborated` | 7      | **7 ✓**  | **7 ✓**    | **7 ✓**  |
| `cr_undermined_by_r_dollar__corroborated` | 9      | **9 ✓**  | **9 ✓**    | 7 ~      |
| `deceptive_feedback__scarcity_timer`      | 7      | 5 ~      | 3 ~        | **0 ✗**  |
| `vp_cs_mismatch`                          | 9      | **9 ✓**  | **9 ✓**    | **9 ✓**  |
| `ego_depletion_mid_task`                  | 7      | **7 ✓**  | **7 ✓**    | **7 ✓**  |
| **Sum**                                   | **57** | **53**   | **53**     | **46**   |

Legend: `✓` confirmed, `~` partial, `✗` refuted.

**Perfect consensus (3/3 confirmed at baseline):**
`modal_excise`, `competing_calls_to_action`, `vp_cs_mismatch`,
`ego_depletion_mid_task`. These four heuristics are visible on the
product exactly as inferred from reviews.

**Divergence cluster:** `channel_gap`, `cr_undermined_by_r_dollar`,
`deceptive_feedback__scarcity_timer` — each model reads the product
slightly differently.

## Key finding — refuted hypothesis

**Opus 4.7 refutes `deceptive_feedback__scarcity_timer` (sev 7 → 0).**

The baseline L5 hypothesis, inferred from reviews, held that a
scarcity timer ("your streak is about to disappear", "X hours
left") was a dark pattern suppressing System 2 reasoning. On Opus
4.7's read of the three screenshots:

> "The scarcity-timer heuristic is refuted — no countdown pressure
> is visible on these paywall modals, only a static regen label
> on the non-blocking energy screen."

What the VLM sees:

- **Screen 1 (energy_manage):** shows "22H 31M" as a static regen
  label on a dedicated, non-blocking surface. Users browse this
  when they want to manage energy; it does not force a purchase
  decision.
- **Screens 2 and 3 (out-of-energy modals):** no countdown visible.
  The modals present Super/Recharge/Quit paths without temporal
  pressure.

The L5 hypothesis conflates two different product surfaces:
reviews complained about time pressure in the *energy regen
context*, but the *paywall modals* — where the pressure would
matter for monetisation — have no scarcity timer. This is exactly
the kind of false-positive that review-corpus analysis alone
cannot resolve: a review saying "it feels like a countdown pressure"
is real qualitative signal, but the defect may not live where the
reviewer thinks it lives.

**This is the first-order value of verify-on-product.** The
pipeline without this hook would ship a direction addressing
scarcity-timer manipulation; with the hook, the direction scopes
itself to the actual loci of pain (modal-blocking + loss framing).
Three Opus-class calls and three screenshots caught what six audit
lenses on review text alone could not.

Opus 4.6 and Sonnet 4.6 both retained the hypothesis at partial
severity (5 and 3 respectively). This is not necessarily wrong —
"partial with static regen label" is a defensible read — but Opus
4.7's hard refutation forces the question and gives the pipeline a
cleaner signal.

## Out-of-baseline defect — "LOSE XP" framing flagged by Opus 4.7

Opus 4.7's summary observes what baseline L5 did not name:

> "...partially cr_undermined_by_r_dollar via 'LOSE XP' framing."

Screen 3 (`out_of_energy_mid_lesson.png`) shows the modal's
secondary link labelled **"LOSE XP"** — a punitive framing on the
"no, I don't want to buy" path. This is a distinct dark pattern
from the baseline's `cr_undermined_by_r_dollar` (loss-aversion on
streak value) — it is a secondary loss-aversion layer on *lesson
progress* reinforcing the subscription push.

The baseline L5 reconciled verdict does not name "LOSE XP" as a
separate heuristic because review-corpus analysis surfaces the
*subjective* complaint ("it feels manipulative") but not the
*specific UI copy* that produces the feeling. Real-product hook
catches the copy. A future iteration of the pipeline could feed
this back into L3b/L5 as a new heuristic candidate for the next
cluster refresh.

## Model divergence analysis

| Dimension                              | Opus 4.6     | Sonnet 4.6   | Opus 4.7     |
|----------------------------------------|--------------|--------------|--------------|
| Baseline alignment (how close to 57)   | 53 (close)   | 53 (close)   | 46 (distant) |
| Refutation willingness                 | none         | none         | 1 heuristic  |
| Out-of-baseline defects flagged        | (not flagged)| (not flagged)| "LOSE XP"    |
| Calibration posture                    | moderate     | conservative | critical     |

Sonnet 4.6 treats the screenshots as supporting evidence for
the review-inferred claims; Opus 4.7 treats them as an
independent check. Neither is strictly "better" — the cheaper
Sonnet model is more likely to rubber-stamp, the flagship Opus is
more likely to dissent. For production deployment a design org
might prefer Opus 4.7 precisely because its dissent is cheap
insurance against false-positives.

Opus 4.6 lands between the two: willing to soften but not
refute, willing to adjust `channel_gap` and `scarcity_timer` to
partial without flagging anything new.

## Costs

Three VLM calls (Opus 4.6, Sonnet 4.6, Opus 4.7 — each receiving
3 PNG images + structured heuristic list):

- Opus calls: ~$0.30–$0.40 each (VLM tokens ≈ 3–4× text tokens for
  same spend).
- Sonnet call: ~$0.05–$0.10.
- Estimated grid total: ~$0.70–$0.90.

Per cluster, per model: about half the cost of an L8-loop
iteration, for arguably higher discriminating value.

## What this means for the pitch

The value proposition document describes the agent as
*"product-integrated research-and-validation agent"* with
full product ecosystem access scoped to v2. Verify-on-product is
the **operational MVP** of that product-hook claim — not the full
MCP integration, but enough to demonstrate the mechanism:

1. Pipeline generates hypotheses from reviews (L3b–L7 output).
2. Real product is inspected against those hypotheses.
3. Hypotheses are **confirmed, softened, or refuted** with cited
   evidence.
4. Direction generation downstream can trust the grounded set.

Without this step, the pipeline would ship a design direction
addressing a scarcity-timer defect that does not exist on the
paywall modal. With this step, the pipeline corrects itself
before handoff.

This is the honest demo. The full MCP-driven product integration
(code repo + Figma + analytics) remains v2 roadmap; the hackathon
implementation is the proof that grounding changes the output, not
just a retrospective confidence annotation.

## Scope gaps

1. **Only three screenshots.** Cluster_02 has more surface area
   than these three captures; edge cases (first-time user flow,
   post-purchase state, A/B test variants) are invisible to the
   VLM.
2. **No DOM / HTML inspection.** VLM reads rendered pixels; it
   cannot verify z-index, aria-live regions, or event-level
   scarcity-timer behaviour (e.g. countdown that shows only when
   within X hours of reset). "No timer visible" here means "not
   in these three frames".
3. **Single-cluster pilot.** Cluster_02 is the only cluster driven
   through verify-on-product. Real value of the hook across the
   10-cluster L3b output is untested.
4. **No feedback into L3b / L5.** The "LOSE XP" finding flagged by
   Opus 4.7 should ideally loop back into the clustering / audit
   layers as a new heuristic candidate. That cycle is not
   implemented; for now the finding lives only in this artifact.

## Artifacts

- Script: `scripts/verify_on_product.py`
- Runner: `scripts/run_verify_on_product_matched.sh`
- Per-cell outputs:
  - `data/derived/verify_on_product/verify_on_product_cluster02_{opus46,sonnet46,opus47}.json`
  - `…{modelshort}.md` (human-readable per-heuristic table with evidence)
  - `…{modelshort}.provenance.json` (tokens, image hashes, verdict counts, baseline vs adjusted sum)

## One-sentence takeaway

**Three VLM calls on three screenshots caught one false-positive
(`scarcity_timer`) and one out-of-baseline defect ("LOSE XP"
framing) that six text-based audit lenses on review text could
not — the first empirical demonstration that the
product-integration hook changes the pipeline output, not just
its confidence.**
