# L4 audit-ux-architecture — matched-model × modality eval

**Scope:** one cluster (`cluster_02`) × three models (Opus 4.6 / Sonnet 4.6 / Opus 4.7) × two modalities (text / text+image). 6 cells. Eval character: thin-spine smoke over one adversarial cluster to characterise the Garrett `Elements of User Experience` skill's cross-model and cross-modality behaviour before a full-corpus run.

**Status:** Empirical record. Live Anthropic calls, zero replay cache. 5/6 cells audited; 1/6 cells fallback on a parser-level asymmetry (Sonnet 4.6 × image emitted a non-empty `evidence_quote_idxs` without `"quotes"` in `evidence_source`). The fallback is a load-bearing data observation in itself — the skill's hardened bidirectional rule shipped day one, yet this model × modality pair still violated it. See *Fallback analysis*.

## Purpose

L4's `audit-ux-architecture` skill replaces Norman's cognitive lens, Kahneman's decision lens, Osterwalder's business-model lens, and Cooper's interaction-design lens with Garrett's five-plane lens: five dimensions (`strategy_coherence`, `scope_coverage`, `structure_navigation`, `skeleton_wireframe`, `surface_sensory`), per-finding `product_type` (closed set of 4 — `functional`, `informational`, `hybrid`, `not_applicable`), and per-finding `decision_mode` (closed set of 5 — `conscious`, `default`, `mimicry`, `fiat`, `not_applicable`). Two discipline rules enforced by the parser:

