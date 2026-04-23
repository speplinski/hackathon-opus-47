# L4 audit — `audit-accessibility` skill, 3-model × 2-modality matched comparison

**Date:** 2026-04-23
**Related:** ADR-008 (audit severity anchors), ADR-011 (replay log contract), `ARCHITECTURE.md` §4.5 (L4 layer), `docs/evals/l4_audit_usability_fundamentals_three_way.md` (sister Norman-skill smoke), `skills/audit-accessibility/SKILL.md`, `src/auditable_design/layers/l4_audit_accessibility.py`, `scripts/smoke_l4_accessibility_multimodal.py`, `scripts/run_l4_accessibility_matched.sh`
**Status:** Empirical record. Thin-spine smoke on one cluster (`cluster_01 "Voice recognition marks correct answers wrong"`) across six cells — {Opus 4.6, Sonnet 4.6, Opus 4.7} × {text-only, multimodal}. Purpose is to characterise the WCAG POUR + Inclusive skill's cross-model and cross-modality behaviour before a full-corpus run.

## Purpose

L4's `audit-accessibility` skill adds WCAG 2.2 success-criterion anchors (POUR) and a fifth Inclusive Design dimension to the Norman-skill contract. Unlike the Norman skill, this audit is expected to pull evidence from layout and code — the bulk of WCAG A/AA criteria are observable in HTML/CSS or in a rendered screenshot, not in user quotes. The matched eval therefore has to answer one question the Norman smoke did not:

- **Does sending a PNG screenshot alongside the HTML change what the audit surfaces, and if so, in which direction?**

Secondary questions carried over from the Norman smoke:

- Is the strict output contract reproducible across Opus 4.6 / Sonnet 4.6 / Opus 4.7?
- Do the three models agree on severity calibration, dimension scores, and the set of criteria worth flagging?
- Where a model is out-of-contract (e.g. cites evidence the input didn't contain), does the behaviour persist across modalities?

## Executive summary

| | Opus 4.6 text | Opus 4.6 image | Sonnet 4.6 text | Sonnet 4.6 image | Opus 4.7 text | Opus 4.7 image |
|---|---|---|---|---|---|---|
| Clusters audited | 1 | 1 | 1 | 1 | 1 | 1 |
| Fallback count | 0 | 0 | 0 | 0 | 0 | 0 |
| Findings emitted | **9** | 8 | 6 | 7 | 7 | 7 |
| WCAG A | 1 | 0 | 0 | 0 | 1 | 1 |
| WCAG AA | 6 | 6 | 5 | 6 | 4 | 4 |
| WCAG AAA | 0 | 0 | 0 | 0 | 0 | 0 |
| Inclusive | 2 | 2 | 1 | 1 | 2 | 2 |
| Nielsen-4 findings | 2 | 0 | 2 | 1 | 0 | 0 |
| Nielsen-3 findings | 4 | 6 | 3 | 4 | 5 | 4 |
| Nielsen-2 findings | 3 | 2 | 1 | 2 | 2 | 3 |
| perceivable score | 2 | 2 | 2 | 1 | 2 | 2 |
| operable score | 2 | 2 | 2 | 2 | 3 | 2 |
| understandable score | 2 | 3 | 3 | 3 | 4 | 3 |
| robust score | 4 | 4 | **2** | **2** | 3 | 4 |
| inclusive_cognitive score | **1** | 2 | 2 | 2 | 2 | 2 |
| Input tokens | 8779 | 10209 | 8779 | 10209 | 8779 | 10209 |
| Output tokens | 1861 | 1725 | 1451 | 1643 | ≈1700¹ | 1643 |

¹ Opus 4.7 text provenance pre-dates the `input_tokens`/`output_tokens` fields; value inferred from the native payload size.

Zero fallback and zero transport failure across all six live calls — the strict output contract held on every model × modality cell once the parser's unescaped-`"` repair was in place (see Caveats). All six verdicts share the same `verdict_id` (`audit-accessibility__cluster_01`), the same `skill_hash` (`cb3598db…`), and the same input sha256 (`80bdf88b…`); they disagree only on finding content.

Three load-bearing observations:

1. **Modality is additive, not replacing.** Multimodal cells do not strictly dominate text cells on finding count: Opus 4.6 goes 9 → 8, Sonnet 4.6 goes 6 → 7, Opus 4.7 stays 7 → 7. What changes is *which* findings appear. Image unlocks target-size (`2.5.8`) on cells that didn't previously have it, and in one case non-text contrast (`1.4.11` on Sonnet 4.6 image); text-only retains findings with `evidence_source: ["html"]` that the image cell sometimes drops. Net effect on severity scores is small — on this cluster image does not reveal a new high-severity AA violation that text missed.

2. **Severity calibration disagreement on robust/`4.1.3`.** Every cell except Opus 4.7 image surfaces the "status message not announced" AA failure (`4.1.3`). The Opus family rates it severity 2 (→ `robust=4`); Sonnet 4.6 rates it severity 3–4 (→ `robust=2`). This is a 2-point dimension-score swing driven by one heuristic, and it is *the* single largest cross-model disagreement in the data.

3. **Opus 4.7 × text is spec-leaky about evidence_source.** Three of the seven Opus 4.7 text findings cite `evidence_source: ["html", "screenshot"]` even though the text-only run sent no image. The contract does not currently police evidence-source against what was actually supplied; the multimodal run corrects itself, but the text run is making a claim it has no way to substantiate. Not a schema violation, a faithfulness violation.

## Methodology

### Input

One enriched cluster from the L3b matched-corpus output, extended with HTML + screenshot + `ui_context` per ADR/concept §7:

| | sha256 |
|---|---|
| `data/derived/l4_audit/audit_accessibility/audit_accessibility_input.jsonl` | `80bdf88b8f3d7feecaa1294395dccdcc4f72c07645dd7a11cb642abc63b3d972` |

Cluster shape: `cluster_01` labelled `"Voice recognition marks correct answers wrong"`, 5 representative quotes:

- `q[0]`: "is incorrect"
- `q[1]`: "I am speaking but it says wrong"
- `q[2]`: "I keep getting it wrong"
- `q[3]`: "give me wrong answers"
- `q[4]`: "always wrong and give you the wrong words"

Attached adversarial artefacts (deliberately constructed to exercise WCAG violations):

- **HTML** (1117 bytes): Duolingo speaking-exercise approximation with `outline: none` on the mic button, `color: #9ca3af` on the REPORT link, `color: #d1d5db` on the "CLICK TO SPEAK" label, no `role="alert"` on the feedback banner, no `aria-live` region for the wrong-verdict announcement, mic button using active-state colours indistinguishable from disabled-state greys.
- **Screenshot** (`data/artifacts/ui/duolingo_speak_wrong_verdict.png`, 47050 bytes PNG): rendered form of the same HTML in light theme with the wrong-verdict feedback banner visible.
- **`ui_context`** (200+ char prose): "Duolingo speaking exercise immediately after the speech recogniser returned a wrong verdict. Desktop, light theme…"

### Skill

`skills/audit-accessibility/SKILL.md`, WCAG 2.2 POUR four-dimension audit + fifth Inclusive Design dimension (`inclusive_cognitive`). Per-finding severity on Nielsen 1–4; per-dimension score 1–5. Output contract enforces the bidirectional coupling `"quotes" in evidence_source ↔ evidence_quote_idxs non-empty` — same hash in all six cells:

Skill hash: `cb3598db9248efc22e6543a6f0c0a315aa2a067dec859d0b7e6d4be3b2c21829`.

### Runs

All six runs via `scripts/smoke_l4_accessibility_multimodal.py`, orchestrated by `scripts/run_l4_accessibility_matched.sh`. Temperature pinned to 0.0 on Sonnet 4.6 and Opus 4.6; stripped entirely on Opus 4.7 via `claude_client._omits_sampling_params` (Opus 4.7 rejects `temperature` with 400). `max_tokens=6144` across the board. `screenshot_media_type="image/png"` on multimodal cells.

| cell | verdicts sha256 | native sha256 |
|---|---|---|
| opus46 × text | `6d88312b9245a9fb…` | `7abf4ccde60b7f80…` |
| opus46 × image | `556a0e1510c810fd…` | `2bda687cf05bd823…` |
| sonnet46 × text | `68f086d08f688649…` | `c6a2b5c67dcef730…` |
| sonnet46 × image | `b1027e851efa7477…` | `0202525206d3128c…` |
| opus47 × text | `b142bc0c09b8b52b…` | `fb359db1ff39f0cf…` |
| opus47 × image | `bec3b43eba307732…` | `dad0e2aed8c5a513…` |

Outputs at `data/derived/l4_audit/audit_accessibility/l4_verdicts_audit_accessibility_cluster01_{opus46,opus46_multimodal,sonnet46,sonnet46_multimodal,opus47,opus47_multimodal}.{jsonl,native.jsonl,provenance.json}`.

## Results

### Finding inventory across all six cells

Rows are WCAG refs / heuristic families, columns are cells. "✓" = present, "—" = absent, `/N` = Nielsen severity.

| WCAG ref | family | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|---|
| `1.4.3` | text contrast (AA) | ✓/3 ×2 | ✓/3 ×2 | ✓/3 ×2 | ✓/4 | ✓/3 | ✓/3 |
| `1.4.1` | colour-only signal (A) | — | — | — | — | ✓/2 | ✓/2 |
| `1.4.11` | non-text contrast (AA) | — | — | — | ✓/3 | — | — |
| `2.4.7` | focus indicator (AA) | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 |
| `2.5.8` | target size (AA) | ✓/2 | ✓/3 | — | ✓/2 | — | ✓/3 |
| `3.3.1` | error identification (A) | ✓/2 | — | — | — | — | — |
| `3.3.3` | error suggestion (AA) | ✓/3 | ✓/2 | ✓/2 | ✓/2 | — | ✓/2 |
| `4.1.3` | status message (AA) | ✓/2 | ✓/2 | ✓/4 | ✓/3 | ✓/3 | **—** |
| `—` (inclusive) | no retry / recovery path | ✓/4 | ✓/3 | ✓/4 | ✓/3 | ✓/3 | ✓/3 |
| `—` (inclusive) | learned helplessness | ✓/4 | ✓/3 | — | — | — | ✓/3 |
| `—` (inclusive) | comparable-experience gap | — | — | — | — | ✓/2 | — |
| `—` (inclusive) | situational inclusion gap | — | ✓/3 | — | — | — | — |

Core convergence across all 6 cells: `1.4.3` text contrast, `2.4.7` focus indicator, and at least one inclusive "no retry / recovery" finding. These three are the load-bearing A/AA + Inclusive signal the skill extracts from this cluster regardless of model or modality — and they are exactly the three failures a human review would flag first on this HTML.

### Per-cell summary prose (native payload, verbatim first sentence)

**Opus 4.6 text.** "The speech-recognition exercise has clear WCAG 2.2 AA failures in both layout (focus suppression on the primary mic button, sub-4.5:1 text contrast on the recording label, target-size violation on the REPORT affordance) and semantics (no live region or status role on the verdict banner, unhelpful error text), compounded by an inclusive-cognitive failure where the only user-facing recovery is 'Let's move on from this one for now' — stripping users with speech differences of any path to challenge, retry, or understand the misrecognition."

**Opus 4.6 image.** "The speaking-exercise screen exhibits several Level-AA failures visible in the screenshot (sub-threshold contrast on the 'CLICK TO SPEAK' and 'REPORT' labels, missing focus ring, under-size REPORT target), semantic gaps in the wrong-verdict feedback (no live-region announcement, thin error suggestion), and — most consequentially — an inclusive failure in which users with atypical speech have no alternative input path and the only recovery affordance is a demotion to 'skip'."

**Sonnet 4.6 text.** "The speaking exercise has three A/AA violations observable in markup: the mic button suppresses its focus indicator (2.4.7 AA), the REPORT button fails contrast at ~2.4:1 (1.4.3 AA), and the feedback banner contains no status-message role or live region so assistive-technology users receive no announcement of the wrong-verdict result (4.1.3 AA). An inclusive finding captures the deeper problem: when the speech recogniser returns a wrong verdict the only forward path is 'Let's move on from this one for now'…"

**Sonnet 4.6 image.** "The speaking exercise presents multiple AA failures visible in the screenshot: the mic button's 'CLICK TO SPEAK' label at #d1d5db-on-white fails 1.4.3 severely (~1.6:1), the REPORT link fails non-text contrast against the surrounding white (1.4.11), the mic button has no visible focus indicator (2.4.7), the REPORT control is under the 24×24 CSS px target-size minimum (2.5.8), and the wrong-verdict feedback has no live-region announcement (4.1.3)…"

**Opus 4.7 text.** "The speaking exercise has markup-observable A/AA accessibility failures in the mic-button control (focus outline stripped, colour-only affordance-state signalling) and in the wrong-verdict feedback (no live region / status role, sub-threshold text contrast on the REPORT link) together with an Inclusive-Cognitive failure where users have no user-facing path to contest or retry the recogniser's verdict…"

**Opus 4.7 image.** "The speaking exercise has multiple observed AA failures: the 'CLICK TO SPEAK' label at #d1d5db on white (~1.6:1) and the REPORT control at #9ca3af on white (~2.8:1) both fail 1.4.3, and the mic button strips its focus outline with no replacement (2.4.7). Quotes cluster around a speech-recogniser verdict users dispute, and the UI offers no retry or alternative input — a situational/control gap for users with atypical speech, background noise, or no mic — but the WCAG-level claim here is that the recovery affordance (REPORT) is itself visually de-emphasised below threshold."

### Dimension-score divergence

| dim | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img | range |
|---|---|---|---|---|---|---|---|
| perceivable | 2 | 2 | 2 | **1** | 2 | 2 | 1 |
| operable | 2 | 2 | 2 | 2 | **3** | 2 | 1 |
| understandable | 2 | 3 | 3 | 3 | **4** | 3 | 2 |
| robust | 4 | 4 | **2** | **2** | 3 | 4 | 2 |
| inclusive_cognitive | **1** | 2 | 2 | 2 | 2 | 2 | 1 |

Two scores stand out:

- **Sonnet 4.6 scores `robust` at 2** on both modalities; Opus family scores it 3–4. All cells surface `4.1.3 status_message_not_announced` (except Opus 4.7 image, which drops it); the same heuristic is Nielsen-2 on the Opus family and Nielsen-3 to 4 on Sonnet. Sonnet reads a missing live region as a higher-stakes failure than Opus does. Both are defensible — the SKILL.md rubric has "1–2 A/AA violations → 4" and "3+ AA or 1 serious → 2", and `4.1.3` on the recovery flow of the only-available input channel is arguably "serious".
- **Opus 4.6 text scores `inclusive_cognitive` at 1** — the worst possible score in the rubric. This is anchored to two Nielsen-4 inclusive findings (`no_alternative_input_path` at sev=4, `learned_helplessness` at sev=4). Every other cell has at most one Nielsen-4 inclusive finding, so the double-Nielsen-4 uniquely produces the 1. This is the strongest single signal in the matrix — Opus 4.6 text is the only cell that "gets the severity of the inclusive failure right" relative to the cluster's theme (speech recognition systematically wrong for users with atypical speech).

### Modality effect, per-model

**Opus 4.6 (9 → 8).** Image drops the text-only `3.3.1 error_identification` (A) finding and one of the two `1.4.3` entries (absorbed into a single finding with `evidence_source: ["html","screenshot"]`). Bumps `2.5.8 target_size_minimum` from Nielsen-2 to Nielsen-3 — visible under-sizing of REPORT in the rendered screenshot warrants the higher severity. Net: less markup-niche reading, more pixel-grounded severity.

**Sonnet 4.6 (6 → 7).** Image adds two findings not present in text-only: `1.4.11 non-text contrast` (REPORT button non-text contrast against its white background, genuinely only visible in the render) and `2.5.8 target_size_minimum`. Image drops nothing. Net: image strictly enriches Sonnet 4.6's coverage.

**Opus 4.7 (7 → 7).** Image drops `4.1.3 status_message_not_announced` — the only cell in the matrix that fails to surface this AA criterion — and adds `2.5.8 target_size_minimum` and `3.3.3 error_suggestion`. Image removes Opus 4.7 text's "screenshot" evidence-source fabrication (3/7 findings in text cite "screenshot" with no image supplied; image cell cites "screenshot" only where appropriate).

### Convergence pattern

Criteria surfaced by all 6 cells (load-bearing convergence):

- `1.4.3 insufficient_text_contrast` (AA)
- `2.4.7 missing_focus_indicator` (AA)
- at least one inclusive "no retry / no alternative path / learned helplessness" family finding

Criteria surfaced by ≥5 of 6 cells:

- `3.3.3 unhelpful_error_suggestion` (AA) — 5/6 (missing only on Opus 4.7 text)
- `4.1.3 status_message_not_announced` (AA) — 5/6 (missing only on Opus 4.7 image)

Criteria surfaced by exactly one cell (unique findings):

- `1.4.11 non-text contrast` — Sonnet 4.6 image only
- `3.3.1 error_identification` (A) — Opus 4.6 text only
- `situational_inclusion_gap` (inclusive) — Opus 4.6 image only
- `comparable_experience_gap_speech` (inclusive) — Opus 4.7 text only

`1.4.1 colour_only_signal` (A) is surfaced only by Opus 4.7, in both modalities — a family-specific reading rather than a single-cell artefact.

### The out-of-contract behaviours

Two behaviours break the skill's implicit honesty contract without breaking the validator:

**Opus 4.7 text cites screenshot evidence with no screenshot supplied.** The text-only run's findings `insufficient_text_contrast`, `colour_only_signal`, and `disabled_appearance_confusion` all carry `evidence_source: ["html", "screenshot"]`. No image was sent — `screenshot_bytes=null` in provenance. The model is appropriating an evidence type that doesn't apply. SKILL.md permits `["html", "screenshot"]` as a valid combination but doesn't explicitly say "don't cite screenshot when no screenshot was supplied"; this is a prompt-level invariant missing from the current skill, not a parser gap. The multimodal run on the same model does not repeat this error.

**Opus 4.7 text files `1.4.11 disabled_appearance_confusion` under `operable`.** WCAG 2.2's `1.4.11 Non-text Contrast` is a Perceivable criterion. This is a dimension-mis-classification that the parser does not currently detect, because it accepts any combination of `(dimension ∈ {perceivable,operable,understandable,robust,inclusive_cognitive}, wcag_ref)`. Cross-validating dimension against WCAG's official POUR bucket would have caught this. Deferred: noting as a concrete reason to add that validation before the full-corpus run, not fixing here.

Neither behaviour changes the audit's substantive conclusions — the contrast finding is real, the disabled-appearance finding is real, only their surface classification slips.

### Contract artefacts — what got written

All six cells produced the three-file set:

- `*.jsonl` — one `AuditVerdict` row, Pydantic-validated
- `*.native.jsonl` — full native Claude payload keyed on `verdict_id`
- `*.provenance.json` — summary with `dimension_score_totals`, `nielsen_severity_histogram`, `wcag_level_histogram`, `wcag_ref_counts`, `input_tokens`, `output_tokens`, `modality`, `mode`, `screenshot_bytes`, `screenshot_media_type`, `skill_hash`, `skill_id`, `model`, `temperature`, `max_tokens`, `fallback_count`, `fallback_reasons`, `transport_failure_count`.

**Meta-sidecar coverage is incomplete.** Only the two single-modality cells that were originally run through the module-proper path (`opus47 × text`, `sonnet46 × text`) have `.meta.json` files; the smoke script doesn't write ADR-011 sidecars, and the four multimodal + two opus46 cells have none. The `sonnet46 × text` meta.json is also stale: its `written_at` records the original fallback run and its `skill_hashes` pin to a pre-bidirectional-coupling SKILL.md (`f1039b7f…`), whereas the current verdict was produced against `cb3598db…`. The verdicts/native/provenance trio is consistent; the meta-sidecar chain is not. Tracking this as a known hygiene gap for the matched-eval smoke path — the production `l4_audit_accessibility` module does write meta sidecars correctly, the smoke script does not.

## Caveats

- **One cluster, one HTML input, one screenshot.** The six-cell matrix measures behaviour on a single adversarially-constructed input that has strong markup-level violations plus a strong inclusive-cognitive theme in the quotes. Generalisation to the full corpus has to come from a full-corpus run (deferred).
- **The input is engineered to trigger multiple criteria.** The HTML was authored with explicit `outline: none`, disabled-state greys on live controls, and a non-announced feedback banner — all things the skill is known to surface. The eval is a faithfulness test ("do the models report what's there") not a discovery test ("do the models find issues that weren't put there for them").
- **Parser repair was required to get Sonnet 4.6 text to parse.** Sonnet 4.6 on the text-only run emits JSON with unescaped `"` inside string values (e.g. `"...(e.g. 'We heard: "Hay panes" — try speaking..."`). The original parser failed on this with `Expecting ',' delimiter`; `_repair_unescaped_string_quotes` in `l4_audit_accessibility.py` now iteratively escapes the stray quotes and retries. The first two invocations of this cell produced fallbacks; the third, after the parser fix, parsed 6 findings cleanly. Sonnet 4.6 multimodal did not exhibit the same issue on this cluster, but the defect is a known Sonnet-family tendency we should assume surfaces again on the full corpus.
- **Opus 4.7 text "screenshot" evidence-source claim is unverifiable.** Three of Opus 4.7 text's seven findings cite `["html", "screenshot"]` with no image supplied. The contract doesn't currently police this. Findings are still structurally valid and substantively correct; flagging the citation as fabrication rather than treating the findings as invalid.
- **Opus 4.7 image drops `4.1.3`.** Every other cell surfaces "status message not announced" on the wrong-verdict banner. Opus 4.7 image does not. `robust=4` is still assigned despite the finding's absence — inconsistent with the rubric's "no findings → 5" but one point off, not two.
- **Cost tracker 3× Opus overestimate still applies.** Tracker-reported Opus 4.6 and 4.7 spend is ~3× actual (`$15/$75` per MTok hardcoded vs actual `$5/$25`). Sonnet 4.6 tracker is 1:1 correct. The total live cost for this six-cell matched run was ~$0.30 real / ~$0.70 tracker.
- **Meta-sidecar chain is partial.** See "Contract artefacts" above. The verdict/native/provenance chain is complete and consistent; the `.jsonl.meta.json` sidecars are missing on four of six cells and stale on one of the remaining two. Fixing this is a smoke-script change, not a skill or module change.

## Reproducing this document

L4 is a Claude API layer; for this matched eval the smoke script bypasses the replay cache and always hits live (`mode: {text,image}_direct_sdk` in provenance). To reproduce byte-identically, the inputs to pin are:

- `data/derived/l4_audit/audit_accessibility/audit_accessibility_input.jsonl` → sha256 `80bdf88b…`
- `skills/audit-accessibility/SKILL.md` → `skill_hash` `cb3598db…`
- `data/artifacts/ui/duolingo_speak_wrong_verdict.png` → 47050 bytes, `image/png`

Regenerate live (not replay — smoke is live-only):

```bash
bash scripts/run_l4_accessibility_matched.sh --all
```

The `--all` flag forces re-runs of every cell, including the four cached successful cells. Without it, the script's skip-if-success logic checks each cell's provenance `audited_count` and only re-runs cells that were fallbacks (or are missing entirely).

Per-cell (for one-off re-runs):

```bash
uv run python scripts/smoke_l4_accessibility_multimodal.py \
  --model claude-opus-4-6 --modality text
uv run python scripts/smoke_l4_accessibility_multimodal.py \
  --model claude-opus-4-6 --modality image
uv run python scripts/smoke_l4_accessibility_multimodal.py \
  --model claude-sonnet-4-6 --modality text
uv run python scripts/smoke_l4_accessibility_multimodal.py \
  --model claude-sonnet-4-6 --modality image
uv run python scripts/smoke_l4_accessibility_multimodal.py \
  --model claude-opus-4-7 --modality text
uv run python scripts/smoke_l4_accessibility_multimodal.py \
  --model claude-opus-4-7 --modality image
```
