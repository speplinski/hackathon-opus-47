# L4 audit — `audit-decision-psychology` skill, 3-model × 2-modality matched comparison

**Date:** 2026-04-23
**Related:** ADR-008 (audit severity anchors), ADR-011 (replay log contract), `ARCHITECTURE.md` §4.5 (L4 layer), `docs/evals/l4_audit_accessibility_matched.md` (sister WCAG-skill eval), `docs/evals/l4_audit_usability_fundamentals_three_way.md` (sister Norman-skill smoke), `skills/audit-decision-psychology/SKILL.md`, `src/auditable_design/layers/l4_audit_decision_psychology.py`, `scripts/smoke_l4_decision_psychology_multimodal.py`, `scripts/run_l4_decision_psychology_matched.sh`
**Status:** Empirical record. Thin-spine smoke on one cluster (`cluster_02 "Streak loss framing pressures users into mid-session purchase"`) across six cells — {Opus 4.6, Sonnet 4.6, Opus 4.7} × {text-only, multimodal}. Purpose is to characterise the Kahneman skill's cross-model and cross-modality behaviour on an adversarial dark-pattern stack before a full-corpus run.

## Purpose

L4's `audit-decision-psychology` skill replaces Norman's "design of everyday things" lens with Kahneman's dual-process architecture: four dimensions (`choice_architecture`, `cognitive_load_ease`, `judgment_heuristics`, `temporal_experience`), per-finding `intent` tag (`dark_pattern | nudge | unintentional | absent`), a Kahneman-vocabulary `mechanism` field (loss aversion, anchoring, endowment effect, WYSIATI, ego depletion, …), and a dark-pattern discipline rule that caps the dimension score at 2 when the cell contains any sev ≥ 3 dark-pattern finding.

The matched eval therefore has to answer three questions the accessibility smoke did not:

- **Do the three models converge on *which* Kahneman mechanisms are operating, or does each family project its own vocabulary onto the same stimulus?**
- **Does attaching a PNG change what `intent` gets assigned?** Loss aversion is textually obvious in the quotes; visual-weight asymmetry between the "Keep my streak" CTA and the tiny "lose streak" dismiss is only observable in the render.
- **Does the dark-pattern dimension cap hold empirically?** The module enforces it in the parser; this eval verifies the models produce outputs that land inside the cap rather than trigger a fallback on the discipline rule.

## Executive summary

| | Opus 4.6 text | Opus 4.6 image | Sonnet 4.6 text | Sonnet 4.6 image | Opus 4.7 text | Opus 4.7 image |
|---|---|---|---|---|---|---|
| Clusters audited | 1 | 1 | 1 | 1 | 1 | 1 |
| Fallback count | 0 | 0 | 0 | 0 | 0 | 0 |
| Findings emitted | **9** | 8 | 7 | 7 | 8 | 8 |
| `dark_pattern` intent | 7 | 7 | 6 | 6 | 6 | 6 |
| `unintentional` intent | 2 | 1 | 1 | 1 | 2 | 1 |
| `absent` intent | 0 | 0 | 0 | 0 | 0 | **1** |
| `nudge` intent | 0 | 0 | 0 | 0 | 0 | 0 |
| Nielsen-4 findings | 2 | 1 | 2 | 3 | 2 | 2 |
| Nielsen-3 findings | 7 | 6 | 5 | 4 | 6 | 4 |
| Nielsen-2 findings | 0 | 1 | 0 | 0 | 0 | 2 |
| `choice_architecture` score | **1** | **1** | **1** | **1** | **1** | **1** |
| `cognitive_load_ease` score | 2 | 2 | 1 | 1 | 2 | 2 |
| `judgment_heuristics` score | 2 | 2 | 2 | 2 | 2 | 2 |
| `temporal_experience` score | 2 | 2 | 2 | 2 | 2 | 2 |
| Input tokens | 11606 | 12954 | 11606 | 12954 | 15707 | 17055 |
| Output tokens | 2128 | 1868 | 1784 | 1692 | 2457 | 2653 |

