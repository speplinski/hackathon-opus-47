# L2 structure-of-complaint — full-corpus three-way model comparison

**Date:** 2026-04-22
**Related:** ADR-009 (L1 model decision), ADR-011 (replay log contract), `docs/evals/l2_structure_evaluation.md` (N=50 sub-sample, precursor), `src/auditable_design/layers/l2_structure.py`
**Status:** Empirical record, no decision. The N=50 precursor established Opus 4.7 as canonical L2 model; this document captures three full-corpus runs that exist to feed L3/L4 comparative analysis, not to re-open the canonical decision.

## Purpose

The N=50 sub-sample eval (sibling doc) established two things: (a) the L2 pipeline is stable enough to graduate to full-N, and (b) the L2 model is indifferent to the L1 classifier input when held constant. This document records three full-corpus L2 runs under a different experimental design: each model runs its **own** L1 output through L2, producing three end-to-end comparable pipelines rather than three variants of a single pipeline. The motivation is downstream: L3 clustering on ~500 pain+expectation nodes per model is statistically meaningful in a way that N=50's ~73 nodes was not (silhouette, inter-run cluster overlap, etc. become informative at this scale), and the audit story benefits from comparing *deployable* pipelines rather than *ablation* slices of one.

The three-way design also surfaces model-level failure profiles that the N=50 run hinted at but could not stabilise — specifically, the different distributions of quarantine reasons (substring drift vs sparse extraction vs hallucination) across the three models.

## Executive summary

On the full 600-review corpus (`sha256=a1ed84d0…`), each model classified L1 and then ran L2 on its own UX-relevant subset:

| | Opus 4.6 | Opus 4.7 | Sonnet 4.6 |
|---|---|---|---|
| L1 UX-relevant | 404 | 411 | 401 |
| L2 valid graphs | 296 (73.3%) | 306 (74.5%) | 282 (70.3%) |
| L2 quarantine | 105 (26.0%) | 105 (25.5%) | 115 (28.7%) |
| L2 parse failures | 3 | 0 | 4 |
| Total nodes | 1419 | 1347 | 1221 |
| Pain + expectation (→ L3) | 621 | 528 | 480 |
| Tracker cost | $19.83 | $21.93 | $3.38 |
| Console cost | $6.58 | $7.32 | $3.38 |

The three-way total actually billed is $17.28 — well under $20 for a full comparative dataset. Accept rates converge within 4 percentage points. Quarantine totals are near-identical for the two Opus models and only 10 rows higher for Sonnet. Where the models materially diverge is in the *distribution* of quarantine reasons and in the *shape* of the graphs they produce — described in the *Results* section below. Critically, the three pain+expectation node pools feeding L3 differ by 29% (621 vs 480) for reasons that are not purely corpus-size driven (opus47 processed more L1 rows than opus46 but produced fewer pain+expectation nodes), which means L3 clustering on the three pools is likely to surface different — not just re-scaled — structure.

A secondary finding, reconciled against Anthropic's usage console: the Opus pricing in `claude_client.PRICING_USD_PER_MTOK` overestimates actual billing by a factor of 3.0× on both Opus 4.6 and Opus 4.7; Sonnet 4.6 pricing matches billing to the cent. This does not affect pipeline correctness but is documented under *Cost reconciliation* below because the kill-switch ceiling semantics depend on the tracker figure.

## Methodology

### Sample

- 600 reviews from `data/raw/corpus.jsonl` (`sha256=a1ed84d0c31ac7ff…`).
- Each model's L1 classification output routes its own UX-relevant subset into L2. Unlike the N=50 sub-sample eval (which held L1 input constant at `l1_full_opus46.jsonl` to isolate L2 variance), this run lets each model own its full pipeline. The three L1 UX-relevant sets overlap on 393 `review_id`s; 11 are opus46-only, 18 are opus47-only, the sonnet46 intersection is ~390 (full pairwise Jaccard is tangential to this doc — see `l1_model_evaluation.md` for the L1 agreement analysis).
- Rationale for full-flow (not shared-L1): this is how the pipeline would actually be deployed — pick one model, run the whole pipeline. Cumulative variance through L1 → L2 is part of the audit story, not a confound to be isolated.

### Runs

