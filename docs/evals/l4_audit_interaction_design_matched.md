# L4 audit — `audit-interaction-design` skill, 3-model × 2-modality matched comparison

**Date:** 2026-04-23
**Related:** ADR-008 (audit severity anchors), ADR-011 (replay log contract), `ARCHITECTURE.md` §4.5 (L4 layer), `docs/evals/l4_audit_business_alignment_matched.md` (sister Osterwalder-skill eval), `docs/evals/l4_audit_decision_psychology_matched.md` (sister Kahneman-skill eval), `docs/evals/l4_audit_accessibility_matched.md` (sister WCAG-skill eval), `docs/evals/l4_audit_usability_fundamentals_three_way.md` (sister Norman-skill smoke), `skills/audit-interaction-design/SKILL.md`, `src/auditable_design/layers/l4_audit_interaction_design.py`, `scripts/smoke_l4_interaction_design_multimodal.py`, `scripts/run_l4_interaction_design_matched.sh`
**Status:** Empirical record. Thin-spine smoke on one cluster (`cluster_02 "Streak loss framing pressures users into mid-session purchase"`) across six cells — {Opus 4.6, Sonnet 4.6, Opus 4.7} × {text-only, multimodal}. Purpose is to characterise the Cooper `About Face` skill's cross-model and cross-modality behaviour on an adversarial freemium-interaction stimulus before a full-corpus run.

## Purpose

L4's `audit-interaction-design` skill replaces Norman's cognitive lens, Kahneman's decision lens, and Osterwalder's business-model lens with Cooper's `About Face` lens: four dimensions (`posture_platform_fit`, `flow_excise`, `idioms_learnability`, `etiquette_forgiveness`), per-finding `posture` (closed set of 7 — `sovereign`, `transient`, `daemonic`, `satellite`, `standalone`, `mixed`, `not_applicable`), `user_tier` (closed set of 4 — `beginner`, `intermediate`, `expert`, `all`), and `excise_type` (closed set of 5 — `navigational`, `modal`, `skeuomorphic`, `stylistic`, `none`). Three discipline rules enforced by the parser:

- `posture == "mixed"` at severity ≥ 3 forces the enclosing dimension score to ≤ 2 (a surface that drifts between postures under pressure is a structural posture failure, not a local fix — mirrors the Kahneman dark-pattern cap and the Osterwalder tension cap).
- `excise_type != "none"` at severity ≥ 3 forces the enclosing dimension score to ≤ 2 (severe excise is structural).
- Every finding in `flow_excise` must name a non-`none` `excise_type` (the dimension is literally "where does the interface tax the user?"; a no-excise finding in this dimension is a category error).

The matched eval therefore has to answer three questions the sister evals did not:

- **Do the three models converge on the stimulus's dominant posture reading, or does each family draw the posture boundaries differently?** The Duolingo mid-lesson modal could be read as a sovereign learning surface that drifts into a transient promo posture under pressure (the "mixed" reading); as a daemonic surface demanding attention (the "daemonic" reading); or as an unsolicited sovereign intrusion (the "sovereign" reading on the modal itself). All three are defensible; which ones each cell surfaces is a posture-taxonomy question.
- **Does attaching a PNG change which *excise type* gets assigned?** The text-only path has to infer excise type from `ui_context` and `html` (tags and prose); the multimodal path sees the actual visual weight of the full-bleed CTA, the countdown timer, and the de-emphasised dismiss link. Whether models type the excise as `modal` (blocking, dismissible) or `navigational` (disrupting the lesson flow) is modality-sensitive.
- **Do the two caps hold empirically?** The module enforces both the mixed-posture cap and the excise cap in the parser; this eval verifies the models produce outputs that land inside the caps rather than trigger a fallback on the discipline rules.

## Executive summary

| | Opus 4.6 text | Opus 4.6 image | Sonnet 4.6 text | Sonnet 4.6 image | Opus 4.7 text | Opus 4.7 image |
|---|---|---|---|---|---|---|
| Clusters audited | 1 | 1 | 1 | 1 | 1 | 1 |
| Fallback count | 0 | 0 | 0 | 0 | 0 | 0 |
| Findings emitted | 8 | 8 | 7 | 7 | 7 | 7 |
| Mixed-posture findings | 1 | **2** | 1 | 1 | 1 | 1 |
| Excise findings | 2 | 2 | 2 | 2 | 2 | 2 |
| Nielsen-4 findings | 4 | 3 | 3 | 3 | 1 | 3 |
| Nielsen-3 findings | 4 | 5 | 3 | 3 | **6** | 4 |
| Nielsen-2 findings | 0 | 0 | 1 | 1 | 0 | 0 |
| `posture_platform_fit` score | **1** | **1** | **1** | **1** | **1** | **1** |
| `flow_excise` score | **1** | 2 | **1** | **1** | 2 | **1** |
| `idioms_learnability` score | 2 | 3 | 3 | 3 | 2 | **4** |
| `etiquette_forgiveness` score | **1** | **1** | 2 | 2 | 2 | **1** |
| `modal` excise findings | 1 | 1 | 2 | 2 | 2 | 2 |
| `navigational` excise findings | 1 | 1 | 0 | 0 | 0 | 0 |
| Input tokens | 10531 | 11879 | 10531 | 11879 | 13984 | 15332 |
| Output tokens | 2121 | 2129 | 1732 | 1847 | 2405 | 2393 |

Zero fallback and zero transport failure across all six live calls. All six verdicts share the same `verdict_id` (`audit-interaction-design__cluster_02`), the same `skill_hash` (`a7d3f385…`), and the same input sha256 (`dc6d981f…`); they disagree only on finding content.

