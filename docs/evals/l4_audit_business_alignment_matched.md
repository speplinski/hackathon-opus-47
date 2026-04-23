# L4 audit — `audit-business-alignment` skill, 3-model × 2-modality matched comparison

**Date:** 2026-04-23
**Related:** ADR-008 (audit severity anchors), ADR-011 (replay log contract), `ARCHITECTURE.md` §4.5 (L4 layer), `docs/evals/l4_audit_decision_psychology_matched.md` (sister Kahneman-skill eval), `docs/evals/l4_audit_accessibility_matched.md` (sister WCAG-skill eval), `docs/evals/l4_audit_usability_fundamentals_three_way.md` (sister Norman-skill smoke), `skills/audit-business-alignment/SKILL.md`, `src/auditable_design/layers/l4_audit_business_alignment.py`, `scripts/smoke_l4_business_alignment_multimodal.py`, `scripts/run_l4_business_alignment_matched.sh`
**Status:** Empirical record. Thin-spine smoke on one cluster (`cluster_02 "Streak loss framing pressures users into mid-session purchase"`) across six cells — {Opus 4.6, Sonnet 4.6, Opus 4.7} × {text-only, multimodal}. Purpose is to characterise the Osterwalder skill's cross-model and cross-modality behaviour on an adversarial freemium-stack stimulus before a full-corpus run.

## Purpose

L4's `audit-business-alignment` skill replaces Norman's cognitive lens and Kahneman's decision-psychology lens with Osterwalder's Business Model Canvas: four dimensions (`value_delivery`, `revenue_relationships`, `infrastructure_fit`, `pattern_coherence`), per-finding `building_blocks` (closed set of 9 Canvas codes — `cs`, `vp`, `ch`, `cr`, `r_dollar`, `kr`, `ka`, `kp`, `c_dollar`), optional `tension` (lex-ordered pair of blocks flagging a cross-block conflict), and `pattern` (closed set of 7 business-model patterns — `multi_sided`, `freemium`, `long_tail`, `subscription`, `unbundled`, `open`, `none_identified`). Discipline rule: any non-empty `tension` at severity ≥ 3 forces the enclosing dimension score to ≤ 2 — a structural cross-block conflict is a failure of business-model design, not a local fix, mirroring the Kahneman dark-pattern cap.

The matched eval therefore has to answer three questions the sister evals did not:

- **Do the three models converge on *which* Canvas blocks are in tension, or does each family draw the block boundaries differently on the same stimulus?** The Duolingo streak-loss modal is a classic VP↔R\$ stack (value proposition is interrupted to trigger revenue) — but it can also be read as CR↔R\$ (the streak *is* the customer-relationship mechanic) or CS↔VP (the "free language learning" marketed to the segment diverges from the mid-lesson paywall reality). All three tensions are defensible; which ones each cell surfaces is a genuine taxonomy question.
- **Does attaching a PNG change which *pattern* label gets assigned?** The quotes and `ui_context` describe a freemium pattern; the HTML/PNG reveals a specific *subscription*-grade implementation (price anchor `$6.99/mo → $3.49`, countdown, full-bleed CTA). Whether models project onto `freemium` or disambiguate into `subscription` is modality-sensitive.
- **Does the tension-cap rule hold empirically?** The module enforces it in the parser; this eval verifies the models produce outputs that land inside the cap rather than trigger a fallback on the discipline rule.

## Executive summary

| | Opus 4.6 text | Opus 4.6 image | Sonnet 4.6 text | Sonnet 4.6 image | Opus 4.7 text | Opus 4.7 image |
|---|---|---|---|---|---|---|
| Clusters audited | 1 | 1 | 1 | 1 | 1 | 1 |
| Fallback count | 0 | 0 | 0 | 0 | 0 | 0 |
| Findings emitted | 7 | 7 | 5 | 5 | 6 | 6 |
| Tension findings | 4 | 4 | 4 | 4 | 5 | 4 |
| Single-block findings | 3 | 3 | 1 | 1 | 1 | 2 |
| Nielsen-4 findings | 3 | 2 | 3 | 2 | 2 | 2 |
| Nielsen-3 findings | 3 | 4 | 2 | 3 | 2 | 2 |
| Nielsen-2 findings | 1 | 1 | 0 | 0 | 2 | 2 |
| `value_delivery` score | **1** | **1** | **1** | 2 | 2 | 2 |
| `revenue_relationships` score | **1** | **1** | **1** | **1** | **1** | **1** |
| `infrastructure_fit` score | 3 | 3 | **4** | 3 | **4** | 3 |
| `pattern_coherence` score | 2 | 2 | 2 | 2 | 2 | 2 |
| `freemium` findings | 7 | 7 | 5 | 5 | 4 | 4 |
| `subscription` findings | 0 | 0 | 0 | 0 | **2** | **2** |
| Input tokens | 9127 | 10475 | 9127 | 10475 | 12247 | 13595 |
| Output tokens | 1852 | 1666 | 1371 | 1393 | 2111 | 2089 |