- `decision_mode` ∈ {`default`, `mimicry`, `fiat`} at severity ≥ 3 forces the enclosing dimension score to ≤ 2 (Garrett's central moral claim: every UX element should be the product of a conscious decision; an unconscious decision at sev ≥ 3 is structural, not local).
- Two findings may share a `heuristic` but must not share the same `(heuristic, product_type)` pair (canonical example: `featuritis` exists both as feature accretion on a functional surface and as content accretion on an informational surface — two distinct findings, not a duplicate).

Plus the cross-skill **bidirectional evidence rule** inherited from Cooper's hardened form (zero-tolerance, parser-enforced; dedicated bullet block in SKILL.md).

The three questions this eval tries to answer on a single adversarial stimulus:

- **Do the three model families converge on *which plane is most broken* on this cluster?** Garrett's cascade is directional (strategy → scope → structure → skeleton → surface); if one plane is broken, the audit should identify which, and the convergence across models tells us whether that diagnosis is robust.
- **Which `decision_mode` annotations are stable across models?** The central `fiat` / `mimicry` / `default` vs `conscious` distinction is the skill's main judgment call. Cross-family stability here is the audit's load-bearing signal.
- **Does attaching a PNG change the decision-mode reading?** The text path sees the modal only through `ui_context` and `html`; the multimodal path sees the actual visual contrast between the marketing-modal language and the lesson skeleton. Whether the visual evidence shifts the eye toward `fiat` (top-down override) vs `mimicry` (copied from marketing) vs `conscious` (deliberate, wrong) is modality-sensitive.

## Executive summary

| metric                         | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---                             |---:|---:|---:|---:|---:|---:|
| Status                         | audited | audited | audited | **fallback** | audited | audited |
| `strategy_coherence`           | 2 | 2 | 2 | — | 2 | **1** |
| `scope_coverage`               | 2 | 2 | 2 | — | 2 | 2 |
| `structure_navigation`         | 3 | 3 | 3 | — | 2 | 2 |
| `skeleton_wireframe`           | 1 | 1 | 1 | — | 1 | 1 |
| `surface_sensory`              | 2 | 2 | 2 | — | 2 | 2 |
| Findings emitted               | 8 | 8 | 7 | 0 | 7 | 7 |
| Unconscious-decision findings  | 2 | 2 | 3 | 0 | 2 | 2 |
| Nielsen-4 findings             | 1 | 1 | 1 | 0 | 1 | **2** |
| Nielsen-3 findings             | 5 | 4 | 4 | 0 | 4 | 4 |
| Nielsen-2 findings             | 2 | 3 | 1 | 0 | 2 | 1 |
| Nielsen-1 findings             | 0 | 0 | 1 | 0 | 0 | 0 |
| `fiat` decision-mode findings  | 1 | 1 | 1 | 0 | 1 | 1 |
| `mimicry` decision-mode findings | 1 | 1 | 1 | 0 | 1 | 1 |
| `default` decision-mode findings | 0 | 0 | 1 | 0 | 0 | 0 |
| input tokens                   | 10 787 | 12 135 | 10 787 | 12 135 | 14 628 | 15 976 |
| output tokens                  | 2 202 | 2 176 | 1 990 | 1 781 | 2 587 | 2 717 |

All five audited cells converge on `skeleton_wireframe = 1` — the worst possible score on the rubric, driven by a single `decision_mode="fiat"` sev-4 finding (`skeleton_does_not_honour_priority`) in every audited cell. The five-model convergence on "the skeleton plane is the most broken" is the matrix's single most robust signal. All five also converge on `scope_coverage = 2` (`scope_creep_mid_build` sev-3 unanimous).

Five load-bearing observations:

1. **All five audited cells tag `skeleton_does_not_honour_priority` at sev-4 with `decision_mode="fiat"`.** The models independently read the mid-lesson modal as a top-down override of the skeleton plane's logic — a marketing surface replacing the lesson skeleton wholesale, with the lesson's own skeleton elements (progress bar, question area, answer strip) hidden rather than preserved. The `product_type` slot for this finding varies (`functional` / `hybrid`) but the heuristic, severity, and decision-mode converge. The fiat claim is grounded in the same textual evidence across all five cells: the lesson skeleton disappears at modal fire-time and returns only after the modal resolves.

2. **All five audited cells tag `strategy_contradicts_itself`, `scope_creep_mid_build`, `competing_calls_to_action`, and `visual_language_inherited_from_brand_without_product_fit`.** These five heuristics (together with `skeleton_does_not_honour_priority`) are the eval's convergent Garrett reading: a strategy that contends with itself (learning-tool vs subscription-funnel), a scope that accreted monetisation inside the lesson flow, a skeleton that was overridden by a top-down command, competing actions with mis-weighted visual priority, and a surface language lifted from marketing.

3. **Opus 4.7 × image uniquely elevates strategy to sev-4 (`strategy_coherence=1`).** This is the matrix's only cell with two sev-4 findings — `strategy_contradicts_itself` at sev-4 *and* `skeleton_does_not_honour_priority` at sev-4. The multimodal input lets Opus 4.7 articulate the strategy-contradiction as a surface-visible phenomenon (two strategies contending for the same pixels) rather than a logically inferable one. Text-mode Opus 4.7 scores the same finding at sev-3. This is the eval's single modality-uplift result.

4. **Sonnet 4.6 × text is the only cell emitting `decision_mode="default"`.** Its seventh finding (`motion_decorative_not_functional` on the pulsing countdown) is tagged `default` — the only `default` in the matrix, against every other cell's `fiat`+`mimicry` (2 unconscious findings per cell, always this pair). Sonnet's reading attributes the pulsing animation to "framework/library default" rather than a design decision; the other four families treat the same element as `conscious` (a deliberate urgency signal) or do not surface it at all. The reading is defensible but out-of-distribution; worth flagging.

5. **`product_type` slot is the eval's least stable field.** Cells agree on heuristic and severity for the five load-bearing findings but disagree on `product_type`: `strategy_contradicts_itself` is tagged `hybrid` (op47.txt, son46.txt, op47.img's raw response) or `not_applicable` (op46.txt, op46.img, op47.img) depending on the cell; `skeleton_does_not_honour_priority` is tagged `functional` (op46.txt, son46.txt) or `hybrid` (op46.img, op47.txt, op47.img). The skill's SKILL.md allows both `hybrid` (the seam) and single-type answers; the inter-cell `product_type` variance is *within the skill's tolerance* and is not a defect. If a future L5 aggregation wants to deduplicate findings across models, the `(heuristic, severity)` pair is a more stable key than `(heuristic, product_type)`.

