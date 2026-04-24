# L10 evolution log — matched assembly

Final pipeline assembly. L10 takes the per-layer outputs already
written by L3b → L5 → L7 → L8 (thin spine) → L8 (loop) and stitches
them into a single DAG of :class:`EvolutionNode` /
:class:`EvolutionEdge` records. Pure-Python, no Claude calls — this
is structural assembly over artifacts the pipeline already produced.

The DAG is the backbone the React demo traverses: "click any review
→ follow to the cluster it informed → to the L5 verdict reconciled
from that cluster → to the L7 decision made in response → to the L8
iterations that refined the decision → to the loop's final
accepted parent."

## Scope and grid

- **Cluster with full-spine coverage:** `cluster_02` (Duolingo
  streak-loss framing / mid-session purchase pressure).
- **L3b universe:** 10 clusters spanning the full Duolingo corpus
  window.
- **L10 is structural, not generative** — there is no model
  selection at this layer. A "matched eval" for L10 means
  swapping which upstream model file is consumed and re-checking
  DAG counts.

## Schema

Nodes (`EvolutionKind`):

| Kind         | Source                      | `node_id`                          | `payload_ref`                      |
|--------------|-----------------------------|------------------------------------|------------------------------------|
| `review`     | L3b clusters' member lists  | review_id (e.g. `b03cb96a0fc0a3…`) | L3b labeled-clusters jsonl         |
| `cluster`    | L3b                         | `cluster_02`                       | L3b labeled-clusters jsonl         |
| `verdict`    | L5                          | `verdict__cluster_02`              | L5 reconciled jsonl                |
| `decision`   | L7                          | `decision__cluster_02__1`          | L7 decisions jsonl                 |
| `iteration`  | L8 thin spine + L8 loop     | `iteration__cluster_02__{NN}`      | L8 iterations jsonl (spine OR loop)|

Edges (`EvolutionRelation`):

| From         | To         | Relation         | Source                              |
|--------------|------------|------------------|-------------------------------------|
| review       | cluster    | `informs`        | L3b `member_review_ids`             |
| cluster      | verdict    | `reconciled_into`| L5 cluster_id field                 |
| verdict      | decision   | `decided_as`     | L7 decision_id convention           |
| decision     | iter 0     | `iterated_to`    | L7 → L8 baseline bridge             |
| iteration    | iteration  | `iterated_to`    | `parent_iteration_id` linkage       |

## Headline results (opus47 input)

```
107 nodes (92 review + 10 cluster + 1 verdict + 1 decision + 3 iteration)
109 edges (104 informs + 1 reconciled_into + 1 decided_as + 3 iterated_to)
```

## Per-cluster breakdown (L3b labelling)

All 10 clusters contribute review→cluster edges; only `cluster_02`
carries downstream structure in this snapshot.

| Cluster     | Members | Label                                                         |
|-------------|---------|---------------------------------------------------------------|
| cluster_00  |   4     | Excessive monetization of previously free features            |
| cluster_01  |   7     | Voice recognition marks correct answers wrong                 |
| **cluster_02**|  **10** | **Mixed complaints (streak-loss / mid-session purchase)** ← pilot |
| cluster_03  |  12     | Mixed complaints                                              |
| cluster_04  |   9     | Mixed complaints                                              |
| cluster_05  |  10     | Daily lesson completion limits                                |
| cluster_06  |  11     | Mixed complaints                                              |
| cluster_07  |  19     | Mixed complaints                                              |
| cluster_08  |  16     | App quality declined over time                                |
| cluster_09  |   6     | App freezes repeatedly                                        |
| **Total members** | **104** | (matches 104 `informs` edges)                          |

Unique reviews across all 10 clusters: 92 (reviews can appear in
multiple clusters; dedup at node creation produces 92 review nodes).

## Cluster_02 evidence — 10 reviews carried through the pipeline

The `cluster_02` review IDs (source: L3b `member_review_ids`):