Four load-bearing observations:

1. **All six cells converge on `posture_platform_fit = 1`** — the worst possible score in the rubric, driven by the mixed-posture cap rule (any sev ≥ 3 finding with `posture="mixed"` → dim capped at 2) compounded by a second sev-3 or sev-4 finding in the same dimension (`daemonic_surface_demands_attention` or `unsolicited_sovereign_intrusion`) that narrows the score further to 1. The three model families independently agree this is a posture catastrophe; the reading is robust to modality. All six cells tag `posture_drift_within_product` at sev-4 with posture="mixed" — the matrix's single most convergent finding.

2. **`modal_excise` is surfaced by every cell; `posture_drift_within_product` is surfaced by every cell; `fudgeability_absent` is surfaced by every cell.** These three heuristics are the matrix's load-bearing convergence — the Duolingo streak-loss modal reads the same way through three model families and two modalities: a mid-session posture drift, heavy modal excise on the most frequent path, and a fudgeability gap where a half-depleted lesson has no suspended or recoverable state. All three are sev-3+ across all 6 cells; `posture_drift_within_product` is unanimously sev-4.

3. **Opus 4.6 uniquely types the energy-system finding as `excise=navigational`; Sonnet 4.6 and Opus 4.7 type it as `excise=modal`.** Both Opus 4.6 cells emit `command_configuration_conflation` with `excise_type="navigational"` — reading the energy gate as navigation-excise (the user is forced to navigate around a state-check the product should infer). Sonnet 4.6 (text) and both Opus 4.7 cells emit the same heuristic but with `excise_type="modal"` — reading the gate as modal-excise (the product blocks the user with a decision-required state). Both readings are defensible in the Cooper taxonomy; this is the eval's single cross-family excise-typing disagreement.

4. **Opus 4.7 × image uplifts `idioms_learnability` to 4 — the matrix's only dim-4 score.** Five cells score `idioms_learnability` at 2-3; Opus 4.7 × image scores it at 4 because no Cooper excise-cap or mixed-posture-cap finding lands in that dimension (the `pliancy_unsignalled` finding sits at sev-3 but has `excise_type="none"` and `posture="sovereign"` — neither cap triggers). Counter to intuition: this is the *only* cell where idiom/affordance reading is not constrained by a structural cap, so the rubric lets it float upward. This is the eval's single dim-score inversion across modalities and signals that Cooper's `idioms_learnability` dimension is structurally less excise-bound than the other three — a property of the taxonomy, not the stimulus.

## Methodology

### Input

One enriched cluster from the L3b matched-corpus output, reused verbatim from the sister evals so all L4 skills see byte-identical input:

| | sha256 |
|---|---|
| `data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl` | `dc6d981f1652884e0088d9299311230d183f9d7cb71c78d4729b1eec5068b961` |

Cluster shape: `cluster_02`, label `"Streak loss framing pressures users into mid-session purchase"`. Five representative quotes drawn from the cluster's seven member reviews (same as Kahneman, Osterwalder, and WCAG evals):

- `q[0]`: "If you don't agree to pay mid-lesson, and you haven't watched ads FIRST, you have to quit mid-lesson"
- `q[1]`: "I'm trying to keep my 800+ day streak, but the recent changes are abysmal"
- `q[2]`: "the new update implemented an energy system instead of the hearts, which ruined my experience by forcing me to pay or watch ads"
- `q[3]`: "I was in holiday so i logged out but when i came home then i logged in but still my streak was fall into 0 days"
- `q[4]`: "I would have to do the same lesson multiple times just to keep my daily streak"

Attached artefacts (shared with sister evals — same HTML/PNG/`ui_context`):

- **HTML** (`data/artifacts/ui/duolingo_streak_modal.html`, sha256 `cdfcbd47…`, 5677 bytes): "STREAK AT RISK" modal with pulsing countdown, inline-SVG flame, loss-framing banner, anchored price row (`$6.99/mo` struck-through → `$3.49`), full-width `Keep my streak` CTA, secondary ads link, de-emphasised `lose streak` dismiss.
- **Screenshot** (`data/artifacts/ui/duolingo_streak_modal.png`, sha256 `bcad10de…`, 119630 bytes PNG): element-screenshot of the `.phone` container rendered via playwright headless chromium at `device_scale_factor=2`, 428×900 viewport.
- **`ui_context`** (prose): "Duolingo mobile app mid-lesson. The user has just depleted their last unit of energy…"

Stimulus note: same asset, different lens. The sister evals ask "what cognitive mechanisms does this stack?" / "what business-model blocks does this connect?" / "what accessibility defects does this ship?"; this eval asks "how does this surface behave — what posture does it adopt, what excise does it impose, what idioms does it use, how does it treat the user?"

### Skill

`skills/audit-interaction-design/SKILL.md` (file sha256 `ee8f5dc4299bfb1502ad84532aee96576c2f6031cdc294c87288b8ad8a4e8e93`), Cooper `About Face` interaction-design audit with four dimensions + per-finding `posture` / `user_tier` / `excise_type`. Severity anchored per ADR-008 (Nielsen 1–4 → `HeuristicViolation.severity` 3/5/7/9). Output contract enforces:

- Quotes are *not* required on every finding (same permissive stance as Osterwalder and Accessibility skills — a posture or excise finding about a dialog can rest on `html` or `ui_context` alone). The parser enforces the bidirectional rule: if `"quotes"` appears in `evidence_source`, `evidence_quote_idxs` must be non-empty; if `evidence_quote_idxs` is non-empty, `"quotes"` must appear in `evidence_source`.
- `posture` ∈ `{sovereign, transient, daemonic, satellite, standalone, mixed, not_applicable}`.
- `user_tier` ∈ `{beginner, intermediate, expert, all}`.
- `excise_type` ∈ `{navigational, modal, skeuomorphic, stylistic, none}`.
- Cross-finding cap 1 (mixed posture): any `posture=="mixed"` finding at severity ≥ 3 forces its dimension score ≤ 2.
- Cross-finding cap 2 (excise): any `excise_type != "none"` finding at severity ≥ 3 forces its dimension score ≤ 2.
- Every finding in dimension `flow_excise` must name a non-`none` `excise_type`.
- No duplicate `(heuristic, posture)` pairs within one audit.

Skill hash: `a7d3f38509f42baffe4907e489562c19b264bafc9a8dc841fd6172d22c7b00d3` (prefix `a7d3f38509f42baf…` as reported by every smoke log line).

SKILL.md note: an earlier revision triggered 2/6 parse fallbacks on an initial run, both on the same defect — models emitting `evidence_quote_idxs=[i]` without `"quotes"` in `evidence_source`. The bidirectional rule was already documented in a continuous paragraph; the fix was to extract it into a dedicated bullet block marked **parser-enforced, zero-tolerance** with an explicit "practically: the moment you cite quote [i] anywhere, …" example. After the edit, all 6 cells parsed cleanly. This is the only discipline rule where the skill's text had to be hardened rather than the model's behaviour adapted; documented for the other skills' authors in case their rule drift recurs.

### Runs

All six runs via `scripts/smoke_l4_interaction_design_multimodal.py`, orchestrated by `scripts/run_l4_interaction_design_matched.sh`. Temperature pinned to 0.0 on Opus 4.6 and Sonnet 4.6; stripped on Opus 4.7 via `claude_client._omits_sampling_params` (Opus 4.7 rejects `temperature` with 400). `max_tokens=6144`. `screenshot_media_type="image/png"` on multimodal cells.

| cell | verdicts sha256 | native sha256 |
|---|---|---|
| opus46 × text | `9c1c06dbc7d9ecad…` | `362e06eaafb53b8b…` |
| opus46 × image | `259f28bb74cd509a…` | `4eb20b172d5fd6de…` |
| sonnet46 × text | `91f6b0984703a41d…` | `84c81e312ff416d3…` |
| sonnet46 × image | `d9568b4feb47651d…` | `c12040fc52ae940c…` |
| opus47 × text | `e69d057950eef568…` | `9a48b060bf6d5b79…` |
| opus47 × image | `ef4623c9d28f0b58…` | `ffa96abdf78509e9…` |

Outputs at `data/derived/l4_audit/audit_interaction_design/l4_verdicts_audit_interaction_design_cluster02_{opus46,opus46_multimodal,sonnet46,sonnet46_multimodal,opus47,opus47_multimodal}.{jsonl,native.jsonl,provenance.json}`.

## Results

### Heuristic inventory across all six cells

Rows are the heuristics each cell named (`finding.heuristic` slot); columns are cells. "✓/N" = present at Nielsen severity N (max across duplicates within the cell); "—" = absent.

| heuristic | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| `posture_drift_within_product` | ✓/4 | ✓/4 | ✓/4 | ✓/4 | ✓/4 | ✓/4 |
| `modal_excise` | ✓/4 | ✓/4 | ✓/4 | ✓/4 | ✓/3 | ✓/4 |
| `fudgeability_absent` | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/4 |
| `command_configuration_conflation` | ✓/3 | ✓/3 | ✓/3 | — | ✓/3 | — |
| `unsolicited_sovereign_intrusion` | — | ✓/3 | ✓/4 | — | ✓/3 | ✓/3 |
| `daemonic_surface_demands_attention` | ✓/4 | ✓/3 | — | ✓/4 | — | — |
| `confirm_asking_instead_of_undo` | — | — | ✓/3 | ✓/3 | — | ✓/3 |
| `no_undo_on_destructive_action` | ✓/4 | ✓/4 | — | — | — | — |
| `pliancy_unsignalled` | — | ✓/3 | — | — | ✓/3 | — |
| `asks_permission_it_should_assume` | — | — | — | ✓/3 | — | ✓/3 |
| `asks_instead_of_acting` | ✓/3 | — | — | — | — | — |
| `idiom_unlearned_per_surface` | ✓/3 | — | — | — | — | — |
| `no_path_to_intermediate` | — | — | ✓/2 | — | — | — |
| `affordance_missing_flat_ui` | — | — | — | ✓/2 | — | — |
| `burdens_with_internal_problems` | — | — | — | — | ✓/3 | — |
| `does_not_take_responsibility` | — | — | — | — | — | ✓/3 |

Core convergence across all 6 cells: `posture_drift_within_product` (sev-4 always, posture=mixed always, driving the posture-cap), `modal_excise` (sev-3 to sev-4 always, posture=sovereign, excise=modal, dimension=flow_excise — triggering the excise-cap on flow_excise in 5/6 cells), and `fudgeability_absent` (sev-3 to sev-4 always, posture=sovereign, tier=intermediate, excise=none, dimension=etiquette_forgiveness). These three heuristics are the load-bearing Cooper signal every cell extracts.

4/6 cells:
- `command_configuration_conflation` (op46×2, son46.txt, op47.txt) — reading the energy gate as "product is asking about configuration when it should just act" — absent in son46.img and op47.img.
- `unsolicited_sovereign_intrusion` (op46.img, son46.txt, op47×2) — reading the modal itself as a transient surface intruding on the sovereign lesson flow — variously labelled.