Zero fallback and zero transport failure across all six live calls. All six verdicts share the same `verdict_id` (`audit-business-alignment__cluster_02`), the same `skill_hash` (`047320d2…`), and the same input sha256 (`dc6d981f…`); they disagree only on finding content.

Four load-bearing observations:

1. **All six cells converge on `revenue_relationships = 1`** — the worst possible score in the rubric, driven by the tension-cap rule (any sev ≥ 3 finding with non-empty tension → dimension capped at 2) compounded by two sev-4 CR↔R\$ findings per cell (`monetisation_interrupts_value` + `cr_undermined_by_r_dollar`) that stack on this dimension. The three model families independently agree this is a revenue-relationship catastrophe; the reading is robust to modality.

2. **The CR↔R\$ and VP↔R\$ tensions are load-bearing across all 6 cells; the CS↔VP tension is a close third.** Every cell surfaces `cr↔r_dollar` at least once (5/6 cells surface it twice), every cell surfaces `r_dollar↔vp`, and 6/6 cells surface `cs↔vp` (though Sonnet 4.6 image and Opus 4.6 image label it `onboarding_vp_drift` rather than `vp_cs_mismatch`). These three tensions are the canonical structural reading of a freemium product that interrupts its own value proposition for revenue — and the skill extracts them reliably regardless of family or modality.

3. **Opus 4.7 uniquely disambiguates `freemium` and `subscription`; the other four cells project onto `freemium` monoculture.** Opus 4.6 (both modalities) and Sonnet 4.6 (both modalities) tag every single finding with `pattern=freemium` — 7/7 and 5/5 respectively. Opus 4.7 splits the same findings: the two CR↔R\$ sev-4 findings read as `subscription` pattern (the specific monetisation mechanic is a monthly recurring plan with anchor pricing), while `monetisation_interrupts_value` and `pattern_declared_not_implemented` retain `freemium`. This is the matrix's single most model-distinguishing behaviour — and the right answer is probably "both": freemium is the stated positioning, subscription is the actual unit economics.

4. **Sonnet 4.6 image uplifts `value_delivery` from 1 → 2, but the HTML/PNG evidence should push the other direction.** Sonnet 4.6 text and all Opus 4.6 cells score `value_delivery = 1` (driven by a sev-4 `vp_cs_mismatch` or equivalent stacking with another sev-3 tension). Sonnet 4.6 image drops the sev-4 tension to sev-3 (`onboarding_vp_drift` at sev-3), removing the floor trigger and letting the dimension settle at 2. Counter-intuitive: the visual evidence should *strengthen* the VP↔CS case (the bright-green "Keep my streak" CTA is an explicitly-designed moment of the product that directly contradicts the marketed VP), but Sonnet 4.6 image reads it as a lower-severity onboarding drift. This is the eval's single modality-severity inversion.

## Methodology

### Input

One enriched cluster from the L3b matched-corpus output, reused verbatim from the Kahneman eval so the two sister audits see byte-identical input:

| | sha256 |
|---|---|
| `data/derived/l4_audit/audit_business_alignment/audit_business_alignment_input.jsonl` | `dc6d981f1652884e0088d9299311230d183f9d7cb71c78d4729b1eec5068b961` |

Cluster shape: `cluster_02`, label `"Streak loss framing pressures users into mid-session purchase"`. Five representative quotes drawn from the cluster's seven member reviews (same as Kahneman eval):

- `q[0]`: "If you don't agree to pay mid-lesson, and you haven't watched ads FIRST, you have to quit mid-lesson"
- `q[1]`: "I'm trying to keep my 800+ day streak, but the recent changes are abysmal"
- `q[2]`: "the new update implemented an energy system instead of the hearts, which ruined my experience by forcing me to pay or watch ads"
- `q[3]`: "I was in holiday so i logged out but when i came home then i logged in but still my streak was fall into 0 days"
- `q[4]`: "I would have to do the same lesson multiple times just to keep my daily streak"

Attached artefacts (shared with Kahneman eval — same HTML/PNG/`ui_context`):

- **HTML** (`data/artifacts/ui/duolingo_streak_modal.html`, sha256 `cdfcbd47…`, 5677 bytes): "STREAK AT RISK" modal with pulsing countdown, inline-SVG flame, loss-framing banner, anchored price row (`$6.99/mo` struck-through → `$3.49`), full-width `Keep my streak` CTA, secondary ads link, de-emphasised `lose streak` dismiss. Same markup the Kahneman eval uses.
- **Screenshot** (`data/artifacts/ui/duolingo_streak_modal.png`, sha256 `bcad10de…`, 119630 bytes PNG): element-screenshot of the `.phone` container rendered via playwright headless chromium at `device_scale_factor=2`, 428×900 viewport.
- **`ui_context`** (prose): "Duolingo mobile app mid-lesson. The user has just depleted their last unit of energy…"