```
1420536260739c86931e38df59f9777fd21ba274
16dad76e9380b4d6510d7b5a04cdd6d40cf86d91
38a3dae6a762cc47221354d2c03d8c13c300d2dd
428fd25e9fa6cedd399ad8abcd5cbf24ce2f3d55
502824bc65438dcb24f8cd8511394ac13590b958
b03cb96a0fc0a3b999973b52d771e904cc8c3d2a
b65b2ba944c88c03d00536e1e8c55f4eebdcf87c
c363ca9d2835c99375bb6ef93a205dc52b26700e
eb02469980cb56f54e0699abc867e7b9d888ae45
fb21aec1163559a79b77f6322dff2917d3ec9296
```

Discrepancy to note: iterations' `informing_review_ids` carry only
7 of the 10 reviews. The iteration module intentionally deduplicates
against the 7 reviews that survived L4 audit sampling (L4 subsampled
representative reviews to keep prompt length bounded on Sonnet).
The L10 DAG preserves all 10 review → cluster edges because the
cluster **was** informed by all 10 even though only 7 became
per-iteration evidence.

## L5 reconciled verdict — 7 heuristics that drive everything downstream

One verdict, one cluster, 7 violations scored on ADR-008 anchored
severity. This list is the **invariant** across L7, L8, L8-loop, and
the B1 re-audit — every scoring step below compares against it:

| Heuristic slug                                                       | Severity | Violation digest                                                      |
|----------------------------------------------------------------------|----------|-----------------------------------------------------------------------|
| `modal_excise__corroborated`                                         | 9        | Full-viewport modal mid-lesson blocks the user's path                 |
| `channel_gap__corroborated`                                          | 9        | No free recovery affordance; no undo on destructive streak loss       |
| `competing_calls_to_action__corroborated`                            | 7        | Three paths at unequal visual weight; confirm-shaming dismiss link    |
| `cr_undermined_by_r_dollar__corroborated`                            | 9        | Revenue capture overrides "free, fun, effective" VP                   |
| `deceptive_feedback__scarcity_timer_suppression__timing_adjustable`  | 7        | Midnight-countdown loss framing suppresses System 2                   |
| `vp_cs_mismatch`                                                     | 9        | VP targets free-lesson expectation; CS reveals paid-wall              |
| `ego_depletion_mid_task`                                             | 7        | Decision forced at maximum cognitive fatigue                          |
| **Sum**                                                              | **57**   | (iteration 0 baseline; every downstream stage compared against this)  |

L5 also logged 2 skill tensions; they inform `reasoning` fields in
the reconciled verdict but do not appear as separate DAG nodes.

## Iteration chain — cluster_02, opus47, Tchebycheff loop

Three `iteration` nodes in the opus47 snapshot:

| Iteration | Parent   | Source         | Status   | Scored sum | Δ vs parent |
|-----------|----------|----------------|----------|------------|-------------|
| iter 00   | —        | L8 thin spine  | baseline | 57         | —           |
| iter 01   | iter 00  | L8 thin spine  | accepted | 11         | −46         |
| iter 02   | iter 01  | L8 loop        | accepted | 0          | −11         |

Full-path traceability from iter 02 back to evidence:

```
iteration__cluster_02__02                       (kind=iteration)
  └─ iterated_to ← iteration__cluster_02__01     (kind=iteration)
     └─ iterated_to ← iteration__cluster_02__00  (kind=iteration, baseline)
        └─ iterated_to ← decision__cluster_02__1 (kind=decision)
           └─ decided_as ← verdict__cluster_02   (kind=verdict)
              └─ reconciled_into ← cluster_02    (kind=cluster)
                 └─ informs ← 1420536260739c…   (kind=review)
                 └─ informs ← 16dad76e9380b4…   (kind=review)
                 ... (10 reviews total for cluster_02)
```

This is the traceability claim the pitch makes operational: **every
design decision has a typed path back to an audited user
complaint**.