| run_id | L1 input | L1 input sha256 | L2 model | skill_hash | item_count | quarantine | written_at |
|---|---|---|---|---|---|---|---|
| `l2-full-opus46` | `l1_full_opus46.jsonl` | `abcea3e8…` | Opus 4.6 | `f697f817…` | 296 | 105 | 2026-04-22T21:21:25Z |
| `l2-full-opus47` | `l1_eval_opus47.jsonl` | `eeaefb3f…` | Opus 4.7 | `f697f817…` | 306 | 105 | 2026-04-22T21:33:13Z |
| `l2-full-sonnet46` | `l1_eval_sonnet46.jsonl` | `70fec742…` | Sonnet 4.6 | `f697f817…` | 282 | 115 | 2026-04-22T21:38:11Z |

All three runs share the same `skill_hash` (`f697f817…`) — identical `SKILL.md` authoring the structure-of-complaint contract — so any divergence is attributable to L1 classification differences or to the L2 model's behaviour on the same prompt, not to prompt drift.

Artifact SHA-256:

| run | graphs | quarantine |
|---|---|---|
| opus46 | `aa71bd40…` | `e8ea9da1…` |
| opus47 | `579a516d…` | `7a637ba3…` |
| sonnet46 | `8a6b14a7…` | `5c144ded…` |

### Quality gates

Identical to the N=50 eval: JSON parses as object, `3 ≤ |nodes| ≤ 12`, every `verbatim_quote` is a substring of the source review body, every edge references existing `node_id`s with a valid `relation`. Failures route to `QuarantineReason`: `substring_containment`, `under_minimum_nodes`, `hallucination`, `parse_error`, `llm_error`.

### Reproducibility

All three runs are cache-replayable from `data/cache/responses.jsonl` per ADR-011 (key = `sha256(skill_id, skill_hash, model, temperature, max_tokens, system, user)`). Re-running any of the three with `--mode replay` reproduces the artifacts byte-for-byte without contacting the API.

## Results

### Quarantine breakdown

| Reason | opus46 | opus47 | sonnet46 |
|---|---|---|---|
| `substring_containment` | 85 | 65 | 81 |
| `under_minimum_nodes` | 16 | 36 | 22 |
| `hallucination` | 4 | 4 | 12 |
| Parse/LLM failures | 3 | 0 | 4 |

The three distributions are qualitatively different:

- **Opus 4.6** dominates `substring_containment` (85 — 81% of its quarantine). This is paraphrase drift: the model emits a plausible-sounding "verbatim" quote that isn't actually a substring of the source review. Under-minimum rate is the lowest of the three (16 — the model tends to produce rich rather than sparse graphs).
- **Opus 4.7** dominates `under_minimum_nodes` (36 — 2.3× opus46). The model is stricter about emitting quotes the validator can verify (65 substring failures, 24% fewer than opus46), but this rigor comes at the cost of occasionally emitting only 1–2 nodes when the review doesn't clearly support more. Zero parse failures — the most format-disciplined of the three.
- **Sonnet 4.6** is the only model where hallucination is non-trivial (12 — 3× either Opus, and in absolute terms the only quarantine reason where Sonnet exceeds both Opus models). Accept rate is the lowest (70.3%); substring and under-minimum rates are Opus-comparable.

### Graph shape

Average graph size:

| Model | nodes/graph | edges/graph | max nodes | min nodes |
|---|---|---|---|---|
| opus46 | 4.8 | 3.1 | 7 | 3 |
| opus47 | 4.4 | 2.9 | 7 | 3 |
| sonnet46 | 4.3 | 2.8 | 7 | 3 |

Node-type distribution (share of total nodes):

| Type | opus46 | opus47 | sonnet46 |
|---|---|---|---|
| `triggered_element` | 33.2% | 35.6% | 35.4% |
| `pain` | 29.1% | 26.5% | 25.3% |
| `lost_value` | 17.8% | 18.9% | 21.2% |
| `expectation` | 14.7% | 12.7% | 14.0% |
| `workaround` | 5.2% | 6.4% | 4.1% |

Edge relation distribution:

| Relation | opus46 | opus47 | sonnet46 |
|---|---|---|---|
| `triggers` | 64.4% | 62.2% | 58.2% |
| `violates_expectation` | 20.5% | 18.0% | 20.9% |
| `compensates_for` | 7.9% | 9.6% | 5.5% |
| `correlates_with` | 7.2% | 10.2% | 15.4% |

The `correlates_with` relation is the weakest (softest causal claim) in the skill taxonomy. Sonnet 4.6 uses it 2.1× more frequently than opus46 (15.4% vs 7.2%), consistent with a model that reaches for the noncommittal relation when causation is unclear. Opus 4.6 correspondingly leans heaviest on `triggers` (64.4%) — the strongest causal claim.

### Cost reconciliation

Per the replay-log usage totals, reconciled against Anthropic usage console:

| Run | New live calls | Input tokens | Output tokens | Tracker $ | Console $ | Ratio |
|---|---|---|---|---|---|---|
| opus46 | 404 | 797,463 | 104,905 | $19.83 | $6.58 | 3.01× |
| opus47 | 361 | 962,110 | 99,920 | $21.93 | $7.32 | 3.00× |
| sonnet46 | 356 | 702,687 | 84,626 | $3.38 | $3.38 | 1.00× |

(New live calls < L1 UX-relevant for opus47 and sonnet46 because their N=50 sub-sample eval runs had already populated the replay-log cache keyed on `sha256(skill_id, skill_hash, model, temperature, max_tokens, system, user)`; those ~50 reviews served from cache at zero marginal cost rather than being re-billed. Opus46's 404 all live because no prior opus46 L2 run had written those keys at full-corpus scope.)

The tracker uses `PRICING_USD_PER_MTOK` hardcoded in `src/auditable_design/claude_client.py`: `(15.0, 75.0)` for both Opus models, `(3.0, 15.0)` for Sonnet 4.6. The Opus figures reflect the 2025 launch price; actual April-2026 billing implies approximately `(5.0, 25.0)`. Sonnet pricing is unchanged between 2025 and 2026. A docstring comment in the pricing table explicitly flags this as expected: *"Prices below are placeholders … readers should treat the kill-switch numbers as approximate until we verify against billing."*

Operational consequence: the `--usd-ceiling` kill-switch is conservative-by-design for Opus (fires at ~3× the true cost) and accurate for Sonnet. For scope-sizing decisions, divide tracker estimates by 3 for Opus; trust tracker for Sonnet. An update to the pricing table is tracked but not urgent — the conservative behaviour is safe.

## Interpretation — behavioural profiles

The three models hit quality on this task but with distinct failure modes. Each profile below is stated in terms of the observable quarantine and distribution figures already tabulated; no claim is made that does not trace to a specific row above.

- **Opus 4.6.** Produces the largest graphs (4.8 nodes/graph, 3.1 edges/graph), commits to the strongest causal relation most often (64.4% `triggers`), and uses the soft `correlates_with` least (7.2%). Pays for richness with the highest substring-containment quarantine count (85) — i.e., paraphrase drift in `verbatim_quote`. Fit when downstream layers tolerate paraphrase; poor fit when `verbatim_quote` traceability is load-bearing.

- **Opus 4.7.** Lowest substring-failure count (65, −24% vs opus46), zero parse failures, highest format discipline. Pays for that with 2.3× more `under_minimum_nodes` quarantines (36 vs 16) — the model declines to emit structure the review does not support. Fit when `verbatim_quote` must be a true substring and the pipeline can tolerate losing ~12% of UX-relevant reviews to sparse graphs; poor fit when a downstream layer requires dense input per review.

- **Sonnet 4.6.** Roughly 5× cheaper than Opus per graph ($0.012/graph console vs ~$0.022). Hallucination quarantines are 3× either Opus model (12 vs 4), and `correlates_with` usage is 2.1× opus46 (15.4% vs 7.2%) — both consistent with lower confidence in causal claims. Fit when cost or latency are binding and downstream tolerates noise; poor fit when audit traceability or causal-claim strength is a first-class requirement.

The goal of this section is not to rank the models but to make the trade-offs legible enough that a reviewer can choose per-context — which is the property the audit pipeline is built to surface.

## Reproducing this document

The three derived files below are not tracked (`.gitignore` excludes `data/derived/`); the replay log is. A reviewer can regenerate all three pipelines byte-for-byte from the tracked `data/raw/corpus.jsonl` + `data/cache/responses.jsonl` and verify the artifact sha256 values match this document.

L1 regeneration (per model):

```bash
uv run python -m auditable_design.layers.l1_classify \
  --mode replay --model claude-opus-4-6 \
  --output data/derived/l1_classification/l1_full_opus46.jsonl \
  --run-id l1-full-opus46

uv run python -m auditable_design.layers.l1_classify \
  --mode replay --model claude-opus-4-7 \
  --output data/derived/l1_classification/l1_eval_opus47.jsonl \
  --run-id l1-eval-opus47

uv run python -m auditable_design.layers.l1_classify \
  --mode replay --model claude-sonnet-4-6 \
  --output data/derived/l1_classification/l1_eval_sonnet46.jsonl \
  --run-id l1-eval-sonnet46
```

L2 regeneration (each uses its own L1 output):

```bash
uv run python -m auditable_design.layers.l2_structure \
  --mode replay --model claude-opus-4-6 \
  --classified data/derived/l1_classification/l1_full_opus46.jsonl \
  --output data/derived/l2_structure/l2_graphs_full_opus46.jsonl \
  --quarantine data/quarantine/l2_full_opus46.jsonl \
  --run-id l2-full-opus46

uv run python -m auditable_design.layers.l2_structure \
  --mode replay --model claude-opus-4-7 \
  --classified data/derived/l1_classification/l1_eval_opus47.jsonl \
  --output data/derived/l2_structure/l2_graphs_full_opus47.jsonl \
  --quarantine data/quarantine/l2_full_opus47.jsonl \
  --run-id l2-full-opus47

uv run python -m auditable_design.layers.l2_structure \
  --mode replay --model claude-sonnet-4-6 \
  --classified data/derived/l1_classification/l1_eval_sonnet46.jsonl \
  --output data/derived/l2_structure/l2_graphs_full_sonnet46.jsonl \
  --quarantine data/quarantine/l2_full_sonnet46.jsonl \
  --run-id l2-full-sonnet46
```

Verify artifact sha256:

```bash
sha256sum \
  data/derived/l1_classification/l1_full_opus46.jsonl \
  data/derived/l1_classification/l1_eval_opus47.jsonl \
  data/derived/l1_classification/l1_eval_sonnet46.jsonl \
  data/derived/l2_structure/l2_graphs_full_opus46.jsonl \
  data/derived/l2_structure/l2_graphs_full_opus47.jsonl \
  data/derived/l2_structure/l2_graphs_full_sonnet46.jsonl \
  data/quarantine/l2_full_opus46.jsonl \
  data/quarantine/l2_full_opus47.jsonl \
  data/quarantine/l2_full_sonnet46.jsonl
```

Expected values:

| File | sha256 |
|---|---|
| `l1_full_opus46.jsonl` | `abcea3e85dc8b5c59f001d9a6c90859478c77769ba0ed3a8a6bcab3fb0ab94b5` |
| `l1_eval_opus47.jsonl` | `eeaefb3f9765a729a5e5c0d572c773e96c6b921366831aa81a880cfd1934036e` |
| `l1_eval_sonnet46.jsonl` | `70fec74246a7413c05cadcc1cf20630b72906a76dacb88b701d1555633db6612` |
| `l2_graphs_full_opus46.jsonl` | `aa71bd404256e7532af47a76e73708a535d67638ae307786202a2a03d228b0ab` |
| `l2_graphs_full_opus47.jsonl` | `579a516d38ae44e874954feb1c57ee43da25060e3f510ca98330d79da1484f01` |
| `l2_graphs_full_sonnet46.jsonl` | `8a6b14a7d8614683b8f220720bdd5e48c6948dcfbc03d8920936cf165db03aed` |
| `l2_full_opus46.jsonl` (quarantine) | `e8ea9da1923eaee99c6f1c74b2733ab57f4f09880ab46c5c9237523f66ce2f82` |
| `l2_full_opus47.jsonl` (quarantine) | `7a637ba3a7072e4ba7e27bc788387d1e4f6542754d94bab40d5264e3d7ae7330` |
| `l2_full_sonnet46.jsonl` (quarantine) | `5c144ded3e4eb000ae2e773a8e80a5de313feccc90264f841553ffe0d297bd9f` |

`--mode replay` does not call the Anthropic API; all six runs serve from the replay log. Any hash divergence from the table indicates either a replay-log regression or a deterministic-ordering bug and should be investigated before trusting downstream (L3) numbers.

## What's next

- **L3 clustering on all three pain+expectation pools (N = 621 / 528 / 480).** The N=50 eval produced degenerate clusters on opus46 (`fallback_reason: null` masking a 2-cluster semantic mess) and a better KMeans fallback on opus47. At full-corpus scale HDBSCAN should find real density structure on all three; comparing cluster counts, silhouette, and representative-quote coherence closes the three-way audit matrix.
- **Caveats on sonnet46 downstream.** If sonnet46's L3 silhouette is materially lower than the Opus runs, that is the predicted cascade from L2 hallucination rate into L3 embedding space — document it, don't try to fix it at L3.
- **Pricing table update (`claude_client.PRICING_USD_PER_MTOK`).** Non-urgent; the divergence between tracker and console is now documented, and the kill-switch is safe because it's conservative.
- **ARCHITECTURE.md §4.4.** The N=50 empirical note currently in §4.4 should be updated with a pointer to this doc and a sentence-level summary once L3 runs complete; full three-way L3 analysis will land as a sibling eval doc, not inline in §4.4.