Stimulus note: same asset, different lens. The Kahneman eval asks "what cognitive mechanisms does this stack?"; this eval asks "what business-model blocks does this connect, and do they hold together?"

### Skill

`skills/audit-business-alignment/SKILL.md` (file sha256 `37957d7893a1977c5d0585608ce5fa9812e90cd7b5970f45183732f328a2aab5`), Osterwalder Business Model Canvas audit with four dimensions + per-finding `building_blocks` / `tension` / `pattern`. Severity anchored per ADR-008 (Nielsen 1–4 → `HeuristicViolation.severity` 3/5/7/9). Output contract enforces:

- Quotes are *not* required on every finding (differs from Kahneman which requires them; aligns with the Accessibility skill's permissive stance — a pricing-page defect or structural KP observation from markup alone is legal). The parser enforces the bidirectional rule instead: if `"quotes"` appears in `evidence_source`, `evidence_quote_idxs` must be non-empty; if `evidence_quote_idxs` is non-empty, `"quotes"` must appear in `evidence_source`.
- `building_blocks` is a non-empty subset of `VALID_BLOCKS` (`{cs, vp, ch, cr, r_dollar, kr, ka, kp, c_dollar}`) with no duplicates.
- `tension` is either `[]` (single-block finding) or a two-element lex-ordered list of *distinct* codes, both present in `building_blocks`.
- `pattern` is one of `{multi_sided, freemium, long_tail, subscription, unbundled, open, none_identified}`.
- Cross-finding dimension cap: any non-empty-tension sev ≥ 3 finding → dimension score ≤ 2.
- No duplicate `(heuristic, tension)` pairs within one audit.

Skill hash: `047320d20d5542ecbf33e4de3acb3c04193ed8b3a8c3648863058d56c199a1ef` (prefix `047320d20d5542ec…` as reported by every smoke log line).

### Runs

All six runs via `scripts/smoke_l4_business_alignment_multimodal.py`, orchestrated by `scripts/run_l4_business_alignment_matched.sh`. Temperature pinned to 0.0 on Opus 4.6 and Sonnet 4.6; stripped on Opus 4.7 via `claude_client._omits_sampling_params` (Opus 4.7 rejects `temperature` with 400). `max_tokens=6144`. `screenshot_media_type="image/png"` on multimodal cells.

| cell | verdicts sha256 | native sha256 |
|---|---|---|
| opus46 × text | `4b46be8f979a883a…` | `9016eefa53750f6c…` |
| opus46 × image | `e30cbb9f5940e826…` | `d3042be619697549…` |
| sonnet46 × text | `17e30dab9f7fce8d…` | `dd3b378d9cc9470e…` |
| sonnet46 × image | `7bb60cc6c98efc72…` | `bd4000f17afff6b8…` |
| opus47 × text | `3dc39075e4666c6b…` | `48c6dbd7bec3aa85…` |
| opus47 × image | `89026f96c4dd3616…` | `9ddaaaa9dd286e43…` |

Outputs at `data/derived/l4_audit/audit_business_alignment/l4_verdicts_audit_business_alignment_cluster02_{opus46,opus46_multimodal,sonnet46,sonnet46_multimodal,opus47,opus47_multimodal}.{jsonl,native.jsonl,provenance.json}`.

## Results

### Heuristic inventory across all six cells

Rows are the heuristics each cell named (`finding.heuristic` slot); columns are cells. "✓/N" = present at Nielsen severity N (max across duplicates within the cell); "—" = absent.

| heuristic | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| `monetisation_interrupts_value` | ✓/4 | ✓/4 | ✓/4 | ✓/4 | ✓/4 | ✓/4 |
| `cr_undermined_by_r_dollar` | ✓/4 | ✓/3 | ✓/4 | ✓/4 | ✓/4 | ✓/4 |
| `pattern_declared_not_implemented` | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 |
| `upgrade_path_opaque` | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/2 | ✓/2 |
| `vp_cs_mismatch` | ✓/4 | ✓/4 | ✓/4 | — | ✓/3 | ✓/3 |
| `onboarding_vp_drift` | — | ✓/3 | — | ✓/3 | — | — |
| `channel_gap` | ✓/3 | — | — | — | ✓/2 | ✓/2 |
| `cost_structure_leaks_to_ux` | ✓/2 | — | — | — | — | — |
| `kr_insufficient` | — | ✓/2 | — | — | — | — |

Core convergence across all 6 cells: `monetisation_interrupts_value` (sev-4 always), `cr_undermined_by_r_dollar` (sev-3 to sev-4), `pattern_declared_not_implemented` (sev-3 always, always on `r_dollar↔vp` tension, always `pattern=freemium`), and `upgrade_path_opaque` (sev-2 to sev-3, always single-block on R\$). These four heuristics are the load-bearing Osterwalder signal every cell extracts.

The CS↔VP tension manifests 6/6 times but splits its label across two heuristics: `vp_cs_mismatch` (4/6 cells) and `onboarding_vp_drift` (2/6 cells, always image — both Opus 4.6 image and Sonnet 4.6 image). These are semantically the same reading at two abstraction levels (one names the broader Canvas tension, the other names a specific UX manifestation of it) — but the skill does not force models to pick one, so cross-cell tallying needs to recognise them as aliases.

Opus 4.6 is the only family that reaches for "peripheral" Canvas heuristics: `cost_structure_leaks_to_ux` (text only, C\$↔VP observation about energy-system operational cost signal), `kr_insufficient` (image only, KR↔CR observation about streak-freeze capacity). Both land at sev-2, both single-block-or-single-tension. Sonnet is the most parsimonious (5 findings, all of which appear in every cell); Opus 4.7 sits in between (6 findings, adding `channel_gap` at sev-2 — a dimension `value_delivery` finding about the after-sales support gap for streak disputes).

### Tension-pair inventory

| tension | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| `cr↔r_dollar` | 1 | 2 | 2 | 2 | 2 | 2 |
| `cs↔vp` | 1 | 1 | 1 | 1 | 1 | 1 |
| `r_dollar↔vp` | 2 | 1 | 1 | 1 | 1 | 1 |
| `ch↔cr` | 0 | 0 | 0 | 0 | 1 | 0 |

Four load-bearing observations:

- **Every cell surfaces the canonical freemium trifecta** — `cr↔r_dollar`, `cs↔vp`, `r_dollar↔vp`. Same three tensions, same directional reading, across all six cells. The skill's tension grammar is extracting the structural signal the input carries regardless of family or modality.
- **Opus 4.6 text is the only cell that double-counts `r_dollar↔vp`** — assigns it to both `monetisation_interrupts_value` and `pattern_declared_not_implemented`. Every other cell assigns `monetisation_interrupts_value` to `cr↔r_dollar` instead, unlocking one finding slot for the `r_dollar↔vp` pair to appear only on `pattern_declared_not_implemented`. Trade-off: one reading emphasises the value interruption (VP↔R\$); the other emphasises the relationship capture (CR↔R\$). Both are defensible; the cells converge on the latter 5/6 times.
- **`ch↔cr` is a one-cell outlier** — Opus 4.7 text is the only cell to label `channel_gap` as a CH↔CR tension. Opus 4.6 text files it at sev-3 but as single-block (`ch+cs`); Opus 4.7 image reads it single-block too (`ch+cr`). Evidence is thin for any of these (the modal has no disputes/support channel surfaced, which is an absence-of-affordance observation); treating it as a cross-block tension vs. a single-block gap is a judgement call the parser permits either way.
- **No `ka↔kp`, no `c_dollar↔kr`, no `kp↔r_dollar` tensions** — every surfaced tension touches at least one of `{cs, vp, cr, r_dollar, ch}`. The Efficiency-side blocks (KR/KA/KP/C\$) are underrepresented, reflecting both the stimulus shape (the modal is a value-side interaction, not a supply-side artefact) and a blind spot worth scrutinising at full-corpus scale: does the skill under-surface Efficiency-side readings, or does the review corpus simply not surface them?

### Per-cell summary prose (native payload, verbatim first sentence)

**Opus 4.6 text.** "The product implements a freemium model whose mid-lesson monetisation modal structurally contradicts the 'free, fun, effective' Value Proposition by blocking core value delivery at the moment of highest user engagement, trading short-term Revenue Stream activation for Customer Relationship damage, streak-loss resentment, and brand-integrity erosion — a textbook case of a freemium tier that has collapsed the protective boundary between free and paid experience."

**Opus 4.6 image.** "The product operates a freemium pattern but implements it as forced-continuity: a blocking modal mid-lesson leverages an artificial loss (streak reset) to convert free users into subscribers, producing a structural VP↔R\$ tension where the Value Proposition ('free, fun, effective language learning') is delivered only up to an energy cap, then held hostage to Revenue Stream conversion at the moment of peak engagement."

**Sonnet 4.6 text.** "The product operates a freemium pattern but the in-product implementation converts the streak mechanic — a core Customer Relationship asset — into a mid-session monetisation trigger, producing a structural CR↔R\$ conflict and a VP-delivery interruption that the marketed 'free' tier is supposed to protect; the Revenue Stream is extracted by withholding the Value Proposition rather than by offering enhancement, which is the configuration a freemium model is specifically designed to avoid."

**Sonnet 4.6 image.** "The product operates a freemium pattern but the streak-loss modal converts the core gamification mechanic into a coercive monetisation trigger, producing a critical VP↔R\$ tension: the declared 'free, fun, effective' Value Proposition is delivered only up to an energy cap, after which the Customer Relationship (the streak) is held hostage to Revenue Stream activation — a structural freemium failure where monetisation interrupts value delivery rather than enhancing it."

**Opus 4.7 text.** "The streak-loss modal structurally couples the Customer Relationship (streak as ongoing commitment device) to the Revenue Stream (mid-lesson paywall with countdown), producing a severe CR↔R\$ tension that contradicts the declared freemium Value Proposition: the product extracts revenue by withholding core value delivery from an engaged user, weaponising the relationship artefact it built to retain them."

**Opus 4.7 image.** "A streak-loss modal converts a learning-engagement mechanic into a paywall trigger, creating a structural VP↔R\$ tension: the 'free, fun, effective' Value Proposition is delivered only until the user is most committed to finishing, at which point the Customer Relationship asset (the multi-day streak) is held against the user to activate the Revenue Stream — a configuration that trades long-term CR equity for short-term conversion."

All six summaries converge on the same two-sentence structural reading: freemium positioning, VP↔R\$ or CR↔R\$ tension, value delivery interrupted at peak engagement to trigger revenue. The vocabulary diverges ("forced-continuity", "monetisation trigger", "commitment device", "held hostage", "weaponising the relationship artefact") but the Canvas-level reading does not.

### Dimension-score divergence

| dim | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img | range |
|---|---|---|---|---|---|---|---|
| value_delivery | **1** | **1** | **1** | 2 | 2 | 2 | 1 |
| revenue_relationships | **1** | **1** | **1** | **1** | **1** | **1** | 0 |
| infrastructure_fit | 3 | 3 | **4** | 3 | **4** | 3 | 1 |
| pattern_coherence | 2 | 2 | 2 | 2 | 2 | 2 | 0 |

Four observations:

- **`revenue_relationships = 1` is unanimous across all six cells.** Every cell drives this dimension to the rubric floor. The effect is mechanical — two sev-4 tension findings (`monetisation_interrupts_value` + `cr_undermined_by_r_dollar`) stacking on this dimension trips the tension cap twice, and with two sev-3+ `cr↔r_dollar` findings the rubric narrows further to 1. The three families agree the revenue-relationship architecture is broken at the bottom of the scale.
- **`value_delivery` splits 3-3 between 1 and 2, with a family-modality interaction.** Three cells score 1 (Opus 4.6 both modalities, Sonnet 4.6 text); three cells score 2 (Sonnet 4.6 image, both Opus 4.7). The 1s all have `vp_cs_mismatch` at sev-4 (the sev-4 tension caps dim at 2, then an additional sev-3 finding in the same dim narrows to 1). The 2s either downrate the VP-CS reading to sev-3 (`vp_cs_mismatch` → 3 in Opus 4.7; `onboarding_vp_drift` → 3 in Sonnet 4.6 image) or swap it entirely (Sonnet 4.6 image drops `vp_cs_mismatch` for the softer `onboarding_vp_drift`). The cross-family severity-calibration disagreement parallels the Sonnet-reads-harsher pattern seen on the Kahneman `cognitive_load_ease` dimension and the Accessibility `4.1.3` reading — except this time it's Opus 4.6 that reads harshest, not Sonnet.
- **`pattern_coherence = 2` is unanimous.** Every cell files `pattern_declared_not_implemented` at sev-3 with tension `r_dollar↔vp`; the tension cap pins the dim at exactly 2; no cell stacks a second finding on this dim. Structurally the cap rule is producing a tight, predictable score here.
- **`infrastructure_fit` ranges 3 to 4.** Four cells score 3 (Opus 4.6 both, Sonnet 4.6 image, Opus 4.7 image); two cells score 4 (Sonnet 4.6 text, Opus 4.7 text). The difference is whether the cell surfaces any Efficiency-side observation at all: Opus 4.6 text adds `cost_structure_leaks_to_ux`, Opus 4.6 image adds `kr_insufficient`, Sonnet 4.6 image spreads its findings such that `infrastructure_fit` has no contribution — and a dim with zero findings defaults to the rubric's neutral-upper score. No cell reaches sev-3 on an infrastructure finding. Interpretation: infrastructure fit is a genuinely less-violated dimension on *this* stimulus (the modal is a value/revenue interaction), and the models reflect that.

### Modality effect, per-model

**Opus 4.6 (7 → 7).** Same cardinality, mid-composition swap. Image drops `channel_gap` and `cost_structure_leaks_to_ux` (two text-only findings reading operational/support gaps from prose only); adds `onboarding_vp_drift` and `kr_insufficient` (two image-grounded findings reading the full-bleed visual weight of the "Keep my streak" CTA as an onboarding-narrative contradiction, and inferring KR under-provision from the explicit "unavailable at your level" footer). Image *downrates* `cr_undermined_by_r_dollar` from sev-4 → sev-3 — the text-only version reads the relationship-capture as catastrophically severe; the image reading sees the same mechanism but softens it by one severity notch. Net: modality shuffles the mechanism mix without changing the two top-level dim scores or the overall severity count.

**Sonnet 4.6 (5 → 5).** Same cardinality, minimal swap: image drops `vp_cs_mismatch` (sev-4, text) and adds `onboarding_vp_drift` (sev-3, image). Same underlying CS↔VP tension reading; severity drops by one notch on the image cell. This is the matrix's one *dimension-score-flipping* modality change — the severity drop pushes `value_delivery` from 1 → 2. Counter-intuitive, as noted in the executive summary: the rendered modal should *strengthen* the VP-CS case (the bright-green full-bleed CTA *is* the moment where the marketed "free" VP contradicts itself), but Sonnet 4.6 reads the image as evidence for a softer onboarding-drift reading rather than the harder VP-CS mismatch. Worth revisiting: does Sonnet systematically downrate severity when it sees visual evidence, or is this a one-cell artefact?

**Opus 4.7 (6 → 6).** Same cardinality, minimal change. Image removes the `ch↔cr` tension tag on `channel_gap` (text cell labels it a tension; image cell labels it single-block) and tightens one finding's building_blocks (image drops `cr` from `pattern_declared_not_implemented`'s block list). Severity and dim-scores are identical across modalities. Opus 4.7 is the most modality-invariant of the three families on this stimulus.

