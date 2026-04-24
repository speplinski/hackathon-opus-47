# Design brief — cluster_11

**Label:** Streak loss

**Pipeline model:** `opus47` · **loop verifier:** `tchebycheff` · **cluster members:** 27 reviews · **baseline heuristics:** 7

**Generated:** 2026-04-24T18:11:48+00:00

---

## Executive summary

Users of **streak loss** report friction captured as 7 named heuristic violations across six design lenses (Norman / WCAG / Kahneman / Osterwalder / Cooper / Garrett), with reconciled severity sum **55**. The pipeline proposes a direction (L7) that, refined through iterative self-verification (L8 loop), drives the measured severity to **3** (**95% reduction** on the same heuristic list). Real-product verification against product screenshots confirms 6 of 7 heuristics, softens 1 to partial, and refutes 0 as a review-inferred false-positive — a correction the pipeline could not make from review text alone. Self-verified (ensemble-internal); external validation remains the design team's responsibility.

---

## User pain signal

Cluster aggregates **27 reviews** with 5 representative quotes captured by L3b.

**Representative user quotes:**

> losing my streak

> lost all my streak and progress

> lose your streak

> all my streak disappeared

> can't even sustain my streak

**Informing review IDs:**

- `104dc4f56ecd50fa03d91399cc6df590c2506fef`
- `16dad76e9380b4d6510d7b5a04cdd6d40cf86d91`
- `1e09575af1a8f786530871cc6b529810717d1660`
- `1e2f9632dff3f69e23da88829b0e4c04a3ee294e`
- `1e50fddfff28e16e670274258323b56286021c96`
- `3a613baf1dbcf647d0065b263482598361bc3b72`
- `3ad1a1a0d1a6cd8cfc0b2a1490991a6932ffddba`
- `43e5983c550ae48971f35d9f73f0cff9a688766c`
- `5a3e115adfefaec93836be475395c9daf904cb00`
- `5b409f9b2a194e67ba94811c6553469851fccf86`
- `5ce13849bc9e0168fb3b4a5f933a29a152e6b0e0`
- `7da7b6a8a66d6637fd793519612eea2347fb104e`
- `7e1c3243b147310e32b509ea22fde6b702126496`
- `8de753021698834f7e69743b0dabf14e7ead5b33`
- `8f527f6f2ca074cb419c84c5011af80c7a5aa370`
- `96649b6906e19e825f45b92f8a58599038c407c5`
- `a0397f7445fe55231b761e9e2c1c15962ef02d21`
- `a03bf5f7faf1de9dcd34d226f82f7581f0173a09`
- `ab37b937f1f65e32bb991447922cafbd471ce300`
- `b65b2ba944c88c03d00536e1e8c55f4eebdcf87c`
- `c36c39a2336b04105d23eb46e02e2a2c73570110`
- `d5662a68756dfc6cccff15eba35ec7db4db8db1a`
- `dee0976b5d348d628eb9077439e44e90e936f497`
- `f29bfbb8cfd5cab9fd05777a3141ff4c7f45a896`
- `fa2e8f99beccd3ac99837ac532561355eca6ab2e`
- `fc2c2a9edc8a485886d05f318b1b9ac33df5c319`
- `fff13ac43ff64e4cedc7636ecef294cfae542b0d`

---

## Measured pain spaces

L5 reconciliation across six lenses produced the ranked list below. Severities use the ADR-008 anchored scale (`{0, 3, 5, 7, 9}`) designed for cross-run reproducibility rather than calibrated intensity.

Grounded evidence from product screenshots is attached per heuristic (confirmed / partial / refuted).

