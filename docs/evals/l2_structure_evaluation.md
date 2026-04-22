# L2 structure-of-complaint — pipeline evaluation (N=50 sub-sample)

**Date:** 2026-04-22
**Related:** ADR-009 (L1 model decision, extended here to L2), ADR-011 (replay log contract), `src/auditable_design/layers/l2_structure.py`
**Decision:** Opus 4.7 canonical L2 model. L1 input = `l1_full_opus46.jsonl` (per ADR-009). `l1_eval_opus47.jsonl` retained as shadow L1 input for cross-check.

## Purpose

This document records the N=50 sub-sample evaluation of the L2 structure-of-complaint pipeline. Two questions drove the run:

1. Does the choice of L1 classifier model (`opus46` canonical vs `opus47` shadow) materially change the L2 output when the L2 model is held constant?
2. Is the L2 pipeline — model, prompt, parser, quarantine routing — stable enough to graduate from pilot (N=10) to a sub-sample checkpoint before committing to full-N?

This file captures *what was measured, how, and what we now know*. The decision inherited from ADR-009 is not re-opened here; what's new is the empirical confirmation that the L1 choice propagates cleanly into L2 and does not need its own model shoot-out.

## Executive summary

On an N=50 sub-sample drawn from the 600-review corpus, two L2 runs were executed under identical conditions except for the L1 classifier input: one run used `l1_full_opus46.jsonl` (canonical), the other used `l1_eval_opus47.jsonl` (shadow). Each run processed 50 UX-relevant reviews as routed by its respective L1 classifier; 47 `review_id`s appear in both runs (the L1 classifiers agreed), 3 are unique to each run (the L1 classifiers disagreed on `is_ux_relevant`). Both runs produced 39 graphs and 11 thin-quarantine rows with an identical set of quarantined `review_id`s on the overlap. On the 36 reviews that appear as graphs in both runs, the canonical JSON of `nodes` + `edges` is byte-identical for all 36. Node-type and edge-relation distributions match within two percentage points on every category (and within one percentage point on eight of nine). The L2 layer is, in practice, indifferent to the L1 model choice on this sample when the L2 model and `skill_hash` are fixed — a result that confirms ADR-009's L1 decision without requiring its own L2-level shoot-out.

A secondary finding: the run surfaced one parse failure (review `07d0c087`) caused by a model response that omitted the `edges` top-level key. SKILL.md permits zero edges, so the parser was tightened to accept a missing `edges` key as an empty list (separately regression-tested in `tests/test_l2_structure.py`). After the fix, `07d0c087` routes to `under_minimum_nodes` quarantine on both runs — recovered, not lost.

## Methodology

### Sample

