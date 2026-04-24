# L6 priority-weight — matched-model eval

**Scope:** one reconciled verdict (`cluster_02` "Streak loss framing" Opus 4.6 L5 output) × three weighting models (Opus 4.6 / Sonnet 4.6 / Opus 4.7) × one modality (text-only — L6 consumes structured reconciled evidence, not UI). 3 cells. Each cell runs 2–3 Claude passes (double-pass baseline + optional third if per-dimension drift > 1). Closes ADR-009 L6 pilot action item.

**Status:** 3/3 scored, zero fallbacks, zero third passes triggered. All three cells landed on the first live run with no SKILL.md hardening iterations — a clean architectural outcome that contrasts with L5's multi-iteration contract evolution. Cross-model agreement on 4/5 dimensions (identical scores); divergence on `cognitive_cost` alone (Opus 4.6 → 9, Sonnet 4.6 + Opus 4.7 → 10).

## Purpose

L6 consumes one cluster's :class:`ReconciledVerdict` (from L5) and scores it on five priority dimensions: severity, reach, persistence, business_impact, cognitive_cost — each integer 0–10 with anchor calibration in SKILL.md. The module applies user-configurable meta-weights (default symmetric 0.2 × 5 = 1.0) to compute a single `weighted_total`, giving L7 a scalar priority per cluster.

The three questions this eval tries to answer on a single adversarial stimulus:

- **Do the three weighting models converge on the dimension scores?** If L6 is robust, a sev-9 cross-skill-corroborated cluster should look the same through Opus 4.6, Sonnet 4.6, and Opus 4.7.
- **Does the double-pass discipline detect within-model drift?** Opus 4.7's temperature-stripped non-determinism is the biggest risk; if pass 1 and pass 2 of the same model disagree by > 1 on any dimension, a third pass triggers and the median stabilises. We want to measure how often drift happens.
- **Which dimensions are model-stable and which are judgment-heavy?** Severity is mechanically derivable from the reconciled `rank_score` / `source_skills` fields; cognitive_cost requires translating Kahneman-style mechanism count into an anchor. Divergence should concentrate on the judgment-heavy dimensions.

## Executive summary

| metric | Opus 4.6 | Sonnet 4.6 | Opus 4.7 |
|---|---:|---:|---:|
| Status | scored | scored | scored |
| severity | 10 | 10 | 10 |
| reach | 9 | 9 | 9 |
| persistence | 8 | 8 | 8 |
| business_impact | 9 | 9 | 9 |
| cognitive_cost | 9 | **10** | **10** |
| weighted_total (symmetric weights) | 9.00 | **9.20** | **9.20** |
| validation_passes | 2 | 2 | 2 |
| validation_delta | 0.0 | 0.0 | 0.0 |
| third_pass_triggered | no | no | no |
| Input tokens total | 16 862 | 16 862 | 22 602 |
| Output tokens total | 1 201 | 1 332 | 1 548 |
| Skill_hash | `2e0764bf5b4c011f…` | `2e0764bf5b4c011f…` | `2e0764bf5b4c011f…` |

**Four observations:**

1. **Severity, reach, persistence, business_impact: unanimous across three models.** Four of five dimensions agree exactly. This is the cluster's robust priority reading — it is sev-10, hits 9/10 on reach, fires on 8/10 frequency, and has 9/10 business impact regardless of which weighting model you run.

2. **cognitive_cost is the only judgment-heavy dimension in this cluster.** Opus 4.6 scores 9 ("high"); Sonnet 4.6 and Opus 4.7 both score 10 ("severe"). The model rationales show the mechanism — Sonnet and Opus 4.7 count *"four compounding Kahneman mechanisms"* (loss aversion on sunk-cost streak, endowment exploitation, scarcity-timer System 2 suppression, asymmetric choice architecture) as anchor-10 territory; Opus 4.6 reads the same four mechanisms but anchors the sum at 9. Legitimate difference in threshold between "high" and "severe"; resolves to a 1-point numerical difference that moves `weighted_total` by 0.2 under symmetric weights.