### Pattern-label divergence

| pattern | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| `freemium` | 7 | 7 | 5 | 5 | 4 | 4 |
| `subscription` | 0 | 0 | 0 | 0 | **2** | **2** |
| others | 0 | 0 | 0 | 0 | 0 | 0 |

The matrix-level fact is stark: **4 cells emit `freemium` monoculture; 2 cells (both Opus 4.7) disambiguate into `freemium` + `subscription`.** The two Opus 4.7 findings tagged `subscription` are `cr_undermined_by_r_dollar` (reading the `$6.99/mo` + countdown as a subscription-sale moment) and `channel_gap` (reading the missing support channel as specific to a subscription relationship, not a freemium one).

Interpretation: the Duolingo stimulus is positioned as freemium but implemented with subscription-pattern mechanics (recurring monthly billing, price anchoring, CTA designed for subscription conversion). Opus 4.7 reads both layers and labels them distinctly; the other families project the implementation onto the positioning label. The `pattern` slot is doing work in Opus 4.7 that it is not doing in the other cells — this is one of the matrix's sharpest family-level behavioural signals.

The unused patterns (`multi_sided`, `long_tail`, `unbundled`, `open`, `none_identified`) are all zero. On an adversarial freemium stimulus this is correct but uninformative — worth revisiting on the full-corpus run whether the pattern slot ever lights up outside the freemium/subscription pair.