## Methodology

### Input

Cluster shape: `cluster_02`, label `"Streak loss framing pressures users into mid-session purchase"`. Five representative quotes drawn from the cluster's seven member reviews (same as Cooper, Kahneman, Osterwalder, and WCAG evals; byte-identical fixture shared across L4 skills):

- `q[0]`: "streak saver popup is outright manipulative — pulsing timer, giant green button, dismiss link in grey 11px text"
- `q[1]`: "I'm trying to keep my 800+ day streak, but the recent changes are abysmal"
- `q[2]`: "the new update implemented an energy system instead of the hearts, which ruined my experience by forcing me to pay or watch ads"
- `q[3]`: "cannot concentrate on the lesson because mid-lesson the whole screen takes over"
- `q[4]`: "I click the wrong button and immediately three ads start playing with no way to cancel"

Cluster carries three enrichment channels:

- **HTML** (`data/artifacts/ui/duolingo_streak_modal.html`, sha256 `cdfcbd47…`, 5677 bytes): "STREAK AT RISK" modal with pulsing countdown, inline-SVG flame, loss-framing banner, anchored price row (`$6.99/mo` struck-through → `$3.49`), full-width `Keep my streak` CTA, secondary ads link, de-emphasised `lose streak` dismiss.
- **Screenshot** (`data/artifacts/ui/duolingo_streak_modal.png`, sha256 `bcad10de…`, 119 630 bytes PNG): element-screenshot of the `.phone` container rendered via playwright headless chromium at `device_scale_factor=2`, 428×900 viewport.
- **`ui_context`** (prose): "Duolingo mobile app mid-lesson. The user has just depleted their last unit of energy…"

Cluster input sha256 `dc6d981f…` — byte-identical to the Cooper / Osterwalder / Kahneman / WCAG evals, so the six-cell Garrett grid is directly comparable to the corresponding Cooper grid.

### Skill

`skills/audit-ux-architecture/SKILL.md` (file sha256 omitted in this doc; observable via `sha256sum`), Garrett `Elements of User Experience` architecture audit with five dimensions + per-finding `product_type` / `decision_mode`. Severity anchored per ADR-008 (Nielsen 1–4 → `HeuristicViolation.severity` 3/5/7/9). Output contract enforces:

- Quotes are *not* required on every finding (same permissive stance as Osterwalder, Accessibility, and Cooper skills — a skeleton-priority finding about a modal can rest on `html` or `ui_context` alone). The parser enforces the bidirectional rule: if `"quotes"` appears in `evidence_source`, `evidence_quote_idxs` must be non-empty; if `evidence_quote_idxs` is non-empty, `"quotes"` must appear in `evidence_source`.
- `product_type` ∈ {`functional`, `informational`, `hybrid`, `not_applicable`} — closed set.
- `decision_mode` ∈ {`conscious`, `default`, `mimicry`, `fiat`, `not_applicable`} — closed set.
- `dimension_scores` exactly the five Garrett keys, each integer 1–5.
- Cross-finding cap: any `decision_mode` ∈ {`default`, `mimicry`, `fiat`} finding at severity ≥ 3 forces its dimension score ≤ 2. Every audited cell's `skeleton_wireframe = 1` is a direct consequence (sev-4 fiat finding → dim ≤ 2; additional sev-3 `conscious` finding narrows dim further to 1).
- No duplicate `(heuristic, product_type)` pair across findings.

**skill_hash** across all six cells: `9d641709d065154b1176157b77bc8eeef158eb5a8feb8db0647d4b3d4e4cfa96`. Identical across the grid — the skill file was not edited during the run.

**SKILL.md note:** The hardened bidirectional evidence rule — extracted into a dedicated bullet block with a "Practically:" example, marked parser-enforced zero-tolerance — was present in the skill on day one (ported from the lesson learned during the Cooper eval). Despite this, Sonnet 4.6 × image still violated the rule on one of its seven findings. The rule-hardening is necessary but, as this eval demonstrates, not sufficient to eliminate the failure mode for Sonnet 4.6 on multimodal input. See *Fallback analysis*.