3. **Zero within-model drift.** Every cell's two passes produced identical dimension vectors. This is a stronger consistency result than the SKILL.md "Two honest scorers" framing anticipated (which anticipated delta = 1 to be typical). The cluster is clear enough that the same model, same prompt, temperature=0 (or stripped on Opus 4.7) produces the same scores on re-invocation. Third pass never needed.

4. **Zero fallbacks.** Unlike L5's three-iteration evolution to land on a graph-primary contract that the model would reliably produce, L6's simpler output contract (`{dimensions, rationale, overall_note}` — 3 top-level keys, 5-int structured dimensions, 6 required rationale strings) landed on the first live run. The lean contract is the right design choice for this layer.

## Methodology

### Input

**Reconciled verdict:** `data/derived/l5_reconcile/l5_reconciled_cluster02_opus46.jsonl` — sha256 `181758aae71f57a7…`. Opus 4.6 L5 output for cluster_02; carries the reconciled verdict with:

- 7 `ranked_violations` (top three: `modal_excise__corroborated` at rs=45 from 5 skills, `channel_gap__corroborated` at rs=36 from 4 skills, `competing_calls_to_action__corroborated` at rs=28 from 4 skills)
- 2 `tensions`: Cooper × Kahneman on `efficiency_vs_safety`, Osterwalder × Kahneman on `conversion_vs_user_wellbeing`
- 0 `gaps`

**Cluster context:** `data/derived/l4_audit/audit_interaction_design/audit_interaction_design_input.jsonl` — cluster_02 shared fixture sha256 `dc6d981f…` (same as L4/L5 evals). 7 member review ids. Cluster label: *"Streak loss framing pressures users into mid-session purchase."*

### Skill

`skills/priority-weight/SKILL.md` v1.0 (single iteration; no hardening rounds). skill_hash `2e0764bf5b4c011f…` (identical across all three cells — the skill was not edited during the run).

Output contract:

```json
{
  "dimensions": {"severity":<int>, "reach":<int>, "persistence":<int>, "business_impact":<int>, "cognitive_cost":<int>},
  "rationale": {"severity":"...", "reach":"...", ...},  // 5 rationale strings
  "overall_note": "..."
}
```

Parser-enforced: 3 top-level keys exactly, 5 dimension integers in `[0, 10]`, 5 non-empty rationale strings, non-empty overall_note. No arithmetic validation (there is no arithmetic in the output — weights applied by module, not by model).

### Runs

All three via `scripts/smoke_l6_weight.py`, orchestrated by `scripts/run_l6_weight_matched.sh`. Text-only. Temperature pinned to 0.0 on Opus 4.6 and Sonnet 4.6; stripped on Opus 4.7 via `claude_client._omits_sampling_params`. `MAX_TOKENS=4096` per pass (per-pass output ~400 tokens; generous headroom).

Double-pass baseline discipline: every cell runs Claude twice on the same prompt. If any per-dimension delta exceeds `MAX_DIMENSION_DELTA=1`, a third pass triggers and the median across three passes is taken. In this eval: **zero third-pass triggers** — all three cells had delta=0 across all five dimensions on the first two passes.

Meta-weights: `DEFAULT_META_WEIGHTS` symmetric 0.2 per dimension, summing to 1.0. Weighted total: `sum(dim × weight)`.

## Results

### Dimension-by-dimension cross-model reading