### Convergence pattern

Heuristics surfaced by all 6 cells — load-bearing convergence:

- `monetisation_interrupts_value` (sev-4 everywhere, always on CR↔R\$ or VP↔R\$ tension)
- `cr_undermined_by_r_dollar` (sev-3 to sev-4 everywhere, always on CR↔R\$ tension)
- `pattern_declared_not_implemented` (sev-3 everywhere, always on R\$↔VP tension, always `pattern=freemium`)
- `upgrade_path_opaque` (sev-2 to sev-3 everywhere, always single-block on R\$)

Heuristics surfaced by 5 of 6 cells:

- `vp_cs_mismatch` — all except Sonnet 4.6 image (which substitutes `onboarding_vp_drift` for the same CS↔VP tension)

Heuristics surfaced by 2-3 cells:

- `channel_gap` — opus46.txt + op47.txt + op47.img (3/6; tension label varies between `ch↔cs`, `ch↔cr`, and none)
- `onboarding_vp_drift` — opus46.img + son46.img (2/6, both image cells — suggests this heuristic is visually-triggered)

Heuristics surfaced by exactly one cell (unique readings):

- `cost_structure_leaks_to_ux` — Opus 4.6 text only (C\$↔VP observation about operational signal leaking into UX)
- `kr_insufficient` — Opus 4.6 image only (KR↔CR observation about streak-freeze capacity being under-provisioned)