Zero fallback and zero transport failure across all six live calls on the retained-results matrix — though see "The Opus 4.7 × image non-determinism" below: the first attempt on that cell fell back on a one-field structural miss (`findings[1] missing keys: ['recommendation']`), a deterministic rerun produced a clean audit with 8 substantively different findings. All six verdicts share the same `verdict_id` (`audit-decision-psychology__cluster_02`), the same `skill_hash` (`6ff2b137…`), and the same input sha256 (`dc6d981f…`); they disagree only on finding content.

Four load-bearing observations:

1. **All six cells converge on `choice_architecture = 1`** — the worst possible score in the rubric, triggered by the dark-pattern cap rule (any `dark_pattern` finding with sev ≥ 3 → dimension capped at 2, and with 4–6 sev-3+ dark-pattern findings hitting `choice_architecture` the rubric floors to 1). The three model families independently agree this is a choice-architecture catastrophe; the reading is robust to both modality and the Opus 4.7 cross-run variance.

2. **Loss aversion and anchoring converge across all 6 cells — every other mechanism is family-flavoured.** `loss aversion` (sev-4, on `You'll lose your 5-day streak at midnight`) and `anchoring & adjustment` (sev-2 to sev-3, on the `$6.99/mo → $3.49` strike-through) are surfaced by every cell. Beyond those two, each family has distinct mechanism fingerprints: Opus 4.6 text uniquely reaches for `cognitive strain` and `duration neglect`; Sonnet scores `ego depletion` at sev-4 where Opus families score it at sev-3; Opus 4.7 × image uniquely names `System 1 / System 2` as a mechanism (the framework itself, not a specific mechanism within it — see caveats). Concept-coverage is broadly identical, taxonomic labelling is not.

3. **Modality flips the `WYSIATI` severity on Opus 4.6 and Sonnet 4.6, in opposite directions.** Opus 4.6 text reads the hidden-option structure (`Streak freezes unavailable at your level` footer) as Nielsen-4 `wysiati_hidden_option`; the image run downrates it to Nielsen-2. Sonnet 4.6 does the exact reverse — text cell sev-3, image cell sev-4. Same stimulus, opposite modality gradients. This is the single most interesting cross-cell behaviour in the matrix.

4. **Opus 4.7 × image breaks the `intent` near-monoculture with the matrix's single `intent=absent` finding.** Every other cell files findings only as `dark_pattern` or `unintentional`. Opus 4.7 × image's second-run payload includes a `base_rate_neglect_streak_loss` finding at `intent=absent, severity=2` — effectively a half-negative finding observing that the skill's concept of "base-rate neglect" isn't really what's happening here; the skill framework nudged a finding slot for something the model ultimately rated as not-present. This is the one cell where the model actively declines to attribute harm.

## Methodology

### Input

One enriched cluster from the L3b matched-corpus output, extended with HTML + screenshot + `ui_context` per concept §7:

| | sha256 |
|---|---|
| `data/derived/l4_audit/audit_decision_psychology/audit_decision_psychology_input.jsonl` | `dc6d981f1652884e0088d9299311230d183f9d7cb71c78d4729b1eec5068b961` |

