# L3 clustering — full-corpus three-way model comparison

**Date:** 2026-04-23
**Related:** ADR-009 (L1 model decision), ADR-011 (replay log contract), ADR-012 (local encoder at L3), `docs/evals/l1_model_evaluation.md`, `docs/evals/l2_full_corpus_three_way.md` (L2 input to this eval), `docs/evals/l2_structure_evaluation.md` (N=50 precursor), `src/auditable_design/layers/l3_cluster.py`
**Status:** Empirical record. The N=50 precursor (documented inline in ARCHITECTURE.md §4.4) produced a degenerate 2-cluster HDBSCAN result on opus46 and a 6-cluster KMeans fallback on opus47 — neither structurally trustworthy. This document records three full-corpus L3 runs that supersede the N=50 observation.

## Purpose

L3 takes each model's L2 complaint graphs, embeds the `pain` and `expectation` nodes with a local sentence-transformers encoder, and clusters them with HDBSCAN (KMeans as fallback). The N=50 eval left the layer's behaviour at scale unresolved: was the 2-cluster degeneracy a small-sample artefact or an intrinsic property of opus46's L2 output? This document answers that and extends the three-way audit matrix to the clustering layer — completing the L1 → L2 → L3 comparison for three independent full-flow pipelines.

The three-way design also surfaces second-order effects that the N=50 run could not: different L2 behavioural profiles (rich-but-drifty / strict-but-sparse / cheap-but-noisy — see `l2_full_corpus_three_way.md`) produce different density structures downstream, and those differences are the audit-relevant signal.

## Executive summary

| | opus46 | opus47 | sonnet46 |
|---|---|---|---|
| L2 pain+expectation nodes (input) | 621 | 528 | 480 |
| L3 clusters | 14 | 10 | 7 |
| Algorithm | HDBSCAN | HDBSCAN | HDBSCAN |
| Fallback invoked? | no | no | no |
| Largest cluster (n) | 46 | 19 | 32 |
| Smallest cluster (n) | 4 | 4 | 3 |

All three runs succeeded under HDBSCAN (`eom` cluster selection, euclidean metric, `min_cluster_size=5`) — KMeans fallback was not triggered on any. The N=50 degenerate case documented in ARCHITECTURE.md §4.4 is resolved by corpus scale: density-based clustering needs roughly an order of magnitude more points than N=50 provided. This document freezes that observation and unblocks downstream layers (L3b labelling, L4 audits) against stable cluster inventories.

Cluster count declines faster than input size across the three models: −23% nodes (621 → 480) but −50% clusters (14 → 7) between opus46 and sonnet46. The drop is not a scaling artefact — it reflects model-level differences in how tightly L2 extractions cluster in the embedding space. The *Results* section below documents that gradient and the distinct topical clusters each model surfaces.

## Methodology

### Input

- Three L2 graph artefacts from `docs/evals/l2_full_corpus_three_way.md`:
  - `data/derived/l2_structure/l2_graphs_full_opus46.jsonl` (sha256=`aa71bd40…`, 296 graphs)
  - `data/derived/l2_structure/l2_graphs_full_opus47.jsonl` (`579a516d…`, 306 graphs)
  - `data/derived/l2_structure/l2_graphs_full_sonnet46.jsonl` (`8a6b14a7…`, 282 graphs)
- Clusterable node types: `pain`, `expectation` (per `l3_cluster.py:clusterable_node_types`; `triggered_element`, `lost_value`, `workaround` excluded by contract).
- Encoder: `sentence-transformers/all-MiniLM-L6-v2` (384-dim, CPU-pinned, `normalize_embeddings=True`, seed=42, `model_weights_hash=15de32948ddab731`).

### Runs

| run_id | input graphs | input sha256 | N nodes | clusters | written_at |
|---|---|---|---|---|---|
| `l3-full-opus46` | l2_graphs_full_opus46 | `aa71bd40…` | 621 | 14 | 2026-04-22T22:00:12Z |
| `l3-full-opus47` | l2_graphs_full_opus47 | `579a516d…` | 528 | 10 | 2026-04-22T22:00:26Z |
| `l3-full-sonnet46` | l2_graphs_full_sonnet46 | `8a6b14a7…` | 480 | 7 | 2026-04-22T22:00:40Z |