### Tension cap rule — empirical verification

SKILL.md specifies: *any finding with non-empty `tension` at severity ≥ 3 forces the enclosing dimension score to ≤ 2*. This is enforced in `parse_audit_response` as a consistency check — a payload violating the rule fails to parse and the cell falls back.

Across 36 audited findings, no cell fell back on this rule. Per-cell verification:

| cell | sev-3+ tension findings per dim (max), capped dims ≤ 2 | ok |
|---|---|---|
| opus46.txt | vd: 2 (sev-4+sev-3); rr: 3 (sev-4 ×2 + sev-3); pc: 1 (sev-3) | ✓ all vd/rr/pc ≤ 2 |
| opus46.img | vd: 1 (sev-4); rr: 2 (sev-4 + sev-3 ×2); pc: 1 (sev-3) | ✓ all vd/rr/pc ≤ 2 |
| son46.txt | vd: 1 (sev-4); rr: 2 (sev-4 ×2); pc: 1 (sev-3) | ✓ all vd/rr/pc ≤ 2 |
| son46.img | vd: 1 (sev-3); rr: 2 (sev-4 ×2); pc: 1 (sev-3) | ✓ all vd/rr/pc ≤ 2 |
| op47.txt | vd: 1 (sev-3); rr: 2 (sev-4 ×2); pc: 1 (sev-3); ch↔cr at sev-2 (exempt) | ✓ all vd/rr/pc ≤ 2 |
| op47.img | vd: 1 (sev-3); rr: 2 (sev-4 ×2); pc: 1 (sev-3) | ✓ all vd/rr/pc ≤ 2 |