| dim | Opus 4.6 | Sonnet 4.6 | Opus 4.7 | convergence |
|---|---:|---:|---:|---|
| severity | 10 | 10 | 10 | unanimous (sev-10 anchor: "cluster-defining crisis") |
| reach | 9 | 9 | 9 | unanimous (9 anchor: "effectively all active users on core loop") |
| persistence | 8 | 8 | 8 | unanimous (8 anchor: "fires on every instance of a frequent task") |
| business_impact | 9 | 9 | 9 | unanimous (9 anchor: "regulatory sniff-test risk, FTC-style complaint territory") |
| cognitive_cost | 9 | 10 | 10 | 2-of-3 at 10 ("severe — dark-pattern coercion stack"); Opus 4.6 at 9 ("high — loss framing + countdown + asymmetric weight") |

All three models anchored to the same evidence:

- **severity 10:** "Top ranked rank_score=45, 5-skill corroboration at sev-9" — mechanically derivable from the reconciled verdict's top entry.
- **reach 9:** "Core-loop surface every active learner hits on energy depletion" + "7 independent reviewers corroborate" — driven by cluster's `member_review_ids_count` and `ui_context`.
- **persistence 8:** "Fires every time energy depletes mid-lesson" — session-level but not literally unavoidable, landing at the anchor-8 band rather than 9.
- **business_impact 9:** "Two tensions on conversion_vs_user_wellbeing and efficiency_vs_safety + users explicitly calling the modal manipulative" — anchored by L5's tension output.
- **cognitive_cost 9 vs 10:** Sonnet 4.6 and Opus 4.7 both count "four compounding Kahneman mechanisms"; Opus 4.6 counts the same four but positions them at the "high" threshold rather than "severe."

### Weighted total

Under symmetric meta-weights (0.2 × 5 = 1.0):

| cell | dimension sum | weighted_total |
|---|---:|---:|
| Opus 4.6 | 45 | **9.00** |
| Sonnet 4.6 | 46 | **9.20** |
| Opus 4.7 | 46 | **9.20** |

The 0.2 difference between Opus 4.6 and the other two models is entirely attributable to the 1-point `cognitive_cost` divergence. Under a different weight profile (e.g. cognitive_cost = 0.4, others = 0.15) the gap would be 0.4; under a weight profile that zeroes cognitive_cost (e.g. severity = 0.5, reach = 0.2, others = 0.1) the cells would all tie at 9.00. The eval does not prescribe a weight profile; L6's job is to provide score vectors, L7's job is to decide which weights to apply.

### Within-model consistency

Every cell ran two passes. In every cell, both passes produced **identical dimension vectors** (delta = 0 on all 5 dims). No third pass triggered. This is a substantially cleaner result than the SKILL.md's "Two honest scorers" discussion anticipated — the skill allowed for delta = 1 as typical and delta > 1 as needing a third pass. In practice, on this cluster at temperature 0 (or stripped), the models re-produce the same scores deterministically.

**Interpretation:** cluster_02 is a *clear-cut* prioritisation case. The evidence is strong enough that model-level judgment variance collapses to zero on re-invocation. Clusters with noisier evidence may still surface within-model drift; this eval does not stress-test that regime.

### Rationale convergence (excerpt)

All three models used the same cluster-level summary phrasing in their `overall_note`:

- Opus 4.6: *"This cluster is an extreme outlier: five skills unanimously identify a full-viewport monetisation modal that weaponises loss aversion, scarcity timing…"*
- Sonnet 4.6: *"This cluster is a textbook dark-pattern stack: a sev-9 defect corroborated by every skill in the pipeline, deployed on the highest-reach surface…"*
- Opus 4.7: *"A core-loop defect where five independent skill frames converge on the same modal as the locus of a monetisation-driven dark pattern, with principle-level…"*

Three different surface phrasings of the same underlying reading: cross-skill unanimous, core-loop, dark-pattern-stack, weaponised. No reading is idiosyncratic; no reading misses the cluster's shape.

### Output-token cost

Per cell: ~400–500 output tokens per pass × 2 passes = ~800–1 000 tokens/cell. Far below `MAX_TOKENS=4096`. Total grid spend approximately: 16k–22k input × 3 cells × 2 passes ≈ 100k input tokens + ~4k output tokens; on Opus 4.6/4.7 pricing (~$5/$25 per million) that is ~$0.60–$1.20 for the grid, cheaper on Sonnet.