Artefact SHA-256:

| run | clusters jsonl | centroids npy |
|---|---|---|
| opus46 | `2f0258d4…` | `bdaa235f…` |
| opus47 | `2cbcac1f…` | `578f63c6…` |
| sonnet46 | `223e0bf4…` | `51fc2970…` |

### Encoder and clustering environment

Per `*.provenance.json` (identical across all three runs):

- `sentence-transformers==3.4.1`, `torch==2.11.0`, `numpy==2.4.4`, `hdbscan==0.8.42`
- `python 3.12.13`, `macOS-26.4.1-arm64-arm-64bit`, device=`cpu`
- Hyperparameters: `min_cluster_size=5`, `metric=euclidean`, `cluster_selection_method=eom`, `kmeans_k=6` (fallback, not invoked), `seed=42`

Identical hyperparameters across all three runs. Divergence in output cluster structure attributes to input-distribution differences, not parameter drift.

## Results

### Cluster counts and size distributions

| Model | N nodes | Clusters | Sizes (desc) |
|---|---|---|---|
| opus46 | 621 | 14 | 46, 18, 13, 12, 12, 10, 9, 8, 7, 6, 6, 5, 5, 4 |
| opus47 | 528 | 10 | 19, 16, 12, 11, 10, 10, 9, 7, 6, 4 |
| sonnet46 | 480 | 7 | 32, 17, 8, 6, 6, 5, 3 |

HDBSCAN is density-based — nodes in sparse regions are assigned to the "noise" bucket rather than forced into a cluster. The sum of `member_review_ids` across output clusters (161 / 104 / 77) is a floor on "nodes in a dense pocket", not total coverage — residual nodes are HDBSCAN noise. Two small final clusters (sonnet46 cluster_01 with n=3, opus46 cluster_13 with n=4; opus47 cluster_00 with n=4) are smaller than `min_cluster_size=5` because HDBSCAN's condensation step can produce final clusters smaller than the density-estimation floor; this is expected behaviour, not a bug.

The largest cluster in every model is a regression-narrative cluster — representative quotes like *"used to love this app"*, *"used to be a great app"*, *"Terrible app"*. This is the Duolingo tentpole signal: the regression-from-good-state pattern dominates every density-based view of this corpus, regardless of upstream model.

### Per-model cluster inventories

**opus46 — 14 clusters.** Representative quotes are direct outputs from the clustering layer (3 shown per cluster, from the full 5-quote list).

| id | n | representative quotes | theme |
|---|---|---|---|
| 00 | 8 | "aprendí por mucho TIEMPO PARA NADA", "fico muito triste em ver no que se tornou", "sempre achando que esse aplicativo era ruim" | non-English regret / betrayal (ES/PT) |
| 01 | 5 | "I gave it 3 star", "I've reduced my review from 5 stars to 3", "having to give a 2 stars" | meta — star-rating mechanics |
| 02 | 7 | "my streak is not maintained", "thinking of going back with my streak", "unable to keep streaks going" | streak failures |
| 03 | 4 | "I keep getting it wrong", "you still get it wrong", "always wrong and give you the wrong words to use" | wrong answers / validation |
| 04 | 5 | "helping me to learn new languages", "just want to learn the language not learning to write it", "I thought more languages were there" | language coverage / didactic method |
| 05 | 10 | "I don't like it", "i do not like", "I dont like" | affect — general dislike |
| 06 | 9 | "used to be good", "Used to be so good", "used to be amazing" | affect — past-tense praise (regression) |
| 07 | 13 | "TERRIBLE", "terrible", "awful" | affect — strong negative |
| 08 | 12 | "Duolingo kind of sucks", "Duolingo is still often unusable", "Duolingo is getting progressively worse" | product-as-subject: worsening |
| 09 | 6 | "very disappointing", "very disappointed", "very disappointed" | affect — disappointment |
| 10 | 46 | "This used to be a great app", "this the worst app ever", "Terrible app" | **tentpole: regression + app-level** |
| 11 | 12 | "completing my lesson every day", "so many lessons I completed", "make the entire lesson correctly" | lesson completion |
| 12 | 18 | "annoying", "annoying", "annoying" | affect — annoyance |
| 13 | 6 | "Very frustrating", "very frustrating", "very frustrating" | affect — frustration |

