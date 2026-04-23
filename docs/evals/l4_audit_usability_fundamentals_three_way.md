# L4 audit — `audit-usability-fundamentals` skill, thin-spine three-way model comparison

**Date:** 2026-04-23
**Related:** ADR-008 (audit severity anchors), ADR-010 (adversarial-input discipline), ADR-011 (replay log contract), `ARCHITECTURE.md` §4.5 (L4 layer), `docs/evals/l3b_matched_three_way.md` (L3b input source), `skills/audit-usability-fundamentals/SKILL.md`, `src/auditable_design/layers/l4_audit.py`
**Status:** Empirical record. Thin-spine smoke test: one labelled cluster × one Norman-framework audit skill × three labeller models (Opus 4.7, Opus 4.6, Sonnet 4.6). Purpose is to prove the L4 audit contract end-to-end on real data, not to evaluate the full cluster inventory. A full-corpus L4 run across the 31 L3b clusters from `l3b_matched_three_way.md` is a separate downstream eval.

## Purpose

L4 takes a labelled L3b cluster and invokes one audit skill to produce an `AuditVerdict` — a normalised record of heuristic violations, severities on the ADR-008 anchored scale (0/3/6/9), evidence anchors into the input quotes, and a reference to the skill's native JSON payload. The goal is a uniform contract across heterogeneous audit skills so downstream L5 synthesis can merge verdicts without reasoning about per-skill schema idiosyncrasies.

This smoke exercises exactly that contract on one cluster: the smallest possible slice that proves the pipeline is sound. Specifically it answers:

- Does a live Claude call, parsed through the strict output contract, produce a valid `AuditVerdict` across all three candidate audit-tier models?
- Does the skill's Nielsen 1–4 severity scale remap cleanly onto the ADR-008 anchored scale (1→3, 2→5, 3→7, 4→9)?
- Do the three required artefacts — verdicts JSONL, native-payload sidecar JSONL, provenance sidecar JSON — get written via `storage.write_jsonl_atomic` with the expected `.meta.json` chain?
- Do the three models produce merit-comparable audits, or does the contract tolerate substantive disagreement on the same input?

The last question is what turns a smoke test into signal for the full-corpus run.

## Executive summary

| | Sonnet 4.6 | Opus 4.6 | Opus 4.7 |
|---|---|---|---|
| Clusters audited | 1 | 1 | 1 |
| Fallback count | 0 | 0 | 0 |
| Transport failures | 0 | 0 | 0 |
| Findings emitted | 2 | 2 | 2 |
| Severity-3 findings | 1 | 1 | 0 |
| Severity-2 findings | 1 | 1 | 2 |
| `interaction_fundamentals` dim score | 2 | 2 | **3** |
| `action_cognition` dim score | 3 | 3 | 3 |
| `error_architecture` dim score | 5 | 5 | 5 |
| `system_maturity` dim score | 5 | 5 | **4** |
| Tracker spend (USD) | $0.0219 | $0.1088 | $0.1481 |

Tracker total **$0.2788**; per calibration rule (Opus ÷ 3, Sonnet 1:1) predicted real spend **~$0.107**. Applies both `claude-opus-4-6` and `claude-opus-4-7` equally per the L3b matched-run calibration.

Zero fallback, zero transport failure across three live calls — the strict output contract held on every model. All three verdicts share the same `verdict_id` (`audit-usability-fundamentals__cluster_01`), the same `skill_hash` (`3a26404c…`), and the same `native_payload_ref` shape; they disagree only on the finding content, which is what the contract is there to absorb.

The interesting divergence is entirely at the severity and dimension-score layer:

- **Sonnet 4.6 and Opus 4.6 produce near-identical audits.** Same finding shape (one severity-3 in `interaction_fundamentals`, one severity-2 in `action_cognition`), identical dimension scores across all four dimensions. The heuristic names differ cosmetically (`signifier_affordance_mismatch` vs `missing_signifier`) — both are Norman signifier-family violations — but the substance is the same.
- **Opus 4.7 is more conservative on thin evidence.** It emits two severity-2 findings instead of one sev-3 + one sev-2, and also introduces a dimension-score inconsistency: `system_maturity=4` with zero findings in that dimension (the rubric says `no findings → 5`). This is the first concrete datapoint on what Opus 4.7's audit-tier behaviour looks like vs Opus 4.6.