## Edge accounting (why 109)

| Relation          | Count | Derivation                                                   |
|-------------------|-------|--------------------------------------------------------------|
| `informs`         | 104   | Σ member_review_ids per cluster across 10 clusters           |
| `reconciled_into` |   1   | cluster_02 → verdict__cluster_02 (only cluster with L5 done) |
| `decided_as`      |   1   | verdict__cluster_02 → decision__cluster_02__1                |
| `iterated_to`     |   3   | decision→iter00 + iter00→iter01 + iter01→iter02              |
| **Total**         | **109** |                                                           |

Note the deduplication: a review_id appearing in two clusters
produces **two** `informs` edges (once per cluster); the node for
that review is singleton. There are no duplicate edges within a
cluster.

## Cross-validation guarantees

The assembler raises `RuntimeError` rather than silently producing a
broken DAG if:

1. A reconciled verdict references a `cluster_id` not present in
   L3b.
2. An L7 decision's `decision_id` decodes to a `cluster_id` not in
   L3b.
3. An iteration's `iteration_id` decodes to a `cluster_id` not in
   L3b.
4. An iteration's `parent_iteration_id` points at an iteration not
   in the loaded set.

Edges are deduplicated on `(src, relation, dst)`; self-loops are
suppressed at the evaluator level. 21 unit tests cover these
invariants (`tests/test_l10_evolution.py`).

## How matched-model inputs change the DAG

L10 itself is deterministic; the DAG shape depends on which
upstream model files the operator passes. Three combinations the
spine supports:

| Upstream model | L8 spine iter-1 sum | L8 loop iter-2+ count         | Total iteration nodes |
|----------------|---------------------|-------------------------------|-----------------------|
| opus46         | 9                   | 1 (accepted, converged)       | 3                     |
| sonnet46       | 11                  | 3 (iter 02 acc, iter 03/04 rej) | 5                     |
| opus47         | 11                  | 1 (accepted, converged)       | 3                     |

Sonnet 4.6 produces the largest DAG because its loop failed to
converge — the rejected iter 03 and iter 04 nodes stay in the
graph (as dead-ends) so the audit trail preserves every attempt.
This is deliberate: the evolution log is **not** a sanitised final
pitch; it is a reviewable record including misses.

## Limitations

- **`element` kind not emitted.** The schema reserves `element` for
  individual design elements inside an iteration (buttons, copy
  strings, etc). The current iteration artifacts are prose
  paragraphs, not component trees — surfacing elements would need a
  deeper L9 render that decomposes the snapshot into typed UI
  chunks. Out of scope; demo Timeline displays the prose directly.
- **Baseline B1 not in the DAG.** B1 lives in
  `data/derived/baseline_b1/` as a flat iteration-shaped record;
  it could plausibly be added as an `iteration` node with a new
  `EvolutionRelation` like `compared_against`. Deferred — the
  demo's CompareView reads baselines.json directly, keeping the
  audit DAG about the auditable pipeline itself.
- **Priority (L6) is not a node.** L6 produces a weighted scalar per
  cluster, not a standalone entity — its effect surfaces as
  metadata on the decision, not as a separate DAG node.
- **9 out of 10 clusters are dead-ended at L3b.** Thin-spine
  scope. The DAG honestly shows that: `informs` edges from every
  cluster's reviews are present, but only `cluster_02` has
  `reconciled_into` onwards. The other clusters' `informs` edges
  are there so the UI can surface "we saw you; this complaint was
  clustered but not yet routed through the audit pipeline."

## Artifacts

- Module: `src/auditable_design/layers/l10_evolution.py` (436 lines)
- Tests: `tests/test_l10_evolution.py` (21 tests, all green)
- Outputs:
  - `data/derived/l10_evolution/evolution_nodes.jsonl`
  - `data/derived/l10_evolution/evolution_edges.jsonl`
  - `data/derived/l10_evolution/evolution.provenance.json`