**opus47 — 10 clusters.**

| id | n | representative quotes | theme |
|---|---|---|---|
| 00 | 4 | "aprendí por mucho TIEMPO", "mucho tiempo perdido", "o intuito de ser gratuito" | non-English regret / monetisation (ES/PT) |
| 01 | 7 | "is incorrect", "I am speaking but it says wrong", "I keep getting it wrong" | wrong answers / validation |
| 02 | 10 | "Duolingo kind of sucks", "Duolingo becomes mad", "duolingo is keep stopping" | product-as-subject: instability |
| 03 | 12 | "terrible", "awful", "awful" | affect — strong negative |
| 04 | 9 | "very disappointed", "very disappointed", "very disappointed" | affect — disappointment |
| 05 | 10 | "completing my lesson every day", "only do 1 lesson a day", "I completed a lesson" | lesson completion |
| 06 | 11 | "frustrating", "frustrating", "frustrating" | affect — frustration |
| 07 | 19 | "annoying", "annoying", "annoying" | affect — annoyance |
| 08 | 16 | "used to love this app", "used to love this app", "used to love this app" | **tentpole: regression** |
| 09 | 6 | "freezing all the time", "keeps freezing", "app freezes" | **freezing (unique to opus47)** |

**sonnet46 — 7 clusters.**

| id | n | representative quotes | theme |
|---|---|---|---|
| 00 | 6 | "duolingo that i know before", "Duolingo crashes before opening", "forced to buy Super Duolingo", "can't login to Duolingo", "needs to tell Duolingo to chill" | **junk drawer — regression + crashes + monetisation + access + affect, see below** |
| 01 | 3 | "learn chess", "I didn't find chess", "i can't seem to find how to play chess" | **chess feature discovery (unique to sonnet46)** |
| 02 | 6 | "very disappointing", "very disappointed", "very disappointed" | affect — disappointment |
| 03 | 8 | "Very frustrating", "very frustrating", "super frustrating" | affect — frustration |
| 04 | 17 | "annoying", "annoying", "annoying" | affect — annoyance |
| 05 | 32 | "used to be a great app", "I used to love this app", "I used to really love this app" | **tentpole: regression** |
| 06 | 5 | "I had completed the lesson", "completed my lesson in time", "completing my lesson every day" | lesson completion |

### Shared themes across models

| Theme | opus46 | opus47 | sonnet46 |
|---|---|---|---|
| "used to love / used to be good" regression | cluster_10 (46) + cluster_06 (9) | cluster_08 (16) | cluster_05 (32) |
| Affect — annoyance | cluster_12 (18) | cluster_07 (19) | cluster_04 (17) |
| Affect — frustration | cluster_13 (6) | cluster_06 (11) | cluster_03 (8) |
| Affect — disappointment | cluster_09 (6) | cluster_04 (9) | cluster_02 (6) |
| Lesson completion / progress | cluster_11 (12) | cluster_05 (10) | cluster_06 (5) |
| Wrong answers / validation | cluster_03 (4) | cluster_01 (7) | — |
| Product-as-subject (worsening / instability) | cluster_07 (13), cluster_08 (12) | cluster_02 (10), cluster_03 (12) | cluster_00 (6, mixed) |
| Non-English regret / monetisation | cluster_00 (8) | cluster_00 (4) | — |

Five affect-and-lesson themes recur across all three models; two additional themes recur across the two Opus models only (wrong answers, non-English regret). Cluster sizes rescale roughly with L2 node count. The two models with sparser L2 output (opus47, sonnet46) consolidate affect into fewer, larger clusters; opus46's richer extraction splits affect into separate *don't like* / *used to be good* / *unusable* / *terrible* clusters that the others absorb into nearby neighbours.

### Themes unique to one model