Both behaviours are defensible. The cluster has two discoverability complaints (`idx=1`, `idx=2`) out of five quotes, with three others that are neutral or positive (`learn chess`, `helps me alot in chess`, `I chose chess`). The SKILL.md calibration anchor says a single "I didn't know which button to tap" complaint → severity 2; two such complaints within a cluster of five is a coin flip between 2 and 3. Opus 4.7 calls it 2, the other two call it 3.

## Methodology

### Input

One cluster extracted from the matched-model L3b output for sonnet46 — `cluster_01 "Chess feature discoverability"`, the only cluster in that run that is both coherent (not `Mixed complaints`) and sits in a clearly auditable Norman dimension (discoverability / signifier). Extracted with `sed -n '2p'` from the full labelled file, no re-labelling.

| | sha256 |
|---|---|
| `data/derived/l4_audit/audit_usability_fundamentals/audit_usability_fundamentals_input.jsonl` | `d5dca239cf37f72fecb3c2f95a70659c0bed6e4260b482cb64263eb79fffa8f1` |

Cluster shape: label `"Chess feature discoverability"`, 5 representative quotes:

- `q[0]`: "learn chess"
- `q[1]`: "I didn't find chess"
- `q[2]`: "i can't seem to find how to play chess"
- `q[3]`: "helps me alot in chess"
- `q[4]`: "I chose chess"

Two quotes evidence discoverability failure (`q[1]`, `q[2]`); three are neutral to positive. This is a thin-evidence cluster by construction — the smoke is meant to exercise the honest-limits path of the skill, not the dense-evidence path illustrated in SKILL.md's worked example.

### Skill

`skills/audit-usability-fundamentals/SKILL.md`, single-file authored in English from the `input/1-the-design-of-everyday-things-revised-and-expanded-edition/` Structure-of-Thought synthesis. Norman-framework audit across four dimensions (Interaction Fundamentals, Action & Cognition, Error Architecture, System Maturity), Nielsen 1–4 severity scale, dimension score 1–5.

