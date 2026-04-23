# L3b cluster labelling — full-corpus three-way model comparison

**Date:** 2026-04-23
**Related:** ADR-010 (adversarial-input discipline), ADR-011 (replay log contract), `docs/evals/l3_full_corpus_three_way.md` (L3 input to this eval), `skills/label-cluster/SKILL.md`, `skills/label-cluster/rubric.md`, `src/auditable_design/layers/l3b_label.py`
**Status:** Empirical record. Three full-corpus L3b runs over the L3 cluster inventories from `l3_full_corpus_three_way.md` — one per L1/L2/L3 pipeline branch (opus46, opus47, sonnet46). Labelling model is the same across all three runs (Haiku 4.5), so divergence in output attributes to input-cluster differences, not labeller drift.

## Purpose

L3b takes each L3 cluster and asks Claude Haiku 4.5 for one short, quote-anchored label (≤ 60 chars). Clusters that are genuinely incoherent — or coherent only at the affect level ("annoying", "disappointing") with no pain element to name — are labelled with the sentinel `"Mixed complaints"`. That sentinel is designed as a first-class signal for the downstream L4 cluster-coherence audit, not a failure mode.

This document records what Haiku produced on each of the three L3 inventories, whether the three pipelines agree on the *labellable* themes, and how the "Mixed complaints" rate decomposes into (a) clusters the rubric explicitly instructs Haiku not to label at element level (affect-only) vs (b) clusters whose content is genuinely heterogeneous (the junk-drawer case from `l3_full_corpus_three_way.md` §*Junk-drawer behaviour in sonnet46*).

## Executive summary

| | opus46 | opus47 | sonnet46 |
|---|---|---|---|
| L3 input clusters | 14 | 10 | 7 |
| Labelled with a theme | 6 | 5 | 3 |
| Labelled `Mixed complaints` | 8 | 5 | 4 |
| Parse failures / fallbacks | 0 | 0 | 0 |
| Transport failures | 0 | 0 | 0 |
| Haiku spend (USD) | $0.0198 | $0.0138 | $0.0081 |

Totals across the three runs: 31 labels emitted, 14 themed (45%), 17 `Mixed complaints` (55%), 0 fallbacks, 0 transport failures, $0.0417 spend. Every cluster in every inventory received a deterministic label; no run had to fall back to the `UNLABELED:<cluster_id>` placeholder.