- **opus47 — "freezing" (cluster_09, n=6).** Technical-stability theme. Representative quotes: *"freezing all the time"*, *"keeps freezing"*, *"app freezes"*, *"it freezes all the time"*, *"freezing"*. Not found as a distinct cluster in opus46 or sonnet46 — either absent from their L2 pain-nodes or scattered below density threshold. Consistent with the L2 eval's observation that opus47's stricter extraction (36 `under_minimum_nodes` quarantines vs opus46's 16) produces cleaner topic separation: opus47 emits fewer but tighter pain nodes, which preserves low-volume technical signals that density-based clustering can resolve.
- **sonnet46 — "chess" (cluster_01, n=3).** Feature-discovery theme. Representative quotes: *"learn chess"*, *"I didn't find chess"*, *"i can't seem to find how to play chess"*, *"helps me alot in chess"*, *"I chose chess"*. Duolingo added a chess mode in mid-2025; this cluster surfaces users struggling to locate or access it. Neither opus model exposes this as a cluster — opus L1 classifiers routed these reviews differently, or opus L2 extraction assigned their nodes to different semantic neighbourhoods that crossed the noise threshold. Caveat: n=3 is below the 5-point `min_cluster_size` floor; HDBSCAN's condensation step produced the cluster from local density in the embedding, so any product claim based on n=3 remains weak.
- **opus46 — affect atomisation + meta and didactic clusters.** Rather than one missing cluster, opus46 *splits* the affect space into more pieces than the others (dislike / past-tense / unusable / terrible as separate clusters) and additionally surfaces two small clusters the other two collapse into noise: cluster_01 (star-rating meta, n=5) and cluster_04 (language coverage / didactics, n=5). Both are plausible pain themes but at the granularity margin — with opus47's or sonnet46's L2 sparsity they fall below HDBSCAN's density threshold.

### "Junk-drawer" behaviour in sonnet46

sonnet46's cluster_00 (n=6) illustrates the downstream cost of noisy L2 extraction. Its representative quotes span four unrelated topics: *"duolingo that i know before"* (regression), *"Duolingo crashes before opening"* (technical), *"forced to buy Super Duolingo"* (monetisation), *"can't login to Duolingo"* (access), *"needs to tell Duolingo to chill"* (affect). A coherent cluster should have one theme; this one has four. The predicted cascade from L2 hallucination rate (12 vs 4 for the opus models) into L3 density structure is visible here — noisy verbatim quotes produce embeddings whose nearest-neighbour structure does not correspond to a single pain theme, and HDBSCAN extracts them as a cluster because local density is real even if semantic coherence is not.

Decision: do not hand-fix. Document the junk-drawer as an L3 artefact of upstream L2 noise. An L4 audit can flag low-coherence clusters by measuring intra-cluster semantic variance (e.g. mean pairwise cosine distance across member embeddings) and route those for review — turning this into a measurable audit signal rather than an ad-hoc observation.

## Interpretation

This is the third layer in the three-way audit matrix. L1 disagreed on UX-relevance (401 / 404 / 411 per `l1_model_evaluation.md`). L2 produced graphs with distinct failure profiles (see *Behavioural profiles* in `l2_full_corpus_three_way.md`). L3 now shows the downstream shape of each L2 profile in the density structure:

- **Richer L2 extraction (opus46) → more, finer clusters.** Affect splits into seven named clusters, plus two small margin-density clusters (star-rating meta, language coverage). Useful if downstream product decisions want to distinguish annoyance from disappointment from frustration; redundant if all affect clusters feed the same product fix.
- **Stricter L2 extraction (opus47) → fewer, cleaner clusters, with at least one rare-but-real theme preserved.** The "freezing" cluster is the operational payoff of opus47's willingness to quarantine sparse reviews rather than pad them: quarantined noise does not pollute L3's density estimates, and low-volume technical signal stays resolvable.
- **Noisy L2 extraction (sonnet46) → fewest clusters, with one junk-drawer artefact.** The bulk of sonnet46's L3 output is fine — a tentpole regression cluster (32), the affect trio (17 / 8 / 6), a clean lesson-completion cluster, plus the unique chess feature-discovery cluster. But cluster_00 mixes four themes, which is the density-clustering layer's way of surfacing upstream extraction noise.

No claim above generalises beyond this corpus, this skill, and these three models. The purpose of the audit matrix is to make the trade-offs legible enough for a reviewer to pick per context — L1, L2, and L3 each have their own decision.

## Reproducing this document

L3 clustering is deterministic given (embedder, hyperparameters, input). No Claude API calls. Regenerate from tracked inputs:

```bash
uv run python -m auditable_design.layers.l3_cluster \
  --graphs data/derived/l2_structure/l2_graphs_full_opus46.jsonl \
  --output data/derived/l3_clusters/l3_clusters_full_opus46.jsonl \
  --centroids data/derived/l3_clusters/l3_centroids_full_opus46.npy \
  --run-id l3-full-opus46

uv run python -m auditable_design.layers.l3_cluster \
  --graphs data/derived/l2_structure/l2_graphs_full_opus47.jsonl \
  --output data/derived/l3_clusters/l3_clusters_full_opus47.jsonl \
  --centroids data/derived/l3_clusters/l3_centroids_full_opus47.npy \
  --run-id l3-full-opus47

uv run python -m auditable_design.layers.l3_cluster \
  --graphs data/derived/l2_structure/l2_graphs_full_sonnet46.jsonl \
  --output data/derived/l3_clusters/l3_clusters_full_sonnet46.jsonl \
  --centroids data/derived/l3_clusters/l3_centroids_full_sonnet46.npy \
  --run-id l3-full-sonnet46
```

Verify:

```bash
sha256sum \
  data/derived/l3_clusters/l3_clusters_full_opus46.jsonl \
  data/derived/l3_clusters/l3_clusters_full_opus47.jsonl \
  data/derived/l3_clusters/l3_clusters_full_sonnet46.jsonl \
  data/derived/l3_clusters/l3_centroids_full_opus46.npy \
  data/derived/l3_clusters/l3_centroids_full_opus47.npy \
  data/derived/l3_clusters/l3_centroids_full_sonnet46.npy
```

Expected:

| File | sha256 |
|---|---|
| `l3_clusters_full_opus46.jsonl` | `2f0258d4526432643a8230f74cf300961460a68f95d6640385acffb97f7739d7` |
| `l3_clusters_full_opus47.jsonl` | `2cbcac1fc2152612812e21a9174fd849480629a5da976e42c2cc9479ba271eff` |
| `l3_clusters_full_sonnet46.jsonl` | `223e0bf452f60cda6a5570ee01e2aee3de44e99e9ccaa5c6abb34b391c6ea14e` |
| `l3_centroids_full_opus46.npy` | `bdaa235f0ebc722a572b1f83b8ccbae236384a760e190afd16e4042fe08d5f95` |
| `l3_centroids_full_opus47.npy` | `578f63c6e09f432e80879db04370e6fc13bf947f7ef8263b4797006afe2b56c7` |
| `l3_centroids_full_sonnet46.npy` | `51fc2970e55bcad41458b9709ca499eba0220e0fb4a5a10331f35acea68fd380` |

Determinism caveat: reproduction is byte-identical if and only if the encoder environment matches (`sentence-transformers 3.4.1`, `torch 2.11.0`, device=cpu, model weights hash `15de32948ddab731`, `numpy 2.4.4`). A GPU run or a different torch build will produce different embeddings and therefore different cluster memberships; the replay-log contract in ADR-011 covers LLM outputs, not numeric reproducibility of the local encoder. See ADR-012 for the local-encoder-at-L3 rationale and the accepted non-portability of embeddings across hardware/library builds.

## What's next

- **ARCHITECTURE.md §4.4 update.** Replace the N=50 empirical note with a full-corpus summary pointing at this doc: HDBSCAN succeeds on all three models at full-corpus scale (N ≥ 480 input nodes); cluster counts 14 / 10 / 7; no KMeans fallback triggered. Closes the caveats paragraph currently left pointing to uncertainty.
- **L3b labelling layer** (`src/auditable_design/layers/l3b_label.py`, not yet implemented). All clusters above carry `label: "UNLABELED:cluster_NN"`. The next layer gives each cluster a short human-readable name via a one-shot Claude call on representative quotes; the three inventories in this document are usable as label-prompting input directly.
- **L4 cluster-coherence audit.** The junk-drawer observation in sonnet46 cluster_00 suggests a measurable L4 audit signal: intra-cluster embedding variance or mean pairwise cosine distance, per cluster. Low-coherence clusters should surface for review. Candidate for the first L4 audit module.
- **Cross-model cluster alignment.** Five themes recur across all three models (see *Shared themes*). A downstream audit could compute Jaccard over member review IDs or cosine similarity between centroids across models to formalise "these two clusters are the same theme seen by two models". Not required for the hackathon deliverable but is the natural bridge to a cross-model agreement score.