Skill hash (as recorded in every run's `.meta.json`): `3a26404c40323c627581f8e10541f0effc33d20bfc9f91d4757dc270019ae8cd`. Identical across all three runs — one skill version, deterministic hashing.

### Runs

| run_id | model | artefact sha256 (verdicts) | native sha256 | written_at |
|---|---|---|---|---|
| `l4-audit-usability-fundamentals-cluster01-sonnet46` | `claude-sonnet-4-6` | `a7b5b2286d87cad444fa16624b7378719e4c79b174f4a9c438f6f1b7937cb787` | `7385e9638ca5e4b4efad44c3dee4aa5465016767871fdf95b39d2c2a86b5cc6f` | 2026-04-23T15:29:56Z |
| `l4-audit-usability-fundamentals-cluster01-opus46` | `claude-opus-4-6` | `084f3311ac4609e1ba914766792df4d6be5823360a0673963f4c2b41a807ee7b` | `61cac9405ec224440fea83c4f1f3daaf1fb8d8b1b6558287a64062b704442e6f` | 2026-04-23T15:30:14Z |
| `l4-audit-usability-fundamentals-cluster01-opus47` | `claude-opus-4-7` | `3411818e156e8df693d6ac85863741b25832efad9acf21572114b87a62f94376` | `6f43cc495e49b1507888a7b9cf0c7483bcf0b0001edcbf93a6d4c4da70aba76e` | 2026-04-23T15:30:52Z |

All three runs: `temperature=0.0`, `max_tokens=4096`, mode `live`, concurrency=6, USD ceiling $5.00 (actual total $0.2788 tracker), same skill hash. Outputs at `data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_{sonnet46,opus46,opus47}.{jsonl,native.jsonl,provenance.json}`.

## Results

### Per-model audit output

**Sonnet 4.6.**

> Multiple users cannot locate a chess feature that demonstrably exists in the product, pointing to a signifier or navigation failure rather than an absent feature. The cluster is thin and the evidence is sparse, so findings are limited to what the quotes directly support.

| dim | score | heuristic | sev | evidence |
|---|---|---|---|---|
| `interaction_fundamentals` | 2 | `signifier_affordance_mismatch` | 3 | `q[1]`, `q[2]` |
| `action_cognition` | 3 | `wide_gulf_of_execution` | 2 | `q[1]`, `q[2]` |
| `error_architecture` | 5 | — | — | — |
| `system_maturity` | 5 | — | — | — |

**Opus 4.6.**

> A subset of users cannot discover the chess feature despite actively seeking it, indicating a signifier/discoverability failure. The cluster is thin and mixed — some quotes are positive — so findings are limited to the clear discovery gap.

| dim | score | heuristic | sev | evidence |
|---|---|---|---|---|
| `interaction_fundamentals` | 2 | `missing_signifier` | 3 | `q[1]`, `q[2]` |
| `action_cognition` | 3 | `wide_gulf_of_execution` | 2 | `q[1]`, `q[2]` |
| `error_architecture` | 5 | — | — | — |
| `system_maturity` | 5 | — | — | — |

**Opus 4.7.**

> Users report being unable to locate a chess feature they know exists in the product, pointing at a discoverability failure in the feature's entry points. The quote set is thin and mixed in sentiment, so findings are kept narrow.

| dim | score | heuristic | sev | evidence |
|---|---|---|---|---|
| `interaction_fundamentals` | 3 | `invisible_affordance` | 2 | `q[1]`, `q[2]` |
| `action_cognition` | 3 | `wide_gulf_of_execution` | 2 | `q[0]`, `q[2]` |
| `error_architecture` | 5 | — | — | — |
| `system_maturity` | 4 | — | — | — |

### Cross-model convergence

All three models agree on the shape of the audit: two findings, same two dimensions (`interaction_fundamentals` + `action_cognition`), both anchored in the two discoverability quotes, both naming the same Norman failure family (absent signifier + wide execution gulf). This is the load-bearing convergence result: **the contract is reproducible in substance across the audit tier models**, not only at the schema level.

Three specific points of divergence, each informative for full-corpus design:

1. **Heuristic-ID drift** — three different heuristic strings for the same Norman failure family: `signifier_affordance_mismatch` (Sonnet), `missing_signifier` (Opus 4.6), `invisible_affordance` (Opus 4.7). SKILL.md's canonical-names list is ordered bullets ("Invisible affordances — ...", "False signifiers — ...", etc.); all three chosen strings are reasonable paraphrases of the first item, but the surface form differs. The second finding's ID is stable (`wide_gulf_of_execution` across all three). Practical implication: label equality will not group findings across models reliably. Either the skill is tightened to emit a controlled vocabulary, or downstream L5 de-duplication treats heuristic IDs as soft and groups on `(dimension, evidence_set)` or embedding similarity.

2. **Severity calibration: Opus 4.7 runs colder.** Opus 4.7 assigns the signifier violation severity 2 where Sonnet/Opus 4.6 both assign 3. The rubric anchor — "a single 'I didn't know which button' complaint → 2" — is compatible with either reading when the cluster has two such complaints out of five. Opus 4.7 appears to read the three neutral-to-positive quotes as diluting the claim's force; the other two weight the count of failure quotes more. This is a severity-calibration delta worth tracking on the full-corpus run, because a systematic 1-point difference compounds into dimension-score differences.

3. **Dimension-score-vs-findings consistency: Opus 4.7 breaks it once.** Opus 4.7's `system_maturity=4` with zero findings in that dimension violates the rubric's "`no findings → 5`" table row. Our parse validator only checks 1–5 range, not consistency with the findings list, so this passes validation. The native payload preserves it faithfully. Reading Opus 4.7's summary ("feature's entry points", mixed sentiment) suggests the model applied a soft system-maturity penalty for the broader discoverability pattern rather than zero-ing it out cleanly. The SKILL.md table is strict; Opus 4.7's behaviour here is an out-of-contract judgement call. Two options for full corpus: (a) tighten the validator to reject score-vs-findings inconsistency, (b) accept it and let L5 synthesis mediate. Decision deferred — noted here as a real, specific divergence rather than a smoke artefact.

### Contract artefacts — what got written

Every run produced the complete artefact family:

- `*.jsonl` — one `AuditVerdict` row, Pydantic-validated shape
- `*.jsonl.meta.json` — ADR-011 sidecar with `run_id`, `written_at`, `layer`, `input_hashes` (1 entry pointing at audit_usability_fundamentals_input.jsonl), `skill_hashes` (1 entry pointing at the audit skill), `artifact_sha256`
- `*.native.jsonl` — full native Claude payload keyed on `verdict_id`
- `*.native.jsonl.meta.json` — matching ADR-011 sidecar for the native file
- `*.provenance.json` — auditor-facing summary: `dimension_score_totals`, `nielsen_severity_histogram`, `findings_count`, `fallback_count`, `transport_failure_count`, model, temperature, max_tokens, skill_id

Every row's `verdict_id` equals `{skill_id}__{cluster_id}` (`audit-usability-fundamentals__cluster_01`), stable across reruns and across models — `run_id` lives in the meta sidecar, not the verdict row, preserving verdict equality under replay.

Severity mapping from Nielsen 1–4 to the ADR-008 anchored 0–10 scale (`{1:3, 2:5, 3:7, 4:9}`) applied correctly on every finding: Nielsen-3 findings surfaced as anchored-7, Nielsen-2 as anchored-5. Evidence quote indices were preserved as part of the finding's `reasoning` string (since the cluster's `representative_quotes` has no per-quote review-id mapping, `evidence_review_ids` is `[]` by design — the quote text and index are embedded in `reasoning` instead, documented as a known trade-off in `l4_audit.py`'s `_build_heuristic_violations`).

### Replay cache state

Three new entries in `data/cache/responses.jsonl`, one per model. Each keyed on the ADR-011 tuple `(skill_id, skill_hash, model, temperature, max_tokens, system, user)`:

| key_hash (prefix) | model | input tok | output tok | tracker cost |
|---|---|---|---|---|
| `7e758a11afec…` | `claude-sonnet-4-6` | 5270 | 405 | $0.0219 |
| `608c8f816335…` | `claude-opus-4-6` | 5270 | 396 | $0.1088 |
| `17ba57640dba…` | `claude-opus-4-7` | 7320 | 510 | $0.1481 |

Opus 4.7's 7320 input tokens vs the 5270 reported by the other two is a provider-side tokenisation property, not a prompt-size difference (the `build_user_message` output is bytewise identical across all three runs, and the system prompt is identical). A rerun of any of these three with `--mode replay` should be a full cache hit and produce byte-identical outputs.

## Caveats

- **This is a 1-cluster smoke, not a cluster-inventory eval.** Any claim about the skill's full-corpus behaviour (hit rate on real issues, false-positive rate on incoherent `Mixed complaints` clusters, dimension coverage) has to come from the full-corpus L4 run across all 31 L3b clusters.
- **The chosen cluster is favourable to the skill.** Cluster 01 is a clean discoverability case — Norman's most canonical territory (signifiers, invisible affordances, execution gulf). The audit's behaviour on adversarial inputs (the 55% `Mixed complaints` rate from the Haiku baseline, the 4 genuinely-heterogeneous clusters, affect-only clusters) is untested here. The SKILL.md's `"Mixed complaints"` short-circuit path (max 1 finding, `system_maturity.incoherent_cluster`) is exercised in unit tests but not in production.
- **Severity delta between Opus 4.7 and the other two is a single datapoint.** One cluster is not enough to claim a systematic calibration drift. The full-corpus run will either confirm or dissolve this observation.
- **Opus tracker cost is 3× the real charge.** Memory-noted `claude_client` pricing for `claude-opus-4-6`/`claude-opus-4-7` is hardcoded at $15/$75 per MTok vs actual ~$5/$25. Real spend on this smoke was ~$0.022 + ~$0.036 + ~$0.049 ≈ **~$0.107**, not the $0.2788 the tracker shows. Sonnet pricing is 1:1 correct.
- **Heuristic-ID drift is a skill-design issue, not a model failure.** The SKILL.md lists canonical names as bullet-nested Norman principles ("Invisible affordances", "False signifiers"), not as a controlled vocabulary of snake_case IDs. Asking three models to paraphrase the same family into a snake_case identifier produces three phrasings. This is a prompting choice to make before the full-corpus run.

## Reproducing this document

L4 is a Claude API layer; reproducible via the ADR-011 replay log rather than by re-running live. The three replay cache entries are keyed on `(skill_id="audit-usability-fundamentals", skill_hash="3a26404c…", model, temperature=0.0, max_tokens=4096, system=SYSTEM_PROMPT, user=<build_user_message(cluster_01)>)`.

Regenerate in replay mode from tracked inputs:

```bash
uv run python -m auditable_design.layers.l4_audit \
  --clusters data/derived/l4_audit/audit_usability_fundamentals/audit_usability_fundamentals_input.jsonl \
  --output   data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_sonnet46.jsonl \
  --native-output data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_sonnet46.native.jsonl \
  --model claude-sonnet-4-6 \
  --run-id l4-audit-usability-fundamentals-cluster01-sonnet46 \
  --mode replay

uv run python -m auditable_design.layers.l4_audit \
  --clusters data/derived/l4_audit/audit_usability_fundamentals/audit_usability_fundamentals_input.jsonl \
  --output   data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_opus46.jsonl \
  --native-output data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_opus46.native.jsonl \
  --model claude-opus-4-6 \
  --run-id l4-audit-usability-fundamentals-cluster01-opus46 \
  --mode replay

uv run python -m auditable_design.layers.l4_audit \
  --clusters data/derived/l4_audit/audit_usability_fundamentals/audit_usability_fundamentals_input.jsonl \
  --output   data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_opus47.jsonl \
  --native-output data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_opus47.native.jsonl \
  --model claude-opus-4-7 \
  --run-id l4-audit-usability-fundamentals-cluster01-opus47 \
  --mode replay
```

Verify:

```bash
sha256sum \
  data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_sonnet46.jsonl \
  data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_opus46.jsonl \
  data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster01_opus47.jsonl
```

Expected:

| File | sha256 |
|---|---|
| `l4_verdicts_audit_usability_fundamentals_cluster01_sonnet46.jsonl` | `a7b5b2286d87cad444fa16624b7378719e4c79b174f4a9c438f6f1b7937cb787` |
| `l4_verdicts_audit_usability_fundamentals_cluster01_opus46.jsonl` | `084f3311ac4609e1ba914766792df4d6be5823360a0673963f4c2b41a807ee7b` |
| `l4_verdicts_audit_usability_fundamentals_cluster01_opus47.jsonl` | `3411818e156e8df693d6ac85863741b25832efad9acf21572114b87a62f94376` |

Byte-identical replay holds if and only if (a) `audit_usability_fundamentals_input.jsonl` sha256 matches `d5dca239…`, (b) the skill hash matches `3a26404c…`, and (c) the replay cache contains the three keyed entries recorded during the live run. Any of those missing triggers a fresh live call (if `--mode live`) or fails closed (if `--mode replay`).

## What's next

- **Full-corpus L4 run across the 31 L3b clusters.** The matched-model L3b output at `data/derived/l3b_labeled_clusters/matched_rubric_v1/` is the natural input. Open questions the full run should resolve: (a) does Opus 4.7's severity-conservative behaviour hold across 31 clusters, or is it noise on thin evidence, (b) what fraction of `Mixed complaints` clusters exercise the `incoherent_cluster` short-circuit correctly, (c) heuristic-ID drift rate across a larger sample of Norman failure families, (d) dimension-score-vs-findings consistency rate per model.
- **Audit-tier model choice for the full spine.** The smoke does not identify a winner. Opus 4.6 and Sonnet 4.6 produce the closest-to-rubric output; Opus 4.7 produces subtly different but defensible output. Sonnet 4.6 is 5× cheaper at the tracker level and 1.5× cheaper at real cost. A reasonable default for the full run is Sonnet 4.6 with an Opus 4.7 diff on a sampled subset, mirroring the L3/L3b matched-vs-baseline pattern.
- **Tighten the parse validator on dim-score vs findings consistency.** The Opus 4.7 `system_maturity=4 with zero findings` case is out-of-contract by the skill rubric but in-range by the validator. A table-enforcement check (`max(severity in dim findings) → implied_score; assert dim_score <= implied_score`) would catch it and convert the current tolerance into a traceable fallback. Worth deciding before the full run.
- **Controlled-vocabulary pass on heuristic IDs.** Turn SKILL.md's canonical-names list into an explicit snake_case ID table (`{family_name} → canonical_id`) and add a parse-time normalisation that maps paraphrases to canonical IDs. Prevents the three-models-three-IDs pattern from fragmenting L5 synthesis.
- **Second audit skill to prove the contract is genuinely uniform.** The spine is *1 cluster × 1 skill*. Width is *N clusters × M skills*. A second audit skill — Kahneman decision-psychology is the obvious next one, with SoT source material already in `input/2-pulapki-myslenia-o-myslen-fr-pub-ebook/` — running alongside Norman on the same cluster would demonstrate that L5 synthesis has a real uniform-contract surface to merge over.