The rule is empirically producing dim scores of 1 (stacked cap) or 2 (single cap trigger) wherever a sev-3+ tension fires, with no fallbacks. Complementary observation: `infrastructure_fit` has zero sev-3+ tension findings across all 6 cells, and correspondingly escapes the cap — it is the only dimension ever scoring 3 or 4 in this matrix. The rule is doing exactly what the SKILL.md intended.

### Intent-equivalent — pattern is doing the work `intent` does in Kahneman

The Kahneman skill carries a `per-finding intent ∈ {dark_pattern, nudge, unintentional, absent}` slot; the Osterwalder skill does not. Its semantic counterpart is the `pattern` slot combined with the presence-or-absence of a `tension` — a sev-4 tension finding is the Osterwalder equivalent of a `dark_pattern` finding in severity-calibration terms.

The pattern-label histogram (above) is the closest thing this skill has to the Kahneman `intent_histogram`, and it carries exactly one narrative: four cells project onto `freemium` monoculture, two cells (both Opus 4.7) disambiguate freemium positioning from subscription implementation. The Osterwalder skill's `pattern` slot is doing less cross-cell differentiation work than Kahneman's `intent` slot did (4 labels actively used vs. 1-2 here) — a property of both the stimulus (one product, one pattern family) and the taxonomy (7 business-model patterns are more mutually exclusive than 4 intent labels are).

### Contract artefacts — what got written

All six cells produced the three-file set:

- `*.jsonl` — one `AuditVerdict` row, Pydantic-validated
- `*.native.jsonl` — full native Claude payload keyed on `verdict_id`
- `*.provenance.json` — summary with `dimension_score_totals`, `nielsen_severity_histogram`, `building_block_counts` (all 9 canvas codes), `pattern_histogram` (all 7 patterns), `tension_counts` (sorted by count desc then lex for deterministic diffs), `tension_findings` + `single_block_findings` gauges, `input_tokens`, `output_tokens`, `modality`, `mode`, `screenshot_bytes`, `screenshot_media_type`, `skill_hash`, `skill_id`, `model`, `temperature`, `max_tokens`, `fallback_count`, `fallback_reasons`, `transport_failure_count`.

The provenance shape is the business-alignment counterpart to Kahneman's `{intent_histogram, mechanism_counts}`: the Osterwalder skill's "what distinguishes this audit from that one" signal lives in the building-block histogram plus the pattern histogram plus the tension-pair tally.

**Meta-sidecar coverage is deliberately absent.** The smoke script does not write ADR-011 `.meta.json` sidecars — same known hygiene gap documented in the sister evals. The production `l4_audit_business_alignment` module does emit meta sidecars; the smoke path skips them because each run is a one-cell ad-hoc call.

## Caveats