- 50 UX-relevant reviews processed by each L2 run, drawn deterministically from the corresponding L1 classifier's output (`data/raw/corpus.jsonl`, `sha256=a1ed84d0…`).
- Because each L1 classifier labels `is_ux_relevant` slightly differently, the 50-review sets for the two runs are not identical: 47 `review_id`s are shared (both L1 classifiers agreed they were UX-relevant), 3 `review_id`s are unique to each run (one L1 said UX-relevant, the other didn't). Union across the two runs: 53 distinct `review_id`s.
- Three `review_id`s in the sample also appear in the L1 pilot gold set (`data/eval/l1_gold.csv`, 20 rows); a partial overlap by construction, not a curated one.

### Runs

Two L2 runs, identical except for L1 input:

| run_id | L1 input | L1 input sha256 | L2 model | skill_hash |
|---|---|---|---|---|
| `l2-n50-opus46-rerun` | `l1_full_opus46.jsonl` | `abcea3e8…` | Opus 4.7 | `f697f817…` |
| `l2-n50-opus47-rerun` | `l1_eval_opus47.jsonl` | `eeaefb3f…` | Opus 4.7 | `f697f817…` |

Both runs share the replay cache (`data/cache/responses.jsonl`) keyed per ADR-011 on `sha256(skill_id, skill_hash, model, temperature, max_tokens, system, user)`. Because the L2 user-prompt text is derived from the *review body* (not the L1 tag output), identical review bodies produce identical cache hits regardless of which L1 classifier fed them — this is what makes the two runs expected to converge, not just empirically observed to.

### Quality gates (per row)

A model response passes L2 if:

- JSON parses as an object with `nodes` required and `edges` optional (treated as `[]` if absent);
- `3 ≤ |nodes| ≤ 12`;
- every `verbatim_quote` is a substring of the source review body (Option B offset authority: the model emits quotes, the pipeline computes offsets);
- every edge references existing `node_id`s with a valid `relation_type`.

Failures route to `data/quarantine/l2_thin_*.jsonl` under one of five `QuarantineReason` values.

### Metrics

- *Routing agreement* — for each review, did both runs place it in the same bucket (graph vs thin)?
- *Graph identity* — on reviews that appear as graphs in both runs, does the canonical JSON (sorted keys, no whitespace) of `nodes` + `edges` byte-match?
- *Node-type / edge-relation distribution* — count and share per type / relation, per run.
- *Thin quarantine breakdown* — count per `QuarantineReason`, per run; overlap of `review_id` sets.

### Reproducibility

Both runs serve from `data/cache/responses.jsonl` on re-invocation with `--mode replay`. Meta sidecars alongside both the graphs and thin files carry `artifact_sha256`, input hashes, and `skill_hash` — the full provenance triple needed to reconstruct either run deterministically.

## Results

### Per-run headline

| run | graphs | thin | total L2-eligible | parse failures |
|---|---|---|---|---|
| `opus46` | 39 | 11 | 50 | 0 |
| `opus47` | 39 | 11 | 50 | 0 |

### Routing agreement

- Union of L2-eligible `review_id`s across both runs: 53.
- `review_id`s present in **both** runs: 47.
- Routing agreement on those 47: **47 / 47** (zero bucket flips).

The 6 `review_id`s present in only one run are reviews that the L1 gate routed to L2 under one classifier but not the other — a function of the L1 layer, not of L2 behaviour.

### Graph identity on shared reviews

On the 36 `review_id`s that produced a graph in **both** runs:

- Canonical JSON of `nodes` + `edges` byte-identical: **36 / 36**.
- Differ: 0.

This is the cleanest possible outcome: whenever the same review body reaches L2 under the same model and `skill_hash`, cache determinism makes the output bit-for-bit identical regardless of what L1 tagged it with.

### Graph structure

| run | n_graphs | total nodes | mean nodes/graph | median | total edges | mean edges/graph | median |
|---|---|---|---|---|---|---|---|
| `opus46` | 39 | 174 | 4.462 | 4 | 114 | 2.923 | 3 |
| `opus47` | 39 | 178 | 4.564 | 4 | 115 | 2.949 | 3 |

Four nodes per graph is the modal output — typically one `pain` + one or two `triggered_element`s + one `expectation` or `lost_value`. Three-edge graphs dominate: enough to form one causal chain plus one lateral relation.

### Node-type distribution

| type | `opus46` n | `opus46` % | `opus47` n | `opus47` % |
|---|---|---|---|---|
| `pain` | 46 | 26.4% | 47 | 26.4% |
| `expectation` | 27 | 15.5% | 28 | 15.7% |
| `triggered_element` | 60 | 34.5% | 63 | 35.4% |
| `workaround` | 13 | 7.5% | 12 | 6.7% |
| `lost_value` | 28 | 16.1% | 28 | 15.7% |

Every node-type category matches within one percentage point. `triggered_element` is the largest class (unsurprising — most complaints name a concrete feature), `workaround` the smallest (users rarely report self-mitigation in reviews).

### Edge-relation distribution

| relation | `opus46` n | `opus46` % | `opus47` n | `opus47` % |
|---|---|---|---|---|
| `triggers` | 64 | 56.1% | 66 | 57.4% |
| `violates_expectation` | 26 | 22.8% | 27 | 23.5% |
| `compensates_for` | 14 | 12.3% | 12 | 10.4% |
| `correlates_with` | 10 | 8.8% | 10 | 8.7% |

`triggers` is the dominant causal relation by more than 2×; `correlates_with` (the "I can't prove causation" fallback) is under 10% — the skill's preference for typed causal claims over hedged co-occurrence is holding. Three of the four relations match within one percentage point; `compensates_for` differs by 1.85pp (12.3% vs 10.4%) — the only edge-relation category where the two runs diverge beyond 1pp. Since the 36 byte-identical overlap graphs contribute equally to both totals, this difference must come from the non-overlap graphs (3 per run).

### Thin quarantine breakdown

Identical profile on both runs:

| reason | `opus46` | `opus47` |
|---|---|---|
| `under_minimum_nodes` | 2 | 2 |
| `over_maximum_nodes` | 0 | 0 |
| `substring_containment` | 9 | 9 |
| `hallucination` | 0 | 0 |
| `schema_violation` | 0 | 0 |

The 11 quarantined `review_id`s are identical between runs. `substring_containment` at 9/11 (82% of thin cases) is the dominant failure mode — the model paraphrases instead of quoting verbatim, typically on short or typo-heavy reviews where the skill's instruction to "copy the span exactly" loses to a cleanup reflex. `under_minimum_nodes` at 2/11 is the "review is real but too thin for a 3-node minimum" bucket, including `07d0c087` below.

Zero hallucinations and zero schema violations on N=50 across two runs is the strongest per-run signal here: the model is not fabricating content it couldn't quote, and the JSON envelope is reliable. The structural quality gate works; the *verbatim-quote* gate is where most loss happens.

## Parser incident: `07d0c087`

During the initial `opus47` run, review `07d0c087` produced a parse error. The model response was:

```json
{"nodes": [{"node_id":"n1","node_type":"triggered_element","verbatim_quote":"energy system"}]}
```

— a valid JSON object, but with the `edges` top-level key absent.

The review body is short and non-causal: *"They now have an energy system. A new approach without having to go through as many ads for the free version."* One observation, no clear causal link. The model correctly declined to invent an edge. The parser, which required a strict `{nodes, edges}` key-set, raised `ParseError` and dropped the row.

SKILL.md explicitly permits zero edges ("zero or more edges"). The strict key-set check was over-specified. The parser was relaxed to:

- `nodes` required; raise `ParseError` with "missing required top-level key" if absent.
- `edges` optional; treat as `[]` if absent.
- Any *other* top-level key raises `ParseError` with "unexpected top-level keys" — the envelope is still narrow, just not brittle on an expected-legal omission.

Regression coverage added in `tests/test_l2_structure.py` (`test_missing_nodes_key_rejected`, `test_missing_edges_key_treated_as_empty`, `test_extra_top_level_key_rejected`). Full test suite stays green (75/75 L2, 298/298 total).

On the rerun after the fix, `07d0c087` was served from the replay cache with the *same* original response (per ADR-011, cache writes precede downstream parse, so the response persisted across the parse failure). Under the relaxed parser it now yields `1 node` and routes to `under_minimum_nodes` — recovered to quarantine, where it belongs, not silently lost.

## Key findings

1. *L1 model choice does not propagate into L2 output.* 47/47 routing agreement, 36/36 byte-identical graphs on shared reviews, identical quarantine profile. The L2 layer is functionally indifferent to which L1 classifier fed it on this sample, given the same L2 model and `skill_hash`. This is the empirical basis for promoting `l1_full_opus46.jsonl` → L2 canonical input without a separate L2-level comparison.
2. *Structural quality gate passes cleanly; verbatim-quote gate dominates quarantine.* 9/11 thin rows are `substring_containment` failures — the model paraphrases where the skill demands a literal copy. This is the next lever to consider if quarantine rate becomes a bottleneck on full-N. Zero hallucinations, zero schema violations on N=50 indicates the JSON envelope and node/edge typing are reliable.
3. *Parser specification was over-tight on a legal edge case.* The original strict `{nodes, edges}` key-set failed on a short non-causal review where the model correctly omitted `edges`. SKILL.md already documented "zero or more edges"; the parser didn't honour that. Fixed, tested, and covered with the cache-replay preserving the original API response as expected (ADR-011).
4. *Cache determinism confirmed end-to-end at L2.* The 36/36 byte-identical graph match is a stronger test of ADR-011's replay contract than the L1 pilot overlap (which matched on predictions but not on byte-serialization of multi-part structures). The replay log is behaving as specified on non-trivial outputs.

## Decision

Canonical L2 input for downstream layers (L3 clustering onward): `data/derived/l2_structure/l2_graphs_opus46.jsonl` — sourced from the canonical L1 run (`l1_full_opus46.jsonl`, per ADR-009). The `opus47` L2 run is retained as shadow for cross-verification but is not a downstream dependency.

No new ADR is raised. This evaluation is consistent with ADR-009's L1 decision and shows that decision propagates cleanly to L2 under the current pipeline configuration.

## Limitations

- *N=50, not N=600.* The sub-sample is a checkpoint before committing API budget to full-N. The 47/47 routing agreement and 36/36 byte-identical graphs are strong signals on this sample, but they are *by construction* — cache determinism guarantees identical output on identical input, so the meaningful test is really the 3 non-overlap reviews per side (where L1 disagreed on routing), not the 47 overlap. A full-N run would re-assert the pattern on more non-overlap cases.
- *Single L2 model evaluated.* Opus 4.7 was used under ADR-009's guidance; no L2-level shoot-out was conducted. If downstream layers surface behaviour that could be L2-model-specific, a shoot-out would be the proper follow-up.
- *`substring_containment` rate is a measurement artefact as much as a quality signal.* The check uses simple Python `in`, which is intentional per SKILL.md but can fail on cosmetic differences (smart-quote vs straight-quote, whitespace normalization) even when the model's paraphrase is faithful. A character-class-tolerant matcher would reduce the quarantine rate but would require a spec change.
- *Opus 4.6 EOL 2026-06-15.* As with L1, the canonical path becomes replay-only after this date. This is by design and expected.

## Evidence pointers

### Inputs
- Corpus: `data/raw/corpus.jsonl` — `sha256=a1ed84d0c31ac7fffb4f54a9b10745a55b056129aad04124bd75dc24c207a672`
- L1 canonical: `data/derived/l1_classification/l1_full_opus46.jsonl` — `sha256=abcea3e85dc8b5c59f001d9a6c90859478c77769ba0ed3a8a6bcab3fb0ab94b5`
- L1 shadow: `data/derived/l1_classification/l1_eval_opus47.jsonl` — `sha256=eeaefb3f9765a729a5e5c0d572c773e96c6b921366831aa81a880cfd1934036e`
- Prompt/skill: `src/auditable_design/skills/structure-of-complaint/` — `skill_hash=f697f817ce42e630b75414ba86cc6caf12f690e5a35de386e969bf872b5c0134`
- Pipeline: `src/auditable_design/layers/l2_structure.py`

### Outputs
- Canonical L2 graphs: `data/derived/l2_structure/l2_graphs_opus46.jsonl` — 39 rows — run_id `l2-n50-opus46-rerun`
- Shadow L2 graphs: `data/derived/l2_structure/l2_graphs_opus47.jsonl` — 39 rows — run_id `l2-n50-opus47-rerun`
- Canonical L2 thin: `data/quarantine/l2_thin_opus46.jsonl` — 11 rows
- Shadow L2 thin: `data/quarantine/l2_thin_opus47.jsonl` — 11 rows

Each with `.meta.json` sidecar carrying `artifact_sha256`, `run_id`, input hashes, `skill_hash`, and `schema_version`.

### Tests
- `tests/test_l2_structure.py` — 75 tests (pure parse, quarantine routing, CLI contract, regression coverage for the missing-`edges` parser fix)

### Cache
- `data/cache/responses.jsonl` — full replay log (all L2 calls committed)