## Architecture observation

Unlike L5 — which went through four SKILL.md iterations (dual-representation → graph+mirror → auto-repair parser → graph-primary) to land a model-authorable contract — L6 landed on the first live run. The reason is architectural simplicity:

- **Small output surface.** 5 integers + 5 rationale strings + 1 overall_note. No nested graph. No arithmetic the model must compute.
- **Separate weights layer.** The model does not see meta-weights; the Python module applies them. This is a deliberate architectural choice (ARCHITECTURE.md §4.7 "meta-weights editable via UI") but it also removes a class of model-failure-modes (weights × scores arithmetic).
- **Anchor-calibrated semantic targets.** The SKILL.md gives explicit 0–2 / 3–4 / 5–6 / 7–8 / 9–10 anchors per dimension, grounded in cluster-level evidence. This is a narrower judgment space than L5's "figure out the cross-skill structure" — here the model translates evidence to anchor band, which models do well.

The lesson for L7 and L8 design: keep the model's output surface small and its judgment targets anchor-calibrated. Complex structural outputs (L5's graph) need multiple design iterations; compact score outputs (L6) do not.

## Caveats

- **One cluster only.** Everything here is cluster_02. Clusters with weaker cross-skill corroboration or higher ambiguity may surface within-model drift that this eval does not exercise.
- **Opus 4.6 L5 input only.** The reconciled verdict feeding L6 is Opus 4.6's L5 output. A different L5 model's reconciled verdict might produce slightly different L6 scores because the evidence presented to L6 would be framed differently. L5 itself showed cross-model variance (different top-ranked entries across Opus 4.6 / Sonnet / Opus 4.7 per L5 matched eval); L6 is one level downstream.
- **Symmetric default weights are a hackathon convenience.** The actual product UI will let users set weights per RunContext. The 0.2 × 5 default here is a neutral baseline for eval purposes; it does not represent a considered product recommendation.
- **Zero third-pass triggers on this cluster is strong but not generalisable.** The cluster is unusually unambiguous (textbook-dark-pattern, 5-skill corroboration). Full-corpus runs will encounter clusters with genuinely ambiguous evidence; a third-pass rate of 10–30% across a mixed corpus is the SKILL.md-anticipated regime.
- **cognitive_cost is the eval's one model-divergent dimension.** If L7 or the UI places heavy weight on cognitive_cost, the 1-point difference between Opus 4.6 and the other two models becomes load-bearing. Running L6 with two different models and comparing would let a reviewer catch this class of disagreement; the `validation_passes_histogram` in provenance surfaces it.

## Reproducing

```
# Run the grid (requires ANTHROPIC_API_KEY):
bash scripts/run_l6_weight_matched.sh

# Force-rerun:
bash scripts/run_l6_weight_matched.sh --all

# Single cell:
uv run python scripts/smoke_l6_weight.py --model claude-sonnet-4-6

# Inspect:
ls -1 data/derived/l6_weight/*.provenance.json

# Tests (80/80 passing):
uv run pytest tests/test_l6_weight.py -v
```

Module: `src/auditable_design/layers/l6_weight.py`. Skill: `skills/priority-weight/SKILL.md` v1.0 (`skill_hash=2e0764bf5b4c011f…`). Expected spend: ~$0.30–0.60 across three cells.

## Closing

ADR-009's L6 pilot action item closed. The eval characterises three-model convergence on a clear-cut cluster (4/5 dimensions unanimous, 1/5 differing by 1 point) with perfect within-model stability (delta=0 across all cells). Full-corpus runs will stress-test the regime this single-cluster eval does not cover — weaker-evidence clusters where the double-pass discipline may actually trigger the third pass that this run did not need. For a hackathon-scale L6 → L7 handoff, the result is production-ready.