The 55% Mixed rate is high but decomposes into two architecturally distinct buckets (see *Results → Mixed rate decomposition*): 13 of the 17 are affect-only clusters where the rubric explicitly prefers `Mixed complaints` over a weak affect paraphrase; 4 are genuinely heterogeneous clusters (including sonnet46's junk-drawer). Only the second bucket is a coherence problem; the first is the label layer behaving as designed.

## Methodology

### Inputs

Three L3 cluster inventories from `docs/evals/l3_full_corpus_three_way.md`:

| run | L3 input | input sha256 |
|---|---|---|
| opus46 | `data/derived/l3_clusters/l3_clusters_full_opus46.jsonl` | `2f0258d4526432643a8230f74cf300961460a68f95d6640385acffb97f7739d7` |
| opus47 | `data/derived/l3_clusters/l3_clusters_full_opus47.jsonl` | `2cbcac1fc2152612812e21a9174fd849480629a5da976e42c2cc9479ba271eff` |
| sonnet46 | `data/derived/l3_clusters/l3_clusters_full_sonnet46.jsonl` | `223e0bf452f60cda6a5570ee01e2aee3de44e99e9ccaa5c6abb34b391c6ea14e` |

For each cluster, L3b sends the `representative_quotes` list (5 quotes per cluster by L3 contract) wrapped under the ADR-010 injection-guard envelope:

```xml
<cluster_quotes>
  <q>...</q>
  <q>...</q>
  ...
</cluster_quotes>
```

The skill (`skills/label-cluster/SKILL.md`, sha256 `8f6bffe52347796050792e1016355d969e950db5102f0bcafc89650e4e2cf10b`) instructs Haiku to return strict JSON `{"label": "<≤60 chars>"}`, to anchor every label to the quotes, to avoid evaluative adjectives absent from the source, and to emit `"Mixed complaints"` for clusters whose quotes name no single pain element.

### Runs

| run_id | L3 input | clusters | labelled | Mixed | artefact sha256 | written_at |
|---|---|---|---|---|---|---|
| `l3b-full-opus46` | l3_clusters_full_opus46 | 14 | 6 | 8 | `95c2c46a7884576a66337156bd8434f7addd12a0d54c44e145af80e57b62b588` | 2026-04-23T09:47:07Z |
| `l3b-full-opus47` | l3_clusters_full_opus47 | 10 | 5 | 5 | `cf89519a283ac35f4f210b87f39d4c8ef0353e4a261f8448d8aa990e98a9f696` | 2026-04-23T09:47:16Z |
| `l3b-full-sonnet46` | l3_clusters_full_sonnet46 | 7 | 3 | 4 | `38655190454894a7cae411571f07cb4611ff346221548e4ac3e2c361ef3bf632` | 2026-04-23T09:47:23Z |

### Model and parameters

- `claude-haiku-4-5-20251001`, `temperature=0.0`, `max_tokens=128`
- Mode: live (replay cache was cold on all three runs; every label is a fresh Claude response recorded to `data/replay_log/<cache_key>.json` per ADR-011)
- Concurrency 6, USD ceiling $2.00 (actual total $0.0417)
- Code version `0.1.0`, skill hash `8f6bffe5…` (identical across all three runs)
- Output schema version 1 (same `InsightCluster` shape as L3; only the `label` field is mutated)

Identical skill, model, and parameters across all three runs. Divergence in output labels attributes to input-cluster differences, not labeller drift.

## Results

### Per-model label inventories

**opus46 — 14 clusters, 6 themed + 8 Mixed.**

| id | n | representative quote (first) | L3b label |
|---|---|---|---|
| 00 | 8 | "aprendí por mucho TIEMPO PARA NADA!!" | Mixed complaints |
| 01 | 5 | "I gave it 3 star" | Mixed complaints |
| 02 | 7 | "my streak is not maintained" | Streak tracking not maintained |
| 03 | 4 | "I keep getting it wrong" | Voice recognition marks correct answers wrong |
| 04 | 5 | "helping me to learn new languages" | Limited language selection or learning focus |
| 05 | 10 | "I don't like it" | Mixed complaints |
| 06 | 9 | "used to be good" | App quality declined over time |
| 07 | 13 | "TERRIBLE" | Mixed complaints |
| 08 | 12 | "Duolingo kind of sucks" | App quality declined over time |
| 09 | 6 | "very disappointing" | Mixed complaints |
| 10 | 46 | "This used to be a great app" | App quality declined over time |
| 11 | 12 | "completing my lesson every day" | Mixed complaints |
| 12 | 18 | "annoying" | Mixed complaints |
| 13 | 6 | "Very frustrating" | Mixed complaints |

**opus47 — 10 clusters, 5 themed + 5 Mixed.**

| id | n | representative quote (first) | L3b label |
|---|---|---|---|
| 00 | 4 | "aprendí por mucho TIEMPO" | Excessive monetization of previously free features |
| 01 | 7 | "is incorrect" | Voice recognition marks correct answers wrong |
| 02 | 10 | "Duolingo kind of sucks" | Mixed complaints |
| 03 | 12 | "terrible" | Mixed complaints |
| 04 | 9 | "very disappointed" | Mixed complaints |
| 05 | 10 | "completing my lesson every day" | Daily lesson completion limits |
| 06 | 11 | "frustrating" | Mixed complaints |
| 07 | 19 | "annoying" | Mixed complaints |
| 08 | 16 | "used to love this app" | App quality declined over time |
| 09 | 6 | "freezing all the time" | App freezes repeatedly |

**sonnet46 — 7 clusters, 3 themed + 4 Mixed.**

| id | n | representative quote (first) | L3b label |
|---|---|---|---|
| 00 | 6 | "duolingo that i know before" | Mixed complaints |
| 01 | 3 | "learn chess" | Chess feature discoverability |
| 02 | 6 | "very disappointing" | Mixed complaints |
| 03 | 8 | "Very frustrating" | Mixed complaints |
| 04 | 17 | "annoying" | Mixed complaints |
| 05 | 32 | "used to be a great app" | App quality declined over time |
| 06 | 5 | "I had completed the lesson" | Lesson progress not recorded |

### Cross-model theme convergence

| Theme | opus46 | opus47 | sonnet46 |
|---|---|---|---|
| App quality declined over time (regression) | **cluster_06, cluster_08, cluster_10** (3×) | cluster_08 | cluster_05 |
| Voice recognition marks correct answers wrong | cluster_03 | cluster_01 | — |
| Lesson progress / completion | — (Mixed: cluster_11) | cluster_05 (*Daily lesson completion limits*) | cluster_06 (*Lesson progress not recorded*) |
| Streak tracking not maintained | cluster_02 | — | — |
| Limited language selection / didactic focus | cluster_04 | — | — |
| Excessive monetization | — | cluster_00 | — |
| App freezes / technical stability | — | cluster_09 | — |
| Chess feature discoverability | — | — | cluster_01 |

Observations:

- **The tentpole regression theme is the only label shared across all three models.** Every inventory exposes "App quality declined over time" as its largest themed cluster. In opus47 and sonnet46 it is a single cluster; in opus46 Haiku emitted the same label on three separate L3 clusters (06, 08, 10). This is the downstream surface of opus46's L2 affect atomisation documented in `l3_full_corpus_three_way.md` §*Themes unique to one model* — L3 split the affect space finer, and L3b's label-only shape (no cross-cluster merging) faithfully preserves the split.
- **"Voice recognition marks correct answers wrong" recurs in both Opus models but not sonnet46.** Consistent with `l3_full_corpus_three_way.md` which noted the wrong-answers theme surfaces in opus46 (cluster_03) and opus47 (cluster_01) but not in sonnet46's 7-cluster inventory. L3b simply names it.
- **Each model has at least one unique theme.** opus47 contributes *Excessive monetization*, *Daily lesson completion limits*, and *App freezes repeatedly* — the three rare-but-real themes that its stricter L2 extraction preserved. sonnet46 contributes *Chess feature discoverability* and *Lesson progress not recorded*. opus46 contributes *Streak tracking not maintained* and *Limited language selection or learning focus*. None of these is reachable from the other inventories because L3 didn't surface them there.

### Mixed rate decomposition

Haiku's instructions (per `skills/label-cluster/SKILL.md` §*"Mixed complaints" sentinel*) are to emit `Mixed complaints` in two distinct situations. Of the 17 `Mixed` labels across the three runs:

**(a) Affect-only clusters (13/17).** Representative quotes are coherent but describe only the *feeling* — no pain element, no behaviour. Labelling these "Annoyance" or "Disappointment" would just echo the quotes with a noun, adding no element-level information. The rubric explicitly prefers `Mixed complaints` here.

| | cluster | first quote | why affect-only |
|---|---|---|---|
| opus46 | 05 | "I don't like it" | generic dislike, no element |
| opus46 | 07 | "TERRIBLE" | negative affect, no element |
| opus46 | 09 | "very disappointing" | disappointment affect |
| opus46 | 12 | "annoying" | annoyance affect |
| opus46 | 13 | "Very frustrating" | frustration affect |
| opus47 | 03 | "terrible" | negative affect |
| opus47 | 04 | "very disappointed" | disappointment affect |
| opus47 | 06 | "frustrating" | frustration affect |
| opus47 | 07 | "annoying" | annoyance affect |
| sonnet46 | 02 | "very disappointing" | disappointment affect |
| sonnet46 | 03 | "Very frustrating" | frustration affect |
| sonnet46 | 04 | "annoying" | annoyance affect |
| opus46 | 01 | "I gave it 3 star" | meta-affect (star-rating change), no pain element |

The last row is arguable — opus46 cluster_01 *is* coherent around "users lowered their star rating", which is a behaviour. But it is still the rating-change reaction, not the underlying pain that caused the change; naming it "Star rating reduced" would be a symptom-tier label with no element. Haiku's call to emit `Mixed complaints` here is defensible under the rubric though not the only reasonable choice.

**(b) Genuinely heterogeneous clusters (4/17).** Representative quotes span multiple pain elements and Haiku cannot honestly name one without ignoring the rest.

| | cluster | quotes span | L4 audit expectation |
|---|---|---|---|
| opus46 | 00 | Spanish/Portuguese regret + "didn't understand the lesson" | L4 should flag — language mix may be an upstream L2 artefact |
| opus46 | 11 | lesson completion + time + correctness (three elements) | L4 should flag — element plurality in one cluster |
| opus47 | 02 | "kind of sucks" + "becomes mad" + "keep stopping" + "originally loved" | L4 should flag — affect + instability + regression in one cluster |
| sonnet46 | 00 | regression + crashes + monetisation + access + affect | L4 should flag — the junk-drawer from `l3_full_corpus_three_way.md` §*Junk-drawer* |

Only the second bucket is a cluster-coherence concern. The first bucket is working as designed: the label layer declining to manufacture specificity from affect-only content. That is the trade-off the rubric's tier-3 ladder was written to make.

### Observations on specific labels

- **opus47 cluster_00 — "Excessive monetization of previously free features" (n=4).** Representative quotes are short bilingual ES/PT regret fragments plus "TODO ES DINERO AHORA". Haiku's synthesis goes beyond the literal quotes — *"of previously free features"* is not in any of the five quotes. The fifth representative quote on this cluster's full member list is *"o intuito de ser gratuito"* ("the intent of being free"), which does support the "previously free" claim. Defensible but close to the rubric's "no evaluative adjectives absent from quotes" edge; an L4 audit that checks label-to-quote support should probably score this near the boundary.
- **opus46 clusters 06 / 08 / 10 — three "App quality declined over time" labels.** The atomisation observation from the L3 eval surfaces cleanly here. L3b correctly does not try to de-duplicate across clusters (it has no cross-cluster visibility, only per-cluster quotes). A future L4 audit or downstream merge step can recognise the redundancy using label equality or centroid similarity; the label layer keeps its single-cluster contract honest.
- **Zero fallback and zero transport failure across 31 labels.** The parser's strict-JSON contract and the injection-guarded envelope held on every real cluster. The `UNLABELED:<cluster_id>` fallback path exercised by the unit tests (53/53 green) was not triggered in production on this corpus.

## Caveats

- **Label quality is Haiku's call, not independently graded here.** This document records *what* was labelled; the label rubric (`skills/label-cluster/rubric.md`) spells out a scoring scheme across schema validity / anchoring / specificity, but no rubric-based scoring run has been performed yet. The per-label commentary above is informal.
- **Haiku pricing is reconciled 1:1 against the Anthropic console.** The tracker reported $0.0417 for this run; the console charged ~$0.04. Haiku 4.5 matches `claude_client`'s hardcoded $1/$5 per MTok, same as Sonnet 4.6 — the 3× overestimate documented in memory is Opus-specific and does not apply here.
- **`Mixed complaints` is not "bad label" — it is an explicit first-class signal.** Reading this document as "55% of labels failed" would be wrong. The rubric prefers the sentinel over a weak affect-echo paraphrase, and the L4 audit is the intended consumer of this signal.
- **No independent model comparison at the labeller layer.** Unlike L1/L2/L3 (three pipelines, three models each), L3b uses one labeller (Haiku) against three inputs. The divergence isolated here is *input* divergence; no claim is made about whether a different labeller would have handled the same inputs differently. That is a separate eval.

## Reproducing this document

L3b is a Claude API layer; it is reproducible via the ADR-011 replay log rather than by re-running live. The three replay cache entries are keyed on `(skill_id="label-cluster", skill_hash="8f6bffe5…", model="claude-haiku-4-5-20251001", temperature=0.0, max_tokens=128, system=SYSTEM_PROMPT, user=<per-cluster-wrapped-quotes>)`.

Regenerate in replay mode from tracked inputs:

```bash
uv run python -m auditable_design.layers.l3b_label \
  --clusters data/derived/l3_clusters/l3_clusters_full_opus46.jsonl \
  --output data/derived/l3b_labeled_clusters/l3b_labeled_clusters_full_opus46.jsonl \
  --run-id l3b-full-opus46 \
  --mode replay

uv run python -m auditable_design.layers.l3b_label \
  --clusters data/derived/l3_clusters/l3_clusters_full_opus47.jsonl \
  --output data/derived/l3b_labeled_clusters/l3b_labeled_clusters_full_opus47.jsonl \
  --run-id l3b-full-opus47 \
  --mode replay

uv run python -m auditable_design.layers.l3b_label \
  --clusters data/derived/l3_clusters/l3_clusters_full_sonnet46.jsonl \
  --output data/derived/l3b_labeled_clusters/l3b_labeled_clusters_full_sonnet46.jsonl \
  --run-id l3b-full-sonnet46 \
  --mode replay
```

Verify:

```bash
sha256sum \
  data/derived/l3b_labeled_clusters/l3b_labeled_clusters_full_opus46.jsonl \
  data/derived/l3b_labeled_clusters/l3b_labeled_clusters_full_opus47.jsonl \
  data/derived/l3b_labeled_clusters/l3b_labeled_clusters_full_sonnet46.jsonl
```

Expected:

| File | sha256 |
|---|---|
| `l3b_labeled_clusters_full_opus46.jsonl` | `95c2c46a7884576a66337156bd8434f7addd12a0d54c44e145af80e57b62b588` |
| `l3b_labeled_clusters_full_opus47.jsonl` | `cf89519a283ac35f4f210b87f39d4c8ef0353e4a261f8448d8aa990e98a9f696` |
| `l3b_labeled_clusters_full_sonnet46.jsonl` | `38655190454894a7cae411571f07cb4611ff346221548e4ac3e2c361ef3bf632` |

Byte-identical replay holds if and only if (a) the three L3 input files are byte-identical to their recorded hashes, (b) the skill file's sha256 matches `8f6bffe5…`, and (c) the replay cache contains the 31 keyed entries recorded during the live run. Any of those missing triggers a fresh live call (if `--mode live`) or fails closed (if `--mode replay`).

## What's next

- **L4 cluster-coherence audit — the natural consumer of the `Mixed complaints` sentinel.** The 4 genuinely-heterogeneous clusters above (opus46_00, opus46_11, opus47_02, sonnet46_00) are the a-priori candidates for L4 to surface as low-coherence. An intra-cluster embedding-variance metric over member pain/expectation nodes would give each cluster a coherence score independent of the label; combining `label == "Mixed complaints"` with a high variance score is a stronger audit signal than either alone.
- **Label-rubric scoring run.** The rubric in `skills/label-cluster/rubric.md` was written but not executed as a separate eval. Running it over the 14 themed labels above would convert the informal per-label commentary in *Observations on specific labels* into a structured score across schema validity / anchoring / specificity.
- **Cross-cluster label de-duplication.** opus46 emitting "App quality declined over time" on three separate L3 clusters is an architecturally honest outcome of L3b's per-cluster contract, but downstream consumers may want a "merged theme" view. A post-L3b pass that groups clusters by (label equality ∨ centroid cosine-similarity above threshold) would collapse the atomisation without losing the underlying L3 structure. Out of scope for this iteration but is the natural bridge from labels to themes.