| Heuristic | L5 sev | Grounded verdict | Adjusted sev | Evidence |
|---|---|---|---|---|
| `deliberate_friction_misapplied__corroborated` | 7 | confirmed | 7 | Screenshot 2 shows the energy modal firing mid-lesson with 'Translate this sentence' and the Sikh character prompt ('Who was going to do that?') still visible above the modal — the user is interrupted mid-task rather than at a natural boundary between lessons. |
| `monetisation_interrupts_value__posture_drift_within_product__skeleton_does_not_honour_priority` | 9 | confirmed | 9 | In screenshot 2, the progress bar at '20/4?' and the live translation task are overridden by a full-surface monetisation modal whose primary CTA is 'TRY 1 WEEK FOR FREE' in bright blue — the lesson posture (transient learning) has been replaced with a sovereign sales posture. |
| `blame_the_user_framing__burdens_with_internal_problems__loss_framed_free_exit` | 7 | confirmed | 7 | Screenshot 2's free exit is labelled 'LOSE XP' in muted grey centred at the bottom, and screenshot 3's exit is 'QUIT LESSON' — both frame the no-pay path as self-inflicted damage, while the headline 'You ran out of energy!' narrates an internal rate-limit as the user's failing. |
| `fudgeability_absent__missing_undo` | 9 | partial | 7 | The modal offers only Super, Recharge (450 gems), or 'LOSE XP'/'QUIT LESSON' — no option to pause, save, or resume the in-progress translation task. No visible undo or state-preservation affordance exists on the modal. |
| `cr_undermined_by_r_dollar__pattern_declared_not_implemented__strategy_contradicts_itself` | 9 | confirmed | 9 | Screenshot 2 shows 27,932 gems held by the user yet the Recharge costs only 450 — the gem economy is dwarfed, and the 'LOSE XP' option explicitly threatens earned progress, weaponising prior free-tier investment to push the Super trial. |
| `competing_calls_to_action__false_signifier` | 7 | confirmed | 7 | The Super card in screenshot 2 has a saturated magenta/cyan/green gradient border, a pre-checked blue tick, and anchors the full-width blue 'TRY 1 WEEK FOR FREE' CTA; the free exit 'LOSE XP' is rendered as small muted blue text with no button affordance, inverting visual priority. |
| `upgrade_path_opaque` | 7 | confirmed | 7 | The primary CTA 'TRY 1 WEEK FOR FREE' in screenshots 2 and 3 shows no adjacent copy disclosing post-trial price, billing cadence, or auto-renewal terms — no fine print is rendered on the modal surface. |

**Baseline severity sum:** 55

---

## Priority reasoning

L6 weights the cluster on five dimensions (anchored 0–10).

| Dimension | Score | Meta-weight |
|---|---|---|
| business_impact | 9 | 0.20 |
| cognitive_cost | 8 | 0.20 |
| persistence | 8 | 0.20 |
| reach | 9 | 0.20 |
| severity | 9 | 0.20 |

**Weighted total:** 8.6 · validation passes: 2 · validation delta: 0.00

---

## Validated direction

L7 proposed a design decision for the highest-priority pain space. L8 loop then refined the decision through iterative self-verification: the loop's final accepted iteration (iter 01) drops the measured severity from **55** to **3**.

### Before (current product state, per L7)

> When energy depletes mid-lesson, a full-viewport modal overrides the lesson surface and forces an immediate choice. The primary CTA 'TRY 1 WEEK FOR FREE' carries dominant green visual weight with no post-trial pricing disclosed. The free exit is rendered as 'LOSE XP' / 'QUIT LESSON' in low-contrast secondary styling — self-blaming, loss-framed copy that narrates an internal rate limit as user failure. In-progress lesson state and streak are discarded on dismiss, with no undo.

### After (validated direction)

> Mid-lesson energy depletion triggers an auto-save: the current question completes, lesson state persists as 'resumable,' and the streak is protected by default. The monetisation surface moves to the lesson-complete screen as a non-blocking inline panel offering three paths — 'Continue with Super' (with full pricing and billing cadence disclosed), 'Watch ads to refill,' and 'Resume tomorrow' — rendered at equal typographic and chromatic weight. Exit copy is neutral and descriptive ('Out of energy — pick up here anytime'); no loss-framing, no blame-the-user phrasing.

### Per-heuristic delta (L5 baseline → loop final)

| Heuristic | Baseline | Final | Δ |
|---|---|---|---|
| `deliberate_friction_misapplied__corroborated` | 7 | 0 | -7 |
| `monetisation_interrupts_value__posture_drift_within_product__skeleton_does_not_honour_priority` | 9 | 0 | -9 |
| `blame_the_user_framing__burdens_with_internal_problems__loss_framed_free_exit` | 7 | 0 | -7 |
| `fudgeability_absent__missing_undo` | 9 | 0 | -9 |
| `cr_undermined_by_r_dollar__pattern_declared_not_implemented__strategy_contradicts_itself` | 9 | 3 | -6 |
| `competing_calls_to_action__false_signifier` | 7 | 0 | -7 |
| `upgrade_path_opaque` | 7 | 0 | -7 |

**Severity reduction:** 52 units (95%).

**Resolves heuristics (per L7 decision):**