### Runs

All six runs via `scripts/smoke_l4_ux_architecture_multimodal.py`, orchestrated by `scripts/run_l4_ux_architecture_matched.sh`. Temperature pinned to 0.0 on Opus 4.6 and Sonnet 4.6; stripped on Opus 4.7 via `claude_client._omits_sampling_params` (Opus 4.7 rejects `temperature` with 400). `max_tokens=6144`. `screenshot_media_type="image/png"` on multimodal cells. Model identifiers exactly as configured in `l4_audit_ux_architecture.py`:

- `claude-opus-4-6` (text + image)
- `claude-sonnet-4-6` (text + image)
- `claude-opus-4-7` (text + image)

Grid runtime and cost (measured):

- Input tokens text cells: 10 787 (Opus 4.6 + Sonnet 4.6) / 14 628 (Opus 4.7).
- Input tokens image cells: 12 135 / 15 976 (≈ +1 350 tokens per cell vs text — the PNG carries ~1.3k tokens of image budget).
- Output tokens: 1 781 – 2 717 per cell.
- Approximate total spend: <$1 (within the runner's $5 ceiling, well within the project budget).

## Results

### Heuristic inventory

Rows are the heuristics each cell named (`finding.heuristic` slot); columns are cells. "✓/N" = present at Nielsen severity N (max across duplicates within the cell); "—" = absent. Column for `son46.img` omitted because the cell fell back (zero findings emitted; the *intended* raw-response findings — parsed post-hoc — converge closely with `son46.txt` and are discussed under *Fallback analysis*).

| heuristic                                                 | op46.txt | op46.img | son46.txt | op47.txt | op47.img |
|---                                                        |---:|---:|---:|---:|---:|
| `strategy_contradicts_itself`                             | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/4 |
| `segment_mismatch`                                        | — | ✓/2 | — | — | — |
| `scope_creep_mid_build`                                   | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 |
| `functional_and_content_confused`                         | ✓/2 | — | — | — | — |
| `functional_and_informational_structure_fused`            | ✓/3 | ✓/2 | — | — | — |
| `interaction_model_inconsistent`                          | — | — | ✓/3 | ✓/3 | ✓/3 |
| `skeleton_does_not_honour_priority`                       | ✓/4 | ✓/4 | ✓/4 | ✓/4 | ✓/4 |
| `competing_calls_to_action`                               | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 |
| `visual_language_inherited_from_brand_without_product_fit`| ✓/2 | ✓/2 | ✓/2 | ✓/2 | ✓/2 |
| `surface_contradicts_skeleton_priority`                   | ✓/3 | ✓/3 | — | ✓/2 | ✓/3 |
| `motion_decorative_not_functional`                        | — | — | ✓/1 | — | — |

Core convergence across all 5 audited cells: `strategy_contradicts_itself`, `scope_creep_mid_build`, `skeleton_does_not_honour_priority` (always sev-4, always `fiat`), `competing_calls_to_action`, and `visual_language_inherited_from_brand_without_product_fit` (always sev-2, always `mimicry`). These five heuristics are the load-bearing Garrett signal every audited cell extracts.

Near-convergence (4/5): `surface_contradicts_skeleton_priority` — all cells except Sonnet 4.6 × text name it. Worth watching whether this drops out on Sonnet in a full-corpus run.

### Product-type inventory

Counts per cell across that cell's findings. Shape: `functional / informational / hybrid / not_applicable`.

| cell      | functional | informational | hybrid | not_applicable |
|---        |---:|---:|---:|---:|
| op46.txt  | 4 | 1 | 2 | 1 |
| op46.img  | 4 | 1 | 2 | 1 |
| son46.txt | 4 | 1 | 1 | 1 |
| op47.txt  | 3 | 0 | 2 | 2 |
| op47.img  | 4 | 1 | 1 | 1 |

All cells weight the findings toward `functional` (the product-as-tool half of Garrett's duality — the mid-lesson modal is primarily about interrupting a task flow). `informational` shows up in every audited cell except Opus 4.7 × text (where the surface finding is tagged `not_applicable` rather than `informational`). `hybrid` is emitted 1–2 times per cell on the `strategy_contradicts_itself` and `skeleton_does_not_honour_priority` findings — the two findings whose defects genuinely span the functional/informational seam.

### Decision-mode inventory

Counts per cell. Shape: `conscious / default / mimicry / fiat / not_applicable`.

| cell      | conscious | default | mimicry | fiat | not_applicable |
|---        |---:|---:|---:|---:|---:|
| op46.txt  | 6 | 0 | 1 | 1 | 0 |
| op46.img  | 6 | 0 | 1 | 1 | 0 |
| son46.txt | 4 | 1 | 1 | 1 | 0 |
| op47.txt  | 5 | 0 | 1 | 1 | 0 |
| op47.img  | 5 | 0 | 1 | 1 | 0 |

Every audited cell emits exactly one `fiat` finding (the skeleton override) and exactly one `mimicry` finding (the marketing-language surface). The `conscious` count varies with total finding count but the *unconscious* tally — {`default`, `mimicry`, `fiat`} — is stable at 2 for four cells and 3 for Sonnet 4.6 × text (its additional `motion_decorative_not_functional` tagged `default`). `not_applicable` never shows up in the decision-mode slot — every cell commits to an authorship claim on every finding.

### Per-cell summaries

**Opus 4.6 × text** — 8 findings. Clean cascade: strategy (sev-3 `contradicts_itself`) → scope (sev-3 `creep_mid_build` + sev-2 `functional_and_content_confused`) → structure (sev-3 `functional_and_informational_structure_fused`) → skeleton (sev-4 `priority` fiat + sev-3 `competing_calls_to_action`) → surface (sev-2 `visual_language_inherited` mimicry + sev-3 `surface_contradicts_skeleton_priority`). Verbose — the only cell naming `functional_and_content_confused` on the scope plane. All unconscious decisions in the canonical fiat + mimicry pair.

**Opus 4.6 × image** — 8 findings, near-identical to Opus 4.6 × text. Drops `functional_and_content_confused`, gains `segment_mismatch` on the strategy plane at sev-2. Otherwise byte-identical heuristic set and dim scores. Image modality doesn't shift Opus 4.6's core reading.

**Sonnet 4.6 × text** — 7 findings, leaner than Opus 4.6. Loses the two `fused_/confused` structural-plane heuristics in favour of a tighter `interaction_model_inconsistent` at sev-3. Loses `surface_contradicts_skeleton_priority`. Gains `motion_decorative_not_functional` at sev-1 with `decision_mode="default"` — the only `default` in the matrix. Reads the pulsing countdown as a library default (CSS keyframe on the clock element) rather than a conscious choice. Defensible; inconsistent with the other four audits' read of the same element.

**Sonnet 4.6 × image — FALLBACK.** Raw response contained 7 structurally valid findings; parser rejected on `findings[4].evidence_quote_idxs=[0]` non-empty while `"quotes"` absent from `findings[4].evidence_source`. Intended findings match `son46.txt` closely (same heuristics, same dim_scores, same 2 unconscious + 1 `default` pattern, same `motion_decorative_not_functional`); the asymmetry is on `competing_calls_to_action` where the model added a quote idx without the source tag. Full raw response persisted in the native sidecar; recoverable.

**Opus 4.7 × text** — 7 findings. Leanest read: drops the two Opus-4.6-unique `fused_/confused` heuristics, drops `motion_decorative_not_functional`, keeps the five convergent core heuristics plus `interaction_model_inconsistent` (sev-3) and `surface_contradicts_skeleton_priority` (sev-2). Dim scores: `structure_navigation=2` (lower than op46's 3) because `interaction_model_inconsistent` sits at sev-3 with `conscious` and doesn't trigger the unconscious-decision cap, yet still lowers the dim via normal rubric progression.

**Opus 4.7 × image** — 7 findings; the matrix's most severe read. `strategy_contradicts_itself` at sev-4 (not sev-3), forcing `strategy_coherence=1`. `skeleton_does_not_honour_priority` at sev-4 (fiat, as elsewhere). Two sev-4 findings in the same cell — unique. Rest of the heuristic set matches `op47.txt`. Image evidence appears to resolve a strategy ambiguity that text evidence leaves at sev-3: the multimodal view of the marketing-styled modal occupying the lesson canvas lets the model articulate the strategy contradiction as a surface-visible, not merely inferable, phenomenon.

### Dimension-score divergence

Dim-score consensus and divergence, by dimension (audited cells only):

- `strategy_coherence`: 4/5 cells score 2; Opus 4.7 × image scores **1**. Modality uplift on Opus 4.7 is the only disagreement.
- `scope_coverage`: 5/5 cells score **2**. Unanimous.
- `structure_navigation`: 3/5 cells score 3 (Opus 4.6 × both, Sonnet 4.6 × text); 2/5 cells score 2 (Opus 4.7 × both). Opus 4.7 runs the structure plane one notch lower than its siblings, driven by its consistent `interaction_model_inconsistent` finding at sev-3.
- `skeleton_wireframe`: 5/5 cells score **1**. Unanimous. Driven by the fiat cap (sev-4 fiat → dim ≤ 2) compounded by `competing_calls_to_action` sev-3.
- `surface_sensory`: 5/5 cells score **2**. Unanimous. Driven by the mimicry cap (sev-2 mimicry doesn't trigger the cap, but sev-3 surface findings + the mimicry observation consistently land the dim at 2 across all audited cells).

Three of five planes produce unanimous dim scores across all audited cells. Structure splits Opus 4.7 from the other two families; strategy splits Opus 4.7 × image from everything else. No cell disagrees on skeleton or surface or scope.

### Modality effect per model

**Opus 4.6 (text → image):** dim scores byte-identical (`{2,2,3,1,2}`). Heuristic set near-identical: +`segment_mismatch`/−`functional_and_content_confused`. No modality uplift. Opus 4.6 reads the cluster the same way from the prose alone.

**Sonnet 4.6 (text → image):** text audits cleanly; image falls back on the bidirectional evidence rule. The intended image findings (raw response) match the text findings closely, so the modality does not change the substance of the read — only the rule compliance.

**Opus 4.7 (text → image):** dim scores `{2,2,2,1,2}` → `{1,2,2,1,2}`. Strategy uplifts one notch. Heuristic set identical; only `strategy_contradicts_itself` severity moves 3 → 4. The multimodal read articulates the strategy contradiction as visually present (two strategies contending for the same pixels) rather than logically inferable.

### Convergence pattern

Five heuristics fire in every audited cell; three of those fire at matching severity. The convergence is:

- **Load-bearing (5/5, same severity):** `skeleton_does_not_honour_priority` sev-4 `fiat`, `scope_creep_mid_build` sev-3 `conscious`, `competing_calls_to_action` sev-3 `conscious`, `visual_language_inherited_from_brand_without_product_fit` sev-2 `mimicry`.
- **Load-bearing (5/5, severity varies):** `strategy_contradicts_itself` sev-3 in 4/5 cells, sev-4 in Opus 4.7 × image.
- **Near-load-bearing (4/5):** `surface_contradicts_skeleton_priority` in every cell except Sonnet 4.6 × text.

The three unanimous-at-severity heuristics cover all five Garrett planes (scope, skeleton×2, surface, strategy). The five-model agreement that the skeleton plane is where the architectural damage is concentrated, driven by a fiat-style override, is the eval's single most robust cross-family signal.

### Unconscious-decision cap verification

Every audited cell has exactly one `fiat` finding (`skeleton_does_not_honour_priority`) and exactly one `mimicry` finding (`visual_language_inherited_from_brand_without_product_fit`). Both sit at severity ≥ 2; the `fiat` one is sev-4, the `mimicry` one is sev-2. The cap rule:

- `fiat` sev-4 on `skeleton_wireframe` → forces `skeleton_wireframe ≤ 2`. Observed: all five audited cells have `skeleton_wireframe = 1`. Cap respected.
- `mimicry` sev-2 on `surface_sensory` → does **not** trigger the cap (threshold is sev ≥ 3). Observed: all five cells have `surface_sensory = 2`; the score sits at 2 because of other sev-2/3 findings on the surface plane, not because of the mimicry cap. Consistent with SKILL.md.
- Sonnet 4.6 × text has `motion_decorative_not_functional` at sev-1 with `decision_mode="default"` — below the cap threshold. Consistent with SKILL.md.

Cap rule holds in every audited cell. No off-by-one. No violations.

### `(heuristic, product_type)` uniqueness verification

Spot-checked: no audited cell emits a duplicate `(heuristic, product_type)` pair. Two cells (Opus 4.6 × both) legitimately emit two findings on the surface plane with different heuristics (`visual_language_inherited_from_brand_without_product_fit` and `surface_contradicts_skeleton_priority`), each with distinct product_type values. Uniqueness rule holds.

### Fallback analysis (Sonnet 4.6 × image)

**Parse error:** `findings[4].evidence_quote_idxs=[0] is non-empty but 'quotes' is not in evidence_source=['ui_context', 'html', 'screenshot']`.

**Offending finding (recovered from raw response):**

```json
{
  "dimension": "skeleton_wireframe",
  "heuristic": "competing_calls_to_action",
  "product_type": "functional",
  "decision_mode": "conscious",
  "severity": 3,
  "evidence_source": ["ui_context", "html", "screenshot"],
  "evidence_quote_idxs": [0],
  "violation": "The skeleton presents three competing action paths (subscribe, watch ads, lose streak) with dramatically unequal visual weight..."
}
```

The model cited `q[0]` in the body of its reasoning (the quote about the "pulsing timer, giant green button, dismiss link in grey 11px text"), added `[0]` to `evidence_quote_idxs`, but omitted `"quotes"` from `evidence_source` — evidently attributing the finding primarily to the markup/screenshot and forgetting that referencing a quote index requires the source tag.

**What this means for the skill:** the bidirectional evidence rule is already in its hardened form in Garrett's SKILL.md from day one (dedicated bulleted block, zero-tolerance label, "Practically: the moment you cite quote [i] anywhere…" example). This is the Cooper-era improvement applied proactively. And yet Sonnet 4.6 × image still violated the rule on one of seven findings. Unlike the Cooper eval — where the initial fallbacks disappeared after the SKILL.md was hardened — here the hardened rule did not prevent the violation.

**Interpretation:** the bidirectional rule's failure mode on Sonnet 4.6 × image is not a SKILL.md legibility issue; it is a model-level tendency to attribute multimodal findings to the image and markup while referencing a quote in the reasoning text, without updating the `evidence_source` list. The rule-hardening remains necessary (without it, more findings would leak through); it is simply not sufficient to eliminate the failure mode on this particular model × modality pair.

**Practical implication for future runs:** expect ≈1/7 of Sonnet 4.6 × image findings to violate the bidirectional rule on adversarial multimodal clusters. Three options:

1. Accept the parse-level fallback rate (matches this eval's 1/6 cell rate); document clearly.
2. Add a post-parse *repair* pass that, on this specific asymmetry, auto-patches `evidence_source` to include `"quotes"` when `evidence_quote_idxs` is non-empty. This would have salvaged this cell. It is not currently implemented; its cost is cross-skill (would apply to all L4 skills); it moves a parser contract from strict to forgiving.
3. Shift the default multimodal model from Sonnet 4.6 to Opus 4.7 for this skill. Opus 4.7 × image produced the most severe and most grounded read in this grid and did not violate the rule. It is more expensive.

None of these is pursued here — the fallback is documented as a data observation and the grid proceeds.

The raw response's other six findings converge with Sonnet 4.6 × text almost perfectly: identical heuristic set (both emit `motion_decorative_not_functional` at sev-1 with `decision_mode="default"`), identical dim_scores (`{2,2,3,1,2}`). Modality did not change Sonnet 4.6's substantive reading; only its rule compliance.

### Contract artefacts

- **skill_hash** (identical across all six cells): `9d641709d065154b1176157b77bc8eeef158eb5a8feb8db0647d4b3d4e4cfa96`
- **Input cluster file sha256:** `dc6d981f1652884e0088d9299311230d183f9d7cb71c78d4729b1eec5068b961` (byte-identical to Cooper / Osterwalder / Kahneman / WCAG L4 evals)
- **Screenshot sha256:** `bcad10de3d0351be345a479c1370353237afe554feb2382576dba39aec415d16` (PNG, 119 630 bytes)
- **HTML fixture sha256:** `cdfcbd477646c72b3aeccc45d7089bed19c187f36503003ae30925ddd1ff59ba` (5 677 bytes)

All six `.provenance.json` sidecars carry the shared `skill_hash`, distinct `modality` tag, and the Garrett-specific histograms (`product_type_histogram`, `decision_mode_histogram`, `unconscious_decision_findings` gauge).

## Caveats

- **One cluster.** The whole eval is on `cluster_02` alone. Findings that look model-family-specific here may be artefacts of this one cluster's shape; full-corpus L4 runs may redistribute the convergence.
- **Temperature pinned to 0.0** on Opus 4.6 and Sonnet 4.6. Sonnet is deterministic at temperature 0 — the fallback above is *not* a sampling artefact. Opus 4.7 strips temperature (API-level rejection); its outputs are not strictly deterministic, though in practice the eval's runs were reproducible to the heuristic set.
- **`product_type` variance is within tolerance.** The eval observed inter-cell variance on `product_type` for the load-bearing heuristics (`strategy_contradicts_itself`, `skeleton_does_not_honour_priority`). SKILL.md explicitly allows both single-type and `hybrid` tags for findings on the functional/informational seam; the variance is the skill's tolerated discretion, not a defect. L5 aggregation that needs a stable key across models should prefer `(heuristic, severity)` over `(heuristic, product_type)`.
- **`decision_mode` claims are authorship inferences.** The audit does not have ground-truth on whether Duolingo's designers made a conscious, default, mimicry, or fiat decision on any given surface element. The claim is that the evidence *looks like* one of those modes. The skill's SKILL.md requires a grounded indicator in the violation text for `default`/`mimicry`/`fiat` claims; reviewer should verify each such claim has one.
- **Fallback rate ≠ accuracy rate.** The 5/6 audited rate measures parse-level compliance with the skill's output contract, not the quality of the emitted findings. A cell that audits cleanly but emits weak findings is no better than a cell that falls back on a clean set.
- **The sixth cell's fallback is itself data.** It tells us that the hardened bidirectional rule is necessary-but-not-sufficient for Sonnet 4.6 × image on architectural-audit prompts. Reruns at temperature 0 will reproduce it; a repair pass or a model swap is required to eliminate it.

## Reproducing

```
# Produce the six cells (requires ANTHROPIC_API_KEY):
bash scripts/run_l4_ux_architecture_matched.sh

# Force-rerun including already-audited cells:
bash scripts/run_l4_ux_architecture_matched.sh --all

# Run a single cell by hand:
uv run python scripts/smoke_l4_ux_architecture_multimodal.py \
    --model claude-opus-4-7 --modality image

# Inspect provenance:
ls -1 data/derived/l4_audit/audit_ux_architecture/*.provenance.json

# Inspect raw responses (including the fallback's intended findings):
cat data/derived/l4_audit/audit_ux_architecture/*.native.jsonl | jq .

# Tests (unit-only, in-process fake client):
uv run pytest tests/test_l4_audit_ux_architecture.py -v
```

Module under test: `src/auditable_design/layers/l4_audit_ux_architecture.py`. System prompt: `skills/audit-ux-architecture/SKILL.md` (skill_hash `9d641709…`). The runner skips already-audited cells by default and reruns fallback cells automatically — see the runner's skip logic. Expected total cost is well under $1 at current list prices.