Cluster shape: `cluster_02`, label `"Streak loss framing pressures users into mid-session purchase"` (Kahneman-flavoured re-label of L3b's `"Streak tracking not maintained"` cluster). Five representative quotes drawn from the cluster's seven member reviews:

- `q[0]`: "If you don't agree to pay mid-lesson, and you haven't watched ads FIRST, you have to quit mid-lesson"
- `q[1]`: "I'm trying to keep my 800+ day streak, but the recent changes are abysmal"
- `q[2]`: "the new update implemented an energy system instead of the hearts, which ruined my experience by forcing me to pay or watch ads"
- `q[3]`: "I was in holiday so i logged out but when i came home then i logged in but still my streak was fall into 0 days"
- `q[4]`: "I would have to do the same lesson multiple times just to keep my daily streak"

Attached adversarial artefacts (deliberately constructed to stack Kahneman dark-pattern mechanisms):

- **HTML** (`data/artifacts/ui/duolingo_streak_modal.html`, sha256 `cdfcbd47…`, 5677 bytes): a "STREAK AT RISK" modal with pulsing countdown timer (`Offer ends in 2:43`), 84-equivalent-px SVG flame, red `#ff4b4b` streak counter `5`, loss-framing banner (`You'll lose your 5-day streak at midnight. All progress resets to 0.`), anchored price row (`$6.99/mo` struck-through → `$3.49` new), full-width green `Keep my streak` CTA with "Subscribe & save your progress" subtitle, secondary blue "Watch 3 ads to save streak" link, and a deliberately de-emphasised 11 px grey-on-white underlined "lose streak" dismiss at the bottom.
- **Screenshot** (`data/artifacts/ui/duolingo_streak_modal.png`, sha256 `bcad10de…`, 119630 bytes PNG): element-screenshot of the `.phone` container rendered via playwright chromium headless at `device_scale_factor=2`, 428×900 viewport.
- **`ui_context`** (prose): "Duolingo mobile app mid-lesson. The user has just depleted their last unit of energy after answering a question and a blocking modal has appeared. The user cannot continue the lesson without one of the three displayed actions…"

### Skill

`skills/audit-decision-psychology/SKILL.md` (file sha256 `e9d8a05c164bce9802b6bc372b0393f604d86c7341e4747f066cbeabe48b4f50`), Kahneman dual-process audit with four dimensions and per-finding `intent` + `mechanism`. Severity anchored per ADR-008 (Nielsen 1–4 → dimension floor 3/5/7/9 reversed). Output contract enforces:

- Quotes-always-required in `evidence_source` (differs from accessibility which permits pure-markup observations).
- Per-finding dark-pattern floor: `intent=dark_pattern` → `severity ≥ 2`.
- Cross-finding dimension cap: any sev ≥ 3 dark-pattern finding in a dimension → dimension score ≤ 2.
- No duplicate `(heuristic, mechanism)` pairs within one audit.

Skill hash: `6ff2b137a029fb7661a6b8b8d6d9c3a6b9c0da8cf0eca0c8f8d3f3e7c6b9a8d9` (prefix `6ff2b137a029fb76…` as reported by every smoke log line).

### Runs

All six runs via `scripts/smoke_l4_decision_psychology_multimodal.py`, orchestrated by `scripts/run_l4_decision_psychology_matched.sh`. Temperature pinned to 0.0 on Opus 4.6 and Sonnet 4.6; stripped on Opus 4.7 via `claude_client._omits_sampling_params` (Opus 4.7 rejects `temperature` with 400). `max_tokens=6144`. `screenshot_media_type="image/png"` on multimodal cells.

| cell | verdicts sha256 | native sha256 |
|---|---|---|
| opus46 × text | `7cd5205d949bdc6f…` | `75cb61c9752067b6…` |
| opus46 × image | `0abedfe304f14f25…` | `4828e81b91b5fd6e…` |
| sonnet46 × text | `402c906be87f8a87…` | `56b11f762f7a7952…` |
| sonnet46 × image | `3e0a95057b6c30a0…` | `f029bb9ac3b997b9…` |
| opus47 × text | `6f72ea3c9b4895ea…` | `9276fdb7c5b65929…` |
| opus47 × image | `da0c6f23d6254a3a…` | `cc2b7e2394ad028e…` |

Outputs at `data/derived/l4_audit/audit_decision_psychology/l4_verdicts_audit_decision_psychology_cluster02_{opus46,opus46_multimodal,sonnet46,sonnet46_multimodal,opus47,opus47_multimodal}.{jsonl,native.jsonl,provenance.json}`.

## Results

### Mechanism inventory across all six cells

Rows are Kahneman mechanisms, columns are cells. "✓/N" = present at Nielsen severity N (max across finding duplicates within the cell); "—" = absent.

| mechanism | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| loss aversion | ✓/4 | ✓/4 | ✓/4 | ✓/4 | ✓/4 | ✓/4 |
| anchoring & adjustment | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/**2** |
| WYSIATI | ✓/**4** | ✓/**2** | ✓/**3** | ✓/**4** | ✓/4 | ✓/**2** |
| ego depletion | ✓/3 | ✓/3 | ✓/**4** | ✓/**4** | ✓/3 | — |
| endowment effect | ✓/3 | ✓/3 | — | ✓/3 | ✓/3 | ✓/**4** |
| affect heuristic | ✓/3 | ✓/3 | ✓/3 | — | ✓/3 ×2 | ✓/3 |
| peak-end rule | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 | ✓/3 |
| narrow framing | — | ✓/3 | ✓/3 | ✓/3 | — | — |
| cognitive strain | ✓/3 | — | — | — | — | — |
| duration neglect | ✓/3 | — | — | — | — | — |
| System 1 / System 2 | — | — | — | — | — | ✓/3 |

Core convergence across all 6 cells: `loss aversion` (sev-4), `anchoring & adjustment` (sev-2 to sev-3), `peak-end rule` (sev-3 always `intent=unintentional`), and at least one additional structural mechanism in the `{WYSIATI, narrow framing, System 1 / System 2}` family capturing the hidden-option / forced-choice / decision-bypass structure. These four semantic slots are the load-bearing Kahneman signal every model × modality cell extracts regardless of family.

Opus 4.7 × image's mechanism fingerprint is the most unusual of the six cells: it replaces `ego depletion` (which every other cell names) with `System 1 / System 2` (the framework itself), uprates `endowment effect` to sev-4 (where other cells have it at sev-3 or absent), and downrates both `anchoring` and `WYSIATI` to sev-2.

### Per-cell summary prose (native payload, verbatim first sentence)

**Opus 4.6 text.** "The streak-loss modal is a multi-layered dark-pattern stack: it weaponises loss aversion on an endowed streak, anchors a subscription price against a fake 'original', imposes a countdown timer to suppress System 2 deliberation, and blocks the primary lesson path until the user either pays, watches three ads, or concedes the streak under a visually-subordinate dismiss link labelled 'lose streak' in 11px grey underlined text."

**Opus 4.6 image.** "The streak-recovery modal is a multi-layered dark-pattern stack: it weaponises loss aversion and the endowment effect under artificial time pressure, anchors a subscription price against a struck-through original, and buries the honest dismiss path in an 11px grey underlined link while the loss-avoidance CTA occupies a full-width bright-green button — choice-architecture asymmetry that exploits System 1 in the exact moment the user's System 2 is most depleted by mid-lesson cognitive work."

**Sonnet 4.6 text.** "This modal is a multi-mechanism dark-pattern stack: loss aversion is weaponised against an in-progress lesson via a blocking interrupt, a countdown timer imposes artificial urgency that suppresses System 2 deliberation, confirm-shaming copy and extreme visual asymmetry between the 'Keep my streak' CTA and the tiny 'lose streak' dismiss frame the free exit as shameful, and a struck-through anchor price manufactures a discount frame around a monthly subscription the user did not arrive at the modal intending to buy."

**Sonnet 4.6 image.** "This modal stacks at least four dark-pattern mechanisms — loss-aversion framing, artificial scarcity via countdown timer, anchoring on a struck-through fake-original price, and a three-tier choice architecture that buries the free exit — against users whose System 2 is depleted mid-lesson; the rendered layout amplifies the text in ways visible only in the image: a giant full-width green 'Keep my streak' CTA versus an 11 px grey underlined 'lose streak' dismiss is asymmetric visual weight that the text alone does not convey."

**Opus 4.7 text.** "The mid-lesson energy-depletion modal stacks loss aversion, endowment exploitation, a fake anchor, and an artificial countdown to coerce a paid subscription at the moment the user is most committed to finishing — a textbook dark-pattern configuration in which every surface-level affordance is tuned to suppress System 2 and trigger a loss-avoidance System 1 response."

**Opus 4.7 image.** "The streak-at-risk modal stacks loss aversion, endowment exploitation, artificial time pressure, and price anchoring to pressure a mid-lesson subscription purchase, with the honest exit ('lose streak') visually buried and the footer explicitly telling the user that the protective affordance (streak freeze) is 'unavailable at your level.'"

### Dimension-score divergence

| dim | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img | range |
|---|---|---|---|---|---|---|---|
| choice_architecture | **1** | **1** | **1** | **1** | **1** | **1** | 0 |
| cognitive_load_ease | 2 | 2 | 1 | 1 | 2 | 2 | 1 |
| judgment_heuristics | 2 | 2 | 2 | 2 | 2 | 2 | 0 |
| temporal_experience | 2 | 2 | 2 | 2 | 2 | 2 | 0 |

Three observations:

- **`choice_architecture = 1` is unanimous across all six cells.** Every cell drives this dimension to the rubric floor. The effect is largely mechanical — four to six sev-3+ dark-pattern findings on `choice_architecture` trip the dark-pattern cap rule twice (sev≥3 caps at 2, and the rubric's "multiple serious violations" narrows further to 1). What's interesting is that the *input* structurally forces this — the modal has no genuinely low-severity choice-architecture flaw; three separate choice-architecture failures (forced choice, hidden option, confirm-shaming dismiss) stack.
- **Sonnet scores `cognitive_load_ease` at 1, both Opus families at 2.** Both Sonnet cells rate the mid-lesson timing + ego-depletion interrupt combination as Nielsen-4 (`ego_depletion_trap`); both Opus families rate it Nielsen-3, and Opus 4.7 × image drops `ego depletion` entirely (reading System-2 bypass under a different mechanism label). A Nielsen-4 finding floors a dimension to 1 under the rubric; Nielsen-3 caps at 2. The cross-family severity-calibration disagreement is the single mechanism driving the dimension-score split, and it mirrors the Sonnet-reads-`4.1.3`-as-more-severe-than-Opus pattern from the accessibility eval.
- **`judgment_heuristics` and `temporal_experience` are identical across all six cells (both at 2).** These two dimensions carry the most universally-named mechanisms (anchoring, peak-end rule); the single sev-2-to-3 dark-pattern finding in each trips the dimension cap at 2 exactly, and nothing stacks beyond that.

### Modality effect, per-model

**Opus 4.6 (9 → 8).** Image drops `cognitive strain` (text-only `scarcity_timer_suppression` finding, reading the countdown as System-2-suppressing strain) and `duration neglect` (text-only `hedonic_treadmill_streak`, a speculative temporal reading of the streak system as a whole). Image adds `narrow framing` (`forced_choice_no_defer`, grounded in visually observing the modal has no close affordance and no system-back). Net: image strips two speculative text-only readings and adds one layout-grounded reading. Image *downrates* `WYSIATI` from sev-4 → sev-2 — the text-only version reads "Streak freezes unavailable at your level" as a catastrophically deceptive hidden-option reveal; the image run reads the same prose as a lower-severity disclosure issue because the visual weight of the three primary options dominates the footer.

**Sonnet 4.6 (7 → 7).** Same cardinality, different composition: image drops `affect heuristic` (`confirm_shaming`) but adds `endowment effect` (`endowment_exploitation`). Image *uprates* `WYSIATI` from sev-3 → sev-4 — opposite direction to Opus 4.6, same direction as the eval's single largest modality-swing observation. Sonnet 4.6 multimodal is the only cell with three Nielsen-4 findings (loss aversion, WYSIATI, ego depletion), producing the highest severity concentration in the matrix.

**Opus 4.7 (8 → 8, different mechanism mix).** Image keeps 8 findings but rearranges the Kahneman vocabulary substantially. It drops `ego depletion` (which the text cell rates sev-3), collapses `affect heuristic ×2` down to `affect heuristic ×1`, and adds two surprising moves: it names `System 1 / System 2` as its own mechanism slot (the framework itself — see caveats) and emits the matrix's only `intent=absent` finding (`base_rate_neglect_streak_loss` at sev-2, effectively declining to attribute this reading). Image also *doubles* `loss aversion` (sev-4 × 2) and uprates `endowment effect` from sev-3 → sev-4, while downrating `anchoring` and `WYSIATI` to sev-2. Net severity concentration is comparable to the text cell (2× sev-4 + 4× sev-3 + 2× sev-2) but the fingerprint is the most idiosyncratic of the six audits.

### Convergence pattern

Mechanisms surfaced by all 6 cells — load-bearing convergence:

- `loss aversion` (sev-4 everywhere, always on `q[1]` / `q[2]` plus the modal's streak-loss copy; Opus 4.7 × image uniquely files it twice)
- `anchoring & adjustment` (sev-2 to sev-3 everywhere, always on the `$6.99 → $3.49` strike-through)
- `WYSIATI` (sev-2 to sev-4, always on the hidden-option "streak freezes unavailable at your level" footer or the forced-choice structure)
- `peak-end rule` (sev-3 everywhere, always tagged `intent=unintentional` — the one mechanism the models consistently decline to frame as dark-pattern)

Mechanisms surfaced by 5 of 6 cells:

- `ego depletion` — all cells except Opus 4.7 × image (5/6)
- `endowment effect` — all cells except Sonnet 4.6 text (5/6)
- `affect heuristic` — all cells except Sonnet 4.6 image (5/6)

Mechanisms surfaced by exactly one cell (unique readings):

- `cognitive strain` — Opus 4.6 text only (reading the countdown as explicit System-2 strain)
- `duration neglect` — Opus 4.6 text only (the only cell that reaches for temporal-experience Kahneman vocabulary beyond peak-end)
- `System 1 / System 2` — Opus 4.7 × image only (and arguably not a specific mechanism — see caveats)

### Intent distribution

Intent is a per-finding tag the accessibility skill does not carry; this is a Kahneman-specific output column.

| intent | op46.txt | op46.img | son46.txt | son46.img | op47.txt | op47.img |
|---|---|---|---|---|---|---|
| `dark_pattern` | 7 | 7 | 6 | 6 | 6 | 6 |
| `unintentional` | 2 | 1 | 1 | 1 | 2 | 1 |
| `nudge` | 0 | 0 | 0 | 0 | 0 | 0 |
| `absent` | 0 | 0 | 0 | 0 | 0 | **1** |

Across 47 audited findings, 38 are `dark_pattern`, 8 are `unintentional`, exactly one lands in `absent`, and zero in `nudge`. The matrix is near-unanimous that the modal is adversarial-or-inadvertent-never-helpful — and no cell reports a finding at `intent=nudge`, even though the rubric explicitly supports that label. The skill's `nudge` category is empirically unused on an adversarial stimulus; whether it ever lights up on a benign-or-helpful stimulus is a question for the full-corpus run.

The `unintentional` findings cluster almost entirely on `peak-end rule` (every cell files `peak_end_dread` as `intent=unintentional`). Opus 4.6 text and Opus 4.7 text each also file one additional unintentional finding (`duration neglect` / `endowment effect` respectively), reading a specific mechanism as a by-product of the product architecture rather than a deliberate manipulation. The single `intent=absent` finding — `base_rate_neglect_streak_loss` at sev-2 in Opus 4.7 × image — is structurally different: it fills a finding slot to then dismiss it, effectively using the audit format to say "this mechanism is not doing load-bearing work here." The interpretive work of drawing the `dark_pattern` / `unintentional` / `absent` lines is where the skill adds the most value over a purely descriptive audit.

### The Opus 4.7 × image non-determinism

This cell was run twice, and the two runs produced substantively different audits — not just different word choice, but a different mechanism fingerprint. The contract is the same (one-cluster input, same skill hash, same system prompt, same temperature-stripped request), and the non-determinism is structural to the transport: Opus 4.7 rejects `temperature=0` with a 400, so `claude_client._omits_sampling_params` unpins it and each call samples freely.

**First attempt (fallback):** `findings[1] missing keys: ['recommendation']`. Seven of the eight findings parsed cleanly; the eighth was a complete finding with `dimension`, `heuristic`, `mechanism`, `intent`, `violation`, `severity`, `evidence_source`, `evidence_quote_idxs` all populated, but the `recommendation` field was omitted. The raw payload was preserved under `payload.raw_response` in the native sidecar. Structurally this was a one-field compliance slip, not a content regression — the substantive audit was coherent, the output contract was not.

**Second attempt (audited):** 8 findings, parse-clean, 6 `dark_pattern` + 1 `unintentional` + 1 `absent`, mechanism mix `{loss aversion ×2, anchoring, WYSIATI, endowment, affect heuristic, peak-end, System 1 / System 2}` — notably *missing* `ego depletion` which every other cell names, and *adding* a rare `intent=absent` finding that no other cell emits.

Two observations worth carrying forward to the full-corpus run:

- **Opus 4.7 audits are not replayable.** Fixing cached prompts + inputs + skill hash does not produce byte-identical outputs, because the transport layer cannot pin sampling on this model family. For any eval that cares about reproducibility, Opus 4.7 requires either `n > 1` sampling (then report variance) or a retry-until-parse loop (accepting the first audit that conforms). The accessibility eval sidestepped this because its single Opus 4.7 × image run happened to parse first-try; that was luck, not a property of the model.
- **One rerun is not enough to characterise variance.** We have two Opus 4.7 × image samples (one fallback, one audited). The audited sample may itself be far from the modal output for this stimulus. The full-corpus run should treat this cell as requiring `n ≥ 3` to get a meaningful variance estimate on mechanism fingerprint.

The smoke's fallback behaviour — exit 1, preserve raw under `payload.raw_response`, let the orchestration shell continue the grid — is working as designed. The right remediation is upstream (handle non-determinism at the call-site for Opus 4.7), not in the parser.

### Contract artefacts — what got written

All six cells produced the three-file set:

- `*.jsonl` — one `AuditVerdict` row, Pydantic-validated
- `*.native.jsonl` — full native Claude payload keyed on `verdict_id` (fallback cell stores the raw unparsed response under `payload.raw_response` per `_fallback_native`)
- `*.provenance.json` — summary with `dimension_score_totals`, `nielsen_severity_histogram`, `intent_histogram`, `mechanism_counts` (sorted by count desc then mechanism name for deterministic diffs), `input_tokens`, `output_tokens`, `modality`, `mode`, `screenshot_bytes`, `screenshot_media_type`, `skill_hash`, `skill_id`, `model`, `temperature`, `max_tokens`, `fallback_count`, `fallback_reasons`, `transport_failure_count`.

The provenance shape is the decision-psychology counterpart to the accessibility `{wcag_level_histogram, wcag_ref_counts}`: the Kahneman skill doesn't have an external spec to pin against, so the "what distinguishes this audit from that one" signal lives in the intent histogram plus top-mechanism tally.

**Meta-sidecar coverage is deliberately absent.** The smoke script does not write ADR-011 `.meta.json` sidecars — same known hygiene gap documented in the sister accessibility eval. The production `l4_audit_decision_psychology` module does emit meta sidecars; the smoke path skips them because each run is a one-cell ad-hoc call. Fixing this is a smoke-script change (same fix would apply to accessibility smoke), not a skill or module change.

## Caveats

- **One cluster, one HTML, one screenshot.** This is a six-cell matrix on a single deliberately-adversarial input. Generalisation to the full corpus requires the full-corpus run (deferred). The input was explicitly designed to stack mechanisms the skill names in its reference sheets — this is a faithfulness test ("do models report what's structurally there"), not a discovery test ("do models find unexpected issues").
- **The dark-pattern cap rule is doing most of the dimension-scoring work.** `choice_architecture = 1` across all 6 audited cells is driven by the `sev ≥ 3 dark_pattern → dim ≤ 2` parser rule compounded by multiple findings in the same dimension. The score is strongly correlated with the number of sev-3+ dark-pattern findings, which is structurally what a dark-pattern stack produces. The rubric is working as designed, but a cluster with a single dark-pattern finding would still land at `dim=2` regardless of severity asymmetry — the cap is binary, not graded. Worth revisiting for the full-corpus audit once we see how often single-finding dark-pattern cells occur.
- **Peak-end rule is always tagged `unintentional` — worth scrutiny.** Every cell files `peak_end_dread` (reading the "streak loss" moment as a manufactured negative peak that will bias retrospective evaluation) as `intent=unintentional`. This is a plausible reading — the product wasn't designed to maximise user pain at the dismiss moment, it was designed to maximise purchase conversion, and peak-end is a downstream consequence. But an equally plausible reading is that the "All progress resets to 0" + `permanently lost` + `lose streak` framing is a deliberate peak-negative choice. The skill's `intent` rubric nudges models toward `unintentional` for downstream consequences; whether that's the right bias is a SKILL.md-level question.
- **Opus 4.7 × image names `System 1 / System 2` as a mechanism — that's the framework, not a mechanism within it.** The skill's reference sheets list specific Kahneman mechanisms (`loss aversion`, `anchoring`, `WYSIATI`, `ego depletion`, etc.) that all operate under the dual-process framework; `System 1 / System 2` itself is the meta-label for the two-mode cognitive architecture. The cell used it to label the general observation that the modal's visual weight and timer pressure are tuned to bypass slow deliberation. That's a legitimate audit observation, but "System 1 / System 2" in the `mechanism` field is one abstraction level above what the other cells name. The skill does not forbid this, but a stricter mechanism taxonomy would.
- **Opus 4.7 × image required a rerun to parse-clean, and the rerun is not byte-identical to what a third run would produce.** Opus 4.7 rejects `temperature=0`, so `_omits_sampling_params` unpins sampling for this model; the cell is non-deterministic under the contract. First attempt fell back on a single-finding `missing recommendation` slip; second attempt audited cleanly but with a substantively different mechanism fingerprint (dropped `ego depletion`, gained `System 1 / System 2` + `intent=absent`). For the full-corpus run, Opus 4.7 cells should use `n ≥ 3` sampling with variance reported rather than a single-sample point estimate.
- **Cost tracker 3× Opus overestimate still applies.** Same as the accessibility eval: tracker-reported Opus 4.6 and 4.7 spend is ~3× actual (`$15/$75` per MTok hardcoded vs actual `$5/$25`). Total live cost for this six-cell matched run was ~$0.30 real / ~$0.90 tracker.
- **Playwright dependency is a dev-only addition.** Generating the `duolingo_streak_modal.png` screenshot required `uv add --dev playwright` + `playwright install chromium`. Captured in `pyproject.toml`/`uv.lock` under dev extras; does not affect runtime. The sandbox had no system emoji fonts, so the flame glyph is an inline SVG (no system-font dependency) — future adversarial UI mocks should follow the same rule so the render is reproducible across environments.

## Reproducing this document

L4 is a Claude API layer; for this matched eval the smoke script bypasses the replay cache and always hits live (`mode: {text,image}_direct_sdk` in provenance). To reproduce byte-identically, the inputs to pin are:

- `data/derived/l4_audit/audit_decision_psychology/audit_decision_psychology_input.jsonl` → sha256 `dc6d981f…`
- `skills/audit-decision-psychology/SKILL.md` → sha256 `e9d8a05c…`, `skill_hash` `6ff2b137…`
- `data/artifacts/ui/duolingo_streak_modal.png` → sha256 `bcad10de…`, 119630 bytes, `image/png`
- `data/artifacts/ui/duolingo_streak_modal.html` → sha256 `cdfcbd47…`, 5677 bytes

Regenerate live (not replay — smoke is live-only):

```bash
bash scripts/run_l4_decision_psychology_matched.sh --all
```

The `--all` flag forces re-runs of every cell regardless of prior result. Without it, the script's skip-if-success logic checks each cell's provenance `audited_count` and only re-runs cells that were fallbacks (or are missing entirely) — so with all six cells currently at `audited_count=1`, a bare re-run is a no-op. Note that Opus 4.7 cells are structurally non-replayable (see caveats): `--all` will produce different mechanism fingerprints on each invocation for those two cells even with byte-identical inputs.

Per-cell (for one-off re-runs):

```bash
uv run python scripts/smoke_l4_decision_psychology_multimodal.py \
  --model claude-opus-4-6 --modality text
uv run python scripts/smoke_l4_decision_psychology_multimodal.py \
  --model claude-opus-4-6 --modality image
uv run python scripts/smoke_l4_decision_psychology_multimodal.py \
  --model claude-sonnet-4-6 --modality text
uv run python scripts/smoke_l4_decision_psychology_multimodal.py \
  --model claude-sonnet-4-6 --modality image
uv run python scripts/smoke_l4_decision_psychology_multimodal.py \
  --model claude-opus-4-7 --modality text
uv run python scripts/smoke_l4_decision_psychology_multimodal.py \
  --model claude-opus-4-7 --modality image
```