- `deliberate_friction_misapplied__corroborated`
- `monetisation_interrupts_value__posture_drift_within_product__skeleton_does_not_honour_priority`
- `blame_the_user_framing__burdens_with_internal_problems__loss_framed_free_exit`
- `fudgeability_absent__missing_undo`
- `competing_calls_to_action__false_signifier`
- `upgrade_path_opaque`

---

## Out-of-baseline observations

Real-product verification can surface defects that the review-inferred heuristic list did not name. These are candidates for inclusion in the next clustering cycle.

> All seven baseline heuristics were confirmed on the product, with only fudgeability_absent downgraded to partial because undo behaviour is inferential rather than directly visible. The mid-lesson screenshot is the strongest evidence: the translation task remains rendered behind a sovereign monetisation modal with asymmetric CTAs ('TRY 1 WEEK FOR FREE' as giant blue button vs 'LOSE XP' as muted text link). One additional defect not named in the baseline: the dedicated energy surface (screenshot 1) duplicates the same Super upsell at a different gem price (500 vs 450 in the modal), which is a pricing-consistency defect worth flagging. Also worth noting: the pre-checked tick on the Super card is a dark-pattern default-selection signal that the baseline does not explicitly name.

---

## Audit trail — iteration log

Every iteration the loop produced is recorded below, including rejected attempts. This is the transparency guarantee: the designer can see not only the final direction but also what the pipeline tried and why each attempt was accepted or rejected.

| Iter | Status | Severity sum | Parent | Notes |
|---|---|---|---|---|
| 00 | ✓ accepted | 55 | `—` | Baseline — heuristic severities imported verbatim from L5 reconciled verdict. No Claude call; no regression possible (it… |
| 01 | ✓ accepted | 3 | `iteration__cluster_11__00` | The after_snapshot structurally resolves the mid-lesson interrupt (friction, posture drift, monetisation-interrupts-valu… |

---

## Signal quality indicators

These are transparent components, not a rollup score. The designer weights them based on context.

- **Severity reduction**: 95% (55 → 3)
- **Loop convergence**: stalled · 2 total iterations · 0 rejected
- **Grounded-evidence ratio**: 6 confirmed / 1 partial / 0 refuted (weighted score: 93%)

---

## Handoff — what the designer owns next

This brief is **direction, not specification**. Translate into wireframes, components, and flows in the tooling your team uses (Claude design, Figma, Linear). The agent does not commit, does not merge, does not ship — the work starts here and is owned by human design/engineering.

**What the brief guarantees:**

- Every finding has a typed chain back to informing user reviews and (where run) real-product screenshots.
- The validated direction self-verifies (ensemble-internal) against the same heuristic baseline — i.e. the re-audit confirms the direction reduces measured pain.
- Every rejected loop attempt is preserved above for audit.

**What the brief does NOT guarantee:**

- Real-user validation (A/B testing, longitudinal study).
- Implementation feasibility in the team's tech stack.
- Aesthetic / brand fit with the product's visual system.
- That the direction is the *only* direction that would work — it is one validated direction, not the space of valid directions.

Designers should feel free to reject the direction on any of the above axes. The pipeline's job is to ensure rejection happens on the basis of visible evidence, not blind trust in either side.

---

## Provenance

| Layer | Input file | sha256 |
|---|---|---|
| L3b cluster | `data/derived/l5_reconcile/shared_l2opus47/l3b_filtered_opus47.jsonl` | `e108546d680fc036` |
| L5 reconciled | `data/derived/l5_reconcile/shared_l2opus47/l5_reconciled_opus47.jsonl` | `76cb7581efa59ce1` |
| L6 priority | `data/derived/l6_weight/shared_l2opus47/l6_priority_opus47.jsonl` | `e271e80f8cd18fcf` |
| L7 decision | `data/derived/l7_decide/shared_l2opus47/l7_design_decisions_opus47.jsonl` | `d13750a19a957491` |
| L8 thin-spine iterations | `data/derived/l8_optimize/shared_l2opus47/l8_optimization_iterations_opus47.jsonl` | `0479b71a0d07a8b4` |
| L8 loop iterations | `data/derived/l8_loop/shared_l2opus47/l8_loop_iterations_cluster11_opus47_tchebycheff.jsonl` | `e3b0c44298fc1c14` |
| verify-on-product | `data/derived/verify_on_product/shared_l2opus47/verify_on_product_cluster11_opus47.json` | `e8d77b72b90d1e1a` |