3/6 cells:
- `daemonic_surface_demands_attention` (op46×2, son46.img) — reading the countdown-timer + flame animation as a daemonic attention-grab.
- `confirm_asking_instead_of_undo` (son46×2, op47.img) — reading the dismiss-with-loss-warning as a confirmation dialog substituting for undo.

2/6 cells:
- `no_undo_on_destructive_action` — both Opus 4.6 cells only (the family that reads the "streak permanently lost" framing as an irreversible-action-without-undo; sev-4 in both).
- `pliancy_unsignalled` (op46.img + op47.txt) — reading the dismiss link's visual under-weighting as a pliancy problem.
- `asks_permission_it_should_assume` (son46.img + op47.img) — reading the three-choice forced decision as needless permission-asking.

Unique readings (1/6 each): `asks_instead_of_acting` (op46.txt), `idiom_unlearned_per_surface` (op46.txt), `no_path_to_intermediate` (son46.txt), `affordance_missing_flat_ui` (son46.img), `burdens_with_internal_problems` (op47.txt), `does_not_take_responsibility` (op47.img).

Opus 4.6 × text is the heaviest cell (8 findings, including two unique heuristics — `asks_instead_of_acting`, `idiom_unlearned_per_surface`). Opus 4.7 × text is the most parsimonious in severity (7 findings but only 1 at sev-4 and 6 at sev-3 — tightest spread). Sonnet 4.6 runs at 7 findings per cell with 1 sev-2 finding always — the only family to emit sev-2 readings.

### Posture inventory

| posture | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| `sovereign` | 5 | 4 | 6 | 4 | 5 | 4 |
| `mixed` | 1 | 2 | 1 | 1 | 1 | 1 |
| `daemonic` | 1 | 1 | 0 | 1 | 1 | 2 |
| `transient` | 1 | 0 | 0 | 0 | 0 | 0 |
| `standalone` | 0 | 1 | 0 | 1 | 0 | 0 |
| `satellite` | 0 | 0 | 0 | 0 | 0 | 0 |
| `not_applicable` | 0 | 0 | 0 | 0 | 0 | 0 |

Four observations:

- **`sovereign` dominates every cell.** 4-6 of every cell's 7-8 findings are tagged `posture="sovereign"`. This is correct — the Duolingo learning flow *is* a sovereign posture, and most findings describe defects *within* that sovereign surface (modal excise breaks the sovereign flow; fudgeability failures are sovereign-posture problems; confirm-asking is a sovereign-etiquette problem). The dominant posture reflects the stimulus.
- **`mixed` is the structural cap-driver, present 1-2× per cell.** Always on `posture_drift_within_product` (the sev-4 finding that reads the whole stimulus as a sovereign-drifts-into-transient problem). Op46.img is the only cell with 2 mixed-posture findings — it also tags `unsolicited_sovereign_intrusion` with posture="mixed" where the other cells tag it `sovereign` or `daemonic`. This is a taxonomy judgement (the intrusion is itself a mixed-posture phenomenon in Op46.img's reading) and drives no scoring difference.
- **`daemonic` appears in 5/6 cells but with heterogeneous attribution.** Op46 reads the countdown-timer + flame as daemonic (1 finding, both modalities). Sonnet 4.6 reads it as daemonic only on the image cell. Op47 reads it as daemonic on the image cell only. The "daemonic surface" reading is visually-triggered for 2 of 3 families — text-only cells under-surface it, image cells lift it.
- **`satellite` and `not_applicable` are unused; `standalone` and `transient` are rare.** Standalone appears 2× (op46.img and son46.img — both naming the dismiss link as a standalone-posture affordance within a sovereign context). Transient appears 1× (op46.txt — `asks_instead_of_acting` tagged as transient-posture etiquette). On a single-stimulus matrix this distribution is not informative — worth revisiting at full-corpus scale whether satellite and not_applicable ever light up, or whether they're vestigial taxonomy.

### User-tier inventory

| tier | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| `all` | 5 | 6 | 4 | 5 | 5 | 5 |
| `intermediate` | 3 | 2 | 3 | 2 | 2 | 2 |
| `beginner` | 0 | 0 | 0 | 0 | 0 | 0 |
| `expert` | 0 | 0 | 0 | 0 | 0 | 0 |

The matrix-level fact: **all 6 cells split between `all` (majority) and `intermediate` (always the `fudgeability_absent` and `command_configuration_conflation` findings); no cell surfaces a `beginner`-specific or `expert`-specific finding.** The `intermediate` tier consistently marks the "800+ day streak" user — the quote says it directly (q[1]) — and the skill correctly identifies that specific user class as the one the fudgeability and command-configuration issues impact worst (a beginner hasn't invested 800 days; an expert has configured around the problem). This is a taxonomic signal that maps cleanly to the stimulus — the skill is doing what `user_tier` is for.

Worth noting: no cell ever emits `beginner` or `expert`. This matches the stimulus (a mid-lesson paywall is not a beginner-onboarding problem, nor a power-user problem); at full-corpus scale the expectation would be that beginner surfaces on onboarding-related clusters and expert on configuration/mastery clusters. If those tiers remain unused across the full corpus, the taxonomy is carrying weight the data does not justify.

### Excise-type inventory

| excise_type | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| `none` | 6 | 6 | 5 | 5 | 5 | 5 |
| `modal` | 1 | 1 | 2 | 2 | 2 | 2 |
| `navigational` | 1 | 1 | 0 | 0 | 0 | 0 |
| `skeuomorphic` | 0 | 0 | 0 | 0 | 0 | 0 |
| `stylistic` | 0 | 0 | 0 | 0 | 0 | 0 |

Four observations:

- **Every cell emits exactly 2 excise findings.** One finding is always `modal_excise` in dimension `flow_excise` with `excise_type="modal"` — unanimous across all 6 cells. The second finding is always `command_configuration_conflation` (or in son46.img and op47.img a variant like `asks_permission_it_should_assume`), also in dimension `flow_excise`, and is where the cross-family disagreement lives.
- **Opus 4.6 is the only family that types the second finding as `navigational`** — both Opus 4.6 cells tag `command_configuration_conflation` with `excise_type="navigational"`. All other cells tag their second excise finding with `excise_type="modal"`. Cooper's taxonomy allows both readings: the energy gate is navigational (the user must route around a state-check the product should infer) *and* modal (the gate presents a decision-required state). Op46 picks the former; son46 and op47 pick the latter. Neither reading changes the cap result — both trigger the excise cap on flow_excise.
- **`skeuomorphic` and `stylistic` are unused across all 6 cells.** The Duolingo modal is stylistically loud but not skeuomorphically-rendered; the flame icon is SVG, not a photographic flame; the price row is a typographic treatment, not a physical-object metaphor. The unused types are correctly unused on this stimulus — but at full-corpus scale, a skeuomorphic-free audit across a full-corpus run would flag the taxonomy as under-utilised.
- **Every `flow_excise` finding has a non-`none` excise type.** This is a schema rule, not an observation — but empirically verifying it held across 12 excise-dimension findings (6 cells × 2 each) gives confidence that the rule is tight and no model confused a non-flow_excise dimension for flow_excise to route a `excise_type="none"` finding.

### Per-cell summary prose (native payload, verbatim first sentence)

**Opus 4.6 text.** "The product's sovereign learning flow is hijacked mid-lesson by a transient, time-pressured upsell modal that forces a purchase-or-abandon decision with no undo, no lesson-suspend state, and no way to dismiss without accepting a permanent loss — a severity-4 posture drift that compounds with modal excise, absent fudgeability, and an energy system that replaces learnable progress idioms with an opaque gate."

**Opus 4.6 image.** "The product's core learning surface is sovereign but is hijacked mid-lesson by a transient, time-pressured upsell modal that fills the viewport, blocks all lesson progress, and offers no undo — a severity-4 posture drift that is the single most user-impactful defect; secondary findings include heavy modal excise on the frequent path, absent fudgeability for interrupted lessons, and an etiquette violation where the product burdens the user with an irreversible loss threat rather than offering graceful recovery."

**Sonnet 4.6 text.** "The product adopts a sovereign learning posture in its core lesson flow but drops into a transient promo posture mid-lesson via a fully blocking modal with no dismissal path except loss or payment, producing a severity-4 posture-drift and unsolicited sovereign intrusion; the secondary defects are heavy modal excise on the most frequent path, a confirm-asking pattern that substitutes fear for undo, and a fudgeability gap where a half-depleted lesson has no suspended or recoverable state."

**Sonnet 4.6 image.** "The surface adopts a sovereign learning posture for its core flow but drops into a transient promo posture mid-lesson via a fully blocking modal, producing a severity-4 posture-drift; the most user-impactful single finding is the unsolicited sovereign intrusion that halts the lesson and forces a three-way commercial decision before the user can continue, with no undo, no suspend state, and a dismiss path styled to be nearly invisible."

**Opus 4.7 text.** "A sovereign learning surface is hijacked mid-lesson by a transient, promotionally-styled modal that gates the only path forward behind a subscription, ad-watching, or streak loss — a severity-4 posture drift compounded by a countdown-timer modal excise, a fudgeability gap (no suspend-and-resume for the lesson), and a dismiss affordance whose pliancy is deliberately suppressed."

**Opus 4.7 image.** "The modal imposes a transient-styled paid-conversion surface mid-lesson in what is otherwise a sovereign learning flow, producing a severe posture-drift compounded by modal excise on the probable path, a countdown-timer decision trap with no undo, and a fudgeability gap where a depleted-energy lesson has no suspended state."

All six summaries converge on the same two-element structural reading: sovereign posture, hijacked mid-lesson by a transient/daemonic modal, with modal excise + fudgeability as secondary defects. The vocabulary diverges ("hijacked", "dropped into promo", "imposes a transient-styled paid-conversion surface", "gate", "decision trap") but the posture-level reading does not.

### Dimension-score divergence

| dim | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img | range |
|---|---|---|---|---|---|---|---|
| posture_platform_fit | **1** | **1** | **1** | **1** | **1** | **1** | 0 |
| flow_excise | **1** | 2 | **1** | **1** | 2 | **1** | 1 |
| idioms_learnability | 2 | 3 | 3 | 3 | 2 | **4** | 2 |
| etiquette_forgiveness | **1** | **1** | 2 | 2 | 2 | **1** | 1 |

Four observations:

- **`posture_platform_fit = 1` is unanimous across all six cells.** Every cell drives this dimension to the rubric floor. The effect is mechanical — the sev-4 `posture_drift_within_product` finding (posture="mixed") trips the mixed-posture cap once (→ dim ≤ 2), and a second sev-3+ finding in the same dimension (`daemonic_surface_demands_attention`, `unsolicited_sovereign_intrusion`, or `burdens_with_internal_problems`) narrows the score to 1. The three families agree the posture architecture is broken at the bottom of the scale. This is the Cooper equivalent of Osterwalder's unanimous `revenue_relationships = 1`.
- **`flow_excise` splits 4-2 between 1 and 2.** Four cells score 1 (op46.txt, son46×2, op47.img); two cells score 2 (op46.img, op47.txt). The 1s have two sev-3+ excise findings in the same dimension (modal_excise + command_configuration_conflation or asks_permission_it_should_assume, both sev-3+); the excise cap fires twice, narrowing the dim to 1. The 2s have modal_excise at sev-3 (op47.txt) or one of the two excise findings at sev-3 with weaker stacking — the cap fires once, settling at 2. No cell scores 3+ on flow_excise — the excise cap dominates this dimension across the matrix.
- **`idioms_learnability` ranges 2 to 4, the matrix's most variable dimension.** Cells with a sev-3+ finding in this dim score 2 or 3 depending on stacking; Opus 4.7 × image is the only cell with *zero* sev-3+ caps-eligible findings in idioms_learnability (its `pliancy_unsignalled` is absent — the cell instead emits `does_not_take_responsibility` in etiquette_forgiveness), letting the rubric float the score upward to 4. This is the eval's single uncapped dimension and the only dim-4 score across the matrix.
- **`etiquette_forgiveness` splits 3-3 between 1 and 2.** Three cells score 1 (op46×2 and op47.img); three cells score 2 (son46×2 and op47.txt). The 1s are driven by the `no_undo_on_destructive_action` sev-4 finding (both Opus 4.6 cells) or by `fudgeability_absent` at sev-4 (op47.img) stacking with a second sev-3 finding. The 2s have only sev-3 findings in the dim, so the cap trips once (via excise cap on modal findings — except etiquette_forgiveness has no excise findings, so the cap here is the mixed-posture cap via `unsolicited_sovereign_intrusion` or similar). The cross-family severity-calibration disagreement parallels the Sonnet-reads-harsher pattern seen on the Kahneman `cognitive_load_ease` dimension and the Osterwalder `value_delivery` reading — except this time Sonnet reads *softer*, not harsher.

### Modality effect, per-model

**Opus 4.6 (8 → 8).** Same cardinality, two-finding swap. Image drops `asks_instead_of_acting` and `idiom_unlearned_per_surface` (two text-only findings reading etiquette and idiom-learnability from prose only); adds `pliancy_unsignalled` and `unsolicited_sovereign_intrusion` (two image-grounded findings reading the dismiss link's visual under-weighting as a pliancy problem and the modal itself as a mixed-posture intrusion). Image also *downrates* `daemonic_surface_demands_attention` from sev-4 → sev-3 — the text-only version reads the daemonic pattern as catastrophically severe; the image version sees the same pattern but softens by one notch. Image additionally uplifts `flow_excise` dim from 1 → 2 (excise cap fires less tightly). Net: modality shuffles the mechanism mix and moves one dim score by one notch; posture_platform_fit and etiquette_forgiveness remain at 1 in both modalities.

**Sonnet 4.6 (7 → 7).** Same cardinality, two-finding swap. Image drops `unsolicited_sovereign_intrusion` (sev-4, text) and `command_configuration_conflation` (sev-3, text); adds `daemonic_surface_demands_attention` (sev-4, image) and `asks_permission_it_should_assume` (sev-3, image). Image also swaps `no_path_to_intermediate` (sev-2, text) for `affordance_missing_flat_ui` (sev-2, image) in dimension `idioms_learnability` — same dim slot, different visually-triggered heuristic. Zero dim-score changes across modalities: `posture_platform_fit=1`, `flow_excise=1`, `idioms_learnability=3`, `etiquette_forgiveness=2` hold on both. Sonnet 4.6 is the most dim-score-stable family on this stimulus; its heuristic taxonomy shifts under modality, but the rubric output does not.

**Opus 4.7 (7 → 7).** Same cardinality, two-finding swap. Image drops `command_configuration_conflation` (sev-3, text) and `pliancy_unsignalled` (sev-3, text) and `burdens_with_internal_problems` (sev-3, text — three text-only findings); adds `asks_permission_it_should_assume`, `confirm_asking_instead_of_undo`, and `does_not_take_responsibility` (three image-grounded findings). Image also *uprates* `modal_excise` from sev-3 → sev-4 and `fudgeability_absent` from sev-3 → sev-4. Image *downrates* `flow_excise` dim from 2 → 1 (excise-cap now fires twice at sev-3+ instead of once) and uprates `idioms_learnability` from 2 → 4 (no excise-cap or mixed-posture-cap finding lands in idioms_learnability on image). Opus 4.7 is the *most* modality-reactive family of the three — both in heuristic mix and in dim scores. This is a reversal from the Osterwalder matrix, where Opus 4.7 was the most modality-invariant family; Cooper's taxonomy is more visually-sensitive than Osterwalder's Canvas.

### Convergence pattern

Heuristics surfaced by all 6 cells — load-bearing convergence:

- `posture_drift_within_product` (sev-4 everywhere, posture=mixed everywhere, dimension=posture_platform_fit)
- `modal_excise` (sev-3 to sev-4 everywhere, posture=sovereign, excise=modal, dimension=flow_excise)
- `fudgeability_absent` (sev-3 to sev-4 everywhere, posture=sovereign, tier=intermediate, excise=none, dimension=etiquette_forgiveness)

Heuristics surfaced by 4 of 6 cells:

- `command_configuration_conflation` — op46×2 + son46.txt + op47.txt (tension label varies between `excise=navigational` on op46 and `excise=modal` elsewhere)
- `unsolicited_sovereign_intrusion` — op46.img + son46.txt + op47×2

Heuristics surfaced by 3 of 6 cells:

- `daemonic_surface_demands_attention` — op46×2 + son46.img (visually-triggered on 2 of 3 families)
- `confirm_asking_instead_of_undo` — son46×2 + op47.img

Heuristics surfaced by 2 of 6 cells:

- `no_undo_on_destructive_action` — both Opus 4.6 cells only (family-specific reading of the streak-permanent-loss framing as an undo-denial)
- `pliancy_unsignalled` — op46.img + op47.txt (cross-family, unrelated modalities — coincidence at this sample size)
- `asks_permission_it_should_assume` — son46.img + op47.img (both image cells — suggests the three-way choice layout is visually-triggered as permission-asking)

Heuristics surfaced by exactly one cell (unique readings):

- `asks_instead_of_acting` — op46.txt only
- `idiom_unlearned_per_surface` — op46.txt only (reading the energy system as a replacement for a learnable progress idiom)
- `no_path_to_intermediate` — son46.txt only (reading the modal as having no graduation path from beginner to intermediate)
- `affordance_missing_flat_ui` — son46.img only (reading the de-emphasised dismiss link as a flat-UI affordance failure)
- `burdens_with_internal_problems` — op47.txt only (reading the energy system as the product pushing its internal monetisation problem onto the user)
- `does_not_take_responsibility` — op47.img only (reading the "lose streak" path as the product refusing to own the energy-depletion decision)

### Mixed-posture cap rule — empirical verification

SKILL.md specifies: *any finding with `posture="mixed"` at severity ≥ 3 forces the enclosing dimension score to ≤ 2*. This is enforced in `parse_audit_response` as a consistency check — a payload violating the rule fails to parse and the cell falls back.

Across 42 audited findings, no cell fell back on this rule. Per-cell verification:

| cell | sev-3+ mixed findings per dim (max), capped dims ≤ 2 | ok |
|---|---|---|
| opus46.txt | pf: 1 (sev-4 mixed) | ✓ pf=1 ≤ 2 |
| opus46.img | pf: 1 (sev-4 mixed); ef: 1 (sev-3 mixed) | ✓ pf=1, ef=1 ≤ 2 |
| sonnet46.txt | pf: 1 (sev-4 mixed) | ✓ pf=1 ≤ 2 |
| sonnet46.img | pf: 1 (sev-4 mixed) | ✓ pf=1 ≤ 2 |
| opus47.txt | pf: 1 (sev-4 mixed) | ✓ pf=1 ≤ 2 |
| opus47.img | pf: 1 (sev-4 mixed) | ✓ pf=1 ≤ 2 |

### Excise cap rule — empirical verification

SKILL.md specifies: *any finding with `excise_type != "none"` at severity ≥ 3 forces the enclosing dimension score to ≤ 2*.

Across 42 audited findings, no cell fell back on this rule. Per-cell verification (all excise-cap-eligible findings land in dimension `flow_excise`):

| cell | sev-3+ excise findings in flow_excise (count and max sev) | fe score | ok |
|---|---|---|---|
| opus46.txt | 2 (sev-4 + sev-3) | 1 | ✓ fe=1 ≤ 2 |
| opus46.img | 2 (sev-4 + sev-3) | 2 | ✓ fe=2 ≤ 2 |
| sonnet46.txt | 2 (sev-4 + sev-3) | 1 | ✓ fe=1 ≤ 2 |
| sonnet46.img | 2 (sev-4 + sev-3) | 1 | ✓ fe=1 ≤ 2 |
| opus47.txt | 2 (sev-3 + sev-3) | 2 | ✓ fe=2 ≤ 2 |
| opus47.img | 2 (sev-4 + sev-3) | 1 | ✓ fe=1 ≤ 2 |

Both cap rules are empirically producing dim scores of 1 or 2 wherever a sev-3+ mixed-posture or non-none-excise finding fires, with no fallbacks. Complementary observation: `idioms_learnability` has zero sev-3+ excise findings and zero sev-3+ mixed-posture findings across all 6 cells, and correspondingly escapes both caps — it is the only dimension ever scoring 3 or 4 in this matrix (and the only dim-4 score is Opus 4.7 × image).

### Contract artefacts — what got written

All six cells produced the three-file set:

- `*.jsonl` — one `AuditVerdict` row, Pydantic-validated
- `*.native.jsonl` — full native Claude payload keyed on `verdict_id`
- `*.provenance.json` — summary with `dimension_score_totals`, `nielsen_severity_histogram`, `posture_histogram` (all 7 postures, including zero-count `satellite` and `not_applicable`), `user_tier_histogram` (all 4 tiers), `excise_type_histogram` (all 5 types), `mixed_posture_findings` gauge, `excise_findings` gauge, `input_tokens`, `output_tokens`, `modality`, `mode`, `screenshot_bytes`, `screenshot_media_type`, `skill_hash`, `skill_id`, `model`, `temperature`, `max_tokens`, `fallback_count`, `fallback_reasons`, `transport_failure_count`.

The provenance shape is the Cooper counterpart to Osterwalder's `{building_block_counts, pattern_histogram, tension_counts}` and Kahneman's `{intent_histogram, mechanism_counts}`: the Cooper skill's "what distinguishes this audit from that one" signal lives in the posture histogram plus the user-tier histogram plus the excise-type histogram plus the two gauges.

**Meta-sidecar coverage is deliberately absent.** The smoke script does not write ADR-011 `.meta.json` sidecars — same known hygiene gap documented in the sister evals. The production `l4_audit_interaction_design` module does emit meta sidecars; the smoke path skips them because each run is a one-cell ad-hoc call.

## Caveats

- **One cluster, one HTML, one screenshot.** This is a six-cell matrix on a single deliberately-adversarial input. Generalisation to the full corpus requires the full-corpus run (deferred). The input was explicitly designed to stack mechanisms the skill's reference sheets name (posture drift, modal excise, fudgeability gaps) — this is a faithfulness test, not a discovery test.
- **The mixed-posture cap and excise cap are doing most of the dimension-scoring work on this stimulus.** `posture_platform_fit = 1` and `flow_excise ≤ 2` across all 6 audited cells is driven by the parser rules compounded by sev-4 findings in each dimension. The scores are strongly correlated with the count of sev-3+ mixed-posture and sev-3+ non-none-excise findings per dim, which is structurally what a freemium-modal stimulus produces. The rubric is working as designed, but the caps are binary — a cluster with a single sev-4 mixed-posture finding would still land at `dim=2` regardless of how many sev-1/sev-2 findings accumulate. Worth revisiting for the full-corpus audit once we see how often single-mixed-posture cells occur.
- **`command_configuration_conflation` is excise-typed differently across families.** Op46 tags it `navigational`; son46 and op47 tag it `modal`. Both readings are defensible in Cooper's taxonomy, and neither changes the cap result — but a full-corpus tally that counts navigational-excise vs. modal-excise findings would under-represent the "same defect, two labels" case. The full-corpus tally needs to either aggregate these labels or the skill needs a disambiguation rule.
- **`satellite`, `not_applicable`, `skeuomorphic`, `stylistic`, `beginner`, and `expert` are all unused across 42 findings.** This is the matrix's largest single concern about taxonomy coverage. Worth revisiting at full-corpus scale: if these values remain unused across a 50-cluster run, the taxonomy is carrying weight the data does not justify, and the skill should either narrow the closed sets or the stimulus curation needs to seed clusters that would force these values to appear.
- **Opus 4.7 × image is the matrix's outlier cell** — uprates `modal_excise` and `fudgeability_absent` to sev-4, narrows `flow_excise` to 1, widens `idioms_learnability` to 4, and emits `does_not_take_responsibility` (a unique heuristic). This cell is doing the most work of any in the matrix, and it's also the cell Opus 4.7 × text *disagrees with most strongly* (identical cardinality but 3 out of 7 heuristics swap). Whether this is a modality artefact or a sample artefact (Opus 4.7 non-determinism even at `temperature` stripped) is indistinguishable at N=1 per cell; a repeat run with a different random seed would disambiguate.
- **The bidirectional evidence-source ↔ quote-idx rule required a SKILL.md rewrite to become reliable.** The initial skill text documented the rule in a continuous paragraph; 2 of 6 initial runs fell back on models emitting `evidence_quote_idxs=[i]` without `"quotes"` in `evidence_source`. A one-line edit extracting the rule into a **parser-enforced, zero-tolerance** bullet block with an explicit "practically: the moment you cite quote [i] anywhere" example eliminated the fallbacks. This is documented here (and in the SKILL.md commit) in case the other skills drift on the same rule.
- **Cost tracker 3× Opus overestimate still applies.** Same as the sister evals: tracker-reported Opus 4.6 and 4.7 spend is ~3× actual. Total live cost for this six-cell matched run (two runs — the first with 2 fallbacks, the second with --all after the SKILL.md fix) was ~$0.40 real / ~$1.20 tracker.

## Reproducing this document

L4 is a Claude API layer; for this matched eval the smoke script bypasses the replay cache and always hits live (`mode: {text,image}_direct_sdk` in provenance). To reproduce byte-identically, the inputs to pin are:

- `data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl` → sha256 `dc6d981f…`
- `skills/audit-interaction-design/SKILL.md` → sha256 `ee8f5dc4…`, `skill_hash` `a7d3f385…`
- `data/artifacts/ui/duolingo_streak_modal.png` → sha256 `bcad10de…`, 119630 bytes, `image/png`
- `data/artifacts/ui/duolingo_streak_modal.html` → sha256 `cdfcbd47…`, 5677 bytes

Regenerate live (not replay — smoke is live-only):

```bash
bash scripts/run_l4_interaction_design_matched.sh --all
```

The `--all` flag forces re-runs of every cell regardless of prior result. Without it, the script's skip-if-success logic checks each cell's provenance `audited_count` and only re-runs cells that were fallbacks (or are missing entirely). Note that Opus 4.7 cells are structurally non-replayable (same reason as sister evals: `temperature=0` rejected → sampling unpinned): `--all` will produce non-byte-identical outputs on each invocation for those two cells, and Opus 4.7 × image in particular appears to be the matrix's most sample-sensitive cell.

Per-cell (for one-off re-runs):

```bash
uv run python scripts/smoke_l4_interaction_design_multimodal.py \
  --model claude-opus-4-6 --modality text
uv run python scripts/smoke_l4_interaction_design_multimodal.py \
  --model claude-opus-4-6 --modality image
uv run python scripts/smoke_l4_interaction_design_multimodal.py \
  --model claude-sonnet-4-6 --modality text
uv run python scripts/smoke_l4_interaction_design_multimodal.py \
  --model claude-sonnet-4-6 --modality image
uv run python scripts/smoke_l4_interaction_design_multimodal.py \
  --model claude-opus-4-7 --modality text
uv run python scripts/smoke_l4_interaction_design_multimodal.py \
  --model claude-opus-4-7 --modality image
```