- **One cluster, one HTML, one screenshot.** This is a six-cell matrix on a single deliberately-adversarial input. Generalisation to the full corpus requires the full-corpus run (deferred). The input was explicitly designed to stack mechanisms the skill's reference sheets name — this is a faithfulness test, not a discovery test.
- **The tension cap rule is doing most of the dimension-scoring work on this stimulus.** `revenue_relationships = 1` across all 6 audited cells is driven by the `sev ≥ 3 tension → dim ≤ 2` parser rule compounded by two sev-4 tension findings in the same dimension. The score is strongly correlated with the count of sev-3+ tension findings per dim, which is structurally what a freemium-stack produces. The rubric is working as designed, but the cap is binary — a cluster with a single sev-4 tension would still land at `dim=2` regardless of how many sev-1/sev-2 findings accumulate. Worth revisiting for the full-corpus audit once we see how often single-tension cells occur.
- **`vp_cs_mismatch` vs. `onboarding_vp_drift` is a label-ambiguity the skill does not disambiguate.** Every cell surfaces the CS↔VP tension; four cells label it `vp_cs_mismatch`, two cells (both image) label it `onboarding_vp_drift`. These are semantically the same tension reading at two abstraction levels, and a strict heuristic taxonomy would force them to collapse. The full-corpus tally needs to recognise them as aliases or the cross-cell heuristic histogram will under-count the most common Osterwalder reading on this product.
- **`pattern=freemium` monoculture in 4/6 cells is both correct and uninformative.** Duolingo is a freemium product; of course the label fits. But when 4 cells emit 5-7 findings each all tagged `freemium`, the `pattern` slot carries no per-finding signal — it becomes a cluster-level constant. Opus 4.7's split into `freemium` + `subscription` is the *only* cell where the pattern slot adds per-finding discrimination, and whether that generalises beyond this stimulus is an open question.
- **Efficiency-side blocks (KR/KA/KP/C\$) are underrepresented.** Across 36 findings, only 3 touch an Efficiency-side block (Opus 4.6 text's `c_dollar`, Opus 4.6 image's `kr`). No finding cites KA or KP at all; no tension pair spans Value/Efficiency sides. Two readings: (a) the stimulus is a customer-facing modal, Value-side only; the skill correctly focuses where the evidence is. (b) The skill under-surfaces Efficiency-side observations; a structural reading of "freemium is a subsidy from paying users' C\$ to free users' KR consumption" would be a legitimate Canvas audit line that none of 6 cells touches. The full-corpus run should track the value-side / efficiency-side ratio; if it stays at 33:1 (as here) across the corpus, that's a skill-level bias to investigate.
- **Opus 4.7 × text vs. × image is surprisingly invariant.** Unlike the Kahneman eval where Opus 4.7 × image produced a substantively different mechanism fingerprint from × text (and required a rerun to parse-clean), both Opus 4.7 cells here converged on 6 findings with identical dim-scores, identical heuristic set, and a single block-list difference on one finding. Not a non-determinism absence (Opus 4.7 still can't pin `temperature=0`) — just a stimulus on which the prose + image convey the same Canvas-level reading. For the full-corpus run, this means Osterwalder may be more modality-robust than Kahneman; confirm on multi-stimulus input.
- **Cost tracker 3× Opus overestimate still applies.** Same as the sister evals: tracker-reported Opus 4.6 and 4.7 spend is ~3× actual. Total live cost for this six-cell matched run was ~$0.25 real / ~$0.75 tracker.

## Reproducing this document

L4 is a Claude API layer; for this matched eval the smoke script bypasses the replay cache and always hits live (`mode: {text,image}_direct_sdk` in provenance). To reproduce byte-identically, the inputs to pin are:

- `data/derived/l4_audit/audit_business_alignment/audit_business_alignment_input.jsonl` → sha256 `dc6d981f…`
- `skills/audit-business-alignment/SKILL.md` → sha256 `37957d78…`, `skill_hash` `047320d2…`
- `data/artifacts/ui/duolingo_streak_modal.png` → sha256 `bcad10de…`, 119630 bytes, `image/png`
- `data/artifacts/ui/duolingo_streak_modal.html` → sha256 `cdfcbd47…`, 5677 bytes

Regenerate live (not replay — smoke is live-only):

```bash
bash scripts/run_l4_business_alignment_matched.sh --all
```

The `--all` flag forces re-runs of every cell regardless of prior result. Without it, the script's skip-if-success logic checks each cell's provenance `audited_count` and only re-runs cells that were fallbacks (or are missing entirely). Note that Opus 4.7 cells are structurally non-replayable (same reason as sister evals: `temperature=0` rejected → sampling unpinned): `--all` will produce non-byte-identical outputs on each invocation for those two cells, though this eval's evidence suggests the Osterwalder fingerprint is more modality/sample-stable than Kahneman's.

Per-cell (for one-off re-runs):

```bash
uv run python scripts/smoke_l4_business_alignment_multimodal.py \
  --model claude-opus-4-6 --modality text
uv run python scripts/smoke_l4_business_alignment_multimodal.py \
  --model claude-opus-4-6 --modality image
uv run python scripts/smoke_l4_business_alignment_multimodal.py \
  --model claude-sonnet-4-6 --modality text
uv run python scripts/smoke_l4_business_alignment_multimodal.py \
  --model claude-sonnet-4-6 --modality image
uv run python scripts/smoke_l4_business_alignment_multimodal.py \
  --model claude-opus-4-7 --modality text
uv run python scripts/smoke_l4_business_alignment_multimodal.py \
  --model claude-opus-4-7 --modality image
```
