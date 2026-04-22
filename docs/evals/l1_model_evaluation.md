# L1 classifier — model evaluation

**Date:** 2026-04-22
**Related:** ADR-009 (decision record), ADR-011 (replay log contract), `src/auditable_design/layers/l1_classify.py`
**Decision:** Opus 4.6 canonical; Sonnet 4.6 and Opus 4.7 retained as shadow evaluation runs for future reference.

## Purpose

This document is the evidence-store companion to ADR-009's L1 pilot findings section. ADR-009 records *what was decided and why*; this file records *the full data and method* so a reviewer can reconstruct the analysis. Where ADR-009 lists top-line triad metrics, this file lists every number, the sources behind them, and the second-order questions the data answered.

## Executive summary

Three Claude models were evaluated on the L1 per-review UX-classifier task against a 20-row human-gold pilot sample and, separately, on the full N=600 corpus without gold. Opus 4.6 was selected as the canonical model for the full pipeline run based on highest mean Jaccard vs gold (0.955), strongest confidence calibration (+0.057), and no active end-of-life exposure within the deliverable window (deadline 2026-04-26; Opus 4.6 EOL 2026-06-15). The N=600 shadow runs confirmed the decision and also produced two findings the pilot was too small to surface: (a) inter-model Cohen's kappa is in the 0.89–0.92 range, not 1.000 as the pilot's small sample suggested, and (b) Opus 4.7 systematically overuses the `interface_other` tag (solo-tagged 52/600 reviews vs 3 and 7 for the other two models).

## Methodology

### Sample

- *Pilot:* 20 reviews, stratified 12 low-star (1–3) + 8 high-star (4–5), `seed=42`. Manually gold-labelled by the author in `data/eval/l1_gold.csv`. Gold corrected reactively after pilot v1 surfaced two labelling errors (see "Pilot iteration" below).
- *Full-N:* 600 reviews from `data/raw/corpus.jsonl` (`sha256=a1ed84d0…`). No gold at this scale; inter-model agreement and triangle-consensus used as quality proxies.

### Triad (per-run quality gate)

A model passes L1 if it clears all three thresholds against gold on the pilot:

- `is_ux_relevant` accuracy ≥ 0.85
- mean Jaccard on `rubric_tags` ≥ 0.60 (empty-∩-empty counted as 1.0)
- confidence delta (mean confidence on correct predictions minus mean on incorrect) > 0.0

### Metrics

- *Accuracy:* fraction of reviews where predicted `is_ux_relevant` matches gold.
- *Jaccard:* `|a ∩ b| / |a ∪ b|` on tag sets; `(∅, ∅) → 1.0` (two labellers agreeing "no tags" is perfect agreement, not undefined).
- *Cohen's kappa:* observed agreement on `is_ux_relevant` corrected for chance agreement given class priors. Relevant here because the corpus is imbalanced (~67% UX-relevant), so naïve agreement rates look high by default.
- *Confidence delta:* `mean(conf | correct) − mean(conf | incorrect)`. A minimal calibration signal; we require `> 0` but do not threshold further.
- *Triangle consensus (N=600 only):* for each review, whether all three models agree on `is_ux_relevant`. Used as a gold-proxy when gold is unavailable at scale.

### Tools

- `scripts/compare_models.py` — two-way comparison vs gold (pilot).
- Ad-hoc N=600 script (reproducible from this document's numbers) — three-way comparison, triangle consensus, tag-distribution analysis.

### Reproducibility

Every model call is cached in `data/cache/responses.jsonl` keyed on `sha256(skill_id, skill_hash, model, temperature, max_tokens, system, user)`. Re-running any of the three pipelines with `--mode replay` returns byte-identical outputs without contacting the API. See ADR-011 for the replay contract.

## Pilot results (N=20)

### Pilot iteration (v1 → v2)

Pilot v1 (both Sonnet 4.6 and Opus 4.6 on original gold + original prompt) surfaced two errors that belonged to the *gold*, not the models:

- Row `4c1fd6` ("app keeps crashing on lesson completion") was gold-labelled `is_ux_relevant=0`, but both models labelled it `1` with high confidence. On review the models were right: a lesson-completion bug is a core-loop UX issue. Gold flipped to `1`.
- Row `5d38e8` ("I told them to give me a reminder and they didn't") was missing the `notifications` tag in gold. The reviewer's complaint is explicitly about a notification promise that wasn't kept. Gold got `notifications` added.

The prompt was also extended with a short "Tag usage notes" paragraph to disambiguate two taxonomy edges both models had struggled with: implicit `feature_removal` (when the user describes a new system replacing an old one — hearts → energy — without using the word "removed"), and narrow `off_topic` (in-product feature mentions like "chess course" are *on*-topic even when the feature isn't language-learning).

These fixes produced prompt v2 (`skill_hash=b5325779…`), against which all three models were evaluated.

### v2 results vs gold (N=20)

| Model | is_ux acc | mean Jaccard | conf delta | run_id |
|---|---|---|---|---|
| Sonnet 4.6 | 0.850 | 0.905 | +0.032 | `l1-pilot-sonnet-20-v2` |
| Opus 4.6 | 0.850 | **0.955** | +0.057 | `l1-pilot-opus46-20-v2` |
| Opus 4.7 | 0.850 | 0.863 | +0.059 | `l1-pilot-opus47-20-v2` |

All three clear the triad. Pairwise Cohen's kappa on `is_ux_relevant` was **1.000 for every pair** at N=20 — an encouraging number, but small-sample-size dependent (see full-N results).

### Pilot disagreement drill-down

Opus 4.6 and Opus 4.7 disagreed on 3/20 reviews; every disagreement was a tag *substitution* rather than an add/drop:

| review_id | Opus 4.6 | Opus 4.7 | gold | closer to gold |
|---|---|---|---|---|
| `07d0c087` | `ads, feature_removal, hearts_streak` | `feature_removal, hearts_streak` | same as 4.6 | Opus 4.6 |
| `f9267ace` | `feature_removal, hearts_streak, paywall` | `bug, feature_removal, hearts_streak` | same as 4.6 | Opus 4.6 |
| `bf4856ee` | `bug, content_quality` | `interface_other` | `bug, content_quality` (is_ux=0) | Opus 4.6 |

On all three, Opus 4.7 moved away from gold. The third example (`bf4856ee`) is notable — Opus 4.7 collapsed two specific tags into a single generic `interface_other`. This pattern recurred at full scale.

## Full-N results (N=600)

### Per-model baselines

| Model | is_ux=1 | %UX | mean conf | mean tags/UX | top-3 tags |
|---|---|---|---|---|---|
| Opus 4.6 | 404 | 67.3% | 0.850 | 2.05 | `hearts_streak` (157), `bug` (137), `interface_other` (115) |
| Sonnet 4.6 | 401 | 66.8% | 0.860 | 2.08 | `hearts_streak` (184), `bug` (147), `content_quality` (111) |
| Opus 4.7 | 411 | 68.5% | 0.840 | 2.05 | **`interface_other` (169)**, `hearts_streak` (146), `bug` (129) |

Top-tag order matches intuition for two of three models (Duolingo reviews complain about hearts/energy and bugs first). Opus 4.7 is the exception: `interface_other` leads.

### Inter-model pairwise agreement

| Pair | κ on is_ux | mean Jaccard | disagree on is_ux | disagree on tags | either |
|---|---|---|---|---|---|
| Sonnet 4.6 ↔ Opus 4.6 | 0.913 | 0.880 | 23 (3.8%) | 129 (21.5%) | 138 (23.0%) |
| Sonnet 4.6 ↔ Opus 4.7 | 0.916 | 0.824 | 22 (3.7%) | 190 (31.7%) | 197 (32.8%) |
| Opus 4.6 ↔ Opus 4.7 | **0.889** | 0.856 | 29 (4.8%) | 153 (25.5%) | 162 (27.0%) |

Notable: **Opus 4.6 and Opus 4.7 — same model family — disagree more on `is_ux_relevant` than either does with Sonnet** (29 flips vs 23 / 22). The 4.6 → 4.7 transition moved the boundary, not just the tag vocabulary. This is not visible in the pilot (κ=1.000 everywhere at N=20).

### Triangle consensus

| | count | % |
|---|---|---|
| All three agree on `is_ux_relevant` | 563 | 93.8% |
| All three assign identical tag set | 383 | 63.8% |
| Mean of pairwise Jaccard, per review | — | 0.853 |

Binary decision is stable: **94% of the corpus gets the same UX/non-UX label from all three models**. Tag *granularity*, however, is less stable — about a third of reviews receive different tag sets across models.

### Confidence is a consensus signal

| Model | mean conf on 3-way agree | mean conf on any disagreement | delta |
|---|---|---|---|
| Opus 4.6 | 0.862 | 0.655 | **+0.207** |
| Sonnet 4.6 | 0.870 | 0.707 | +0.163 |
| Opus 4.7 | 0.852 | 0.658 | +0.194 |

All three models report markedly lower confidence on reviews where inter-model agreement breaks. This is a useful property: *the classifier self-reports uncertainty on the same reviews that are objectively harder.* Opus 4.6 has the sharpest signal (0.207) — another data point in its favour for downstream layers that weight evidence by confidence.

### `interface_other` overuse in Opus 4.7

The pilot showed a single case where Opus 4.7 collapsed `bug + content_quality` into `interface_other`. At full scale this is a pattern, not an anomaly:

| Model | Solo `interface_other` (other two don't tag it) |
|---|---|
| Opus 4.7 | **52** |
| Sonnet 4.6 | 7 |
| Opus 4.6 | 3 |

Opus 4.7 uses `interface_other` *as a sole dissenter* 52 / 600 = 8.7% of the corpus — an order of magnitude more than either other model. Combined with its overall tag-distribution (`interface_other` is its most-used tag at full scale, unlike the other two), this is a systematic shift in tagging behaviour rather than noise.

The practical consequence for downstream layers: a pipeline using Opus 4.7 at L1 would present L2 with an inflated `interface_other` signal, likely crowding out more specific tags like `bug` or `content_quality` that L2 aggregates into per-cluster evidence. This is a non-trivial reason to prefer 4.6 for L1 beyond the pilot's triad-metric differences.

### `is_ux_relevant` disagreement directionality

When exactly one model labels a review UX-relevant while the other two don't:

| Model | Solo `is_ux=1` |
|---|---|
| Opus 4.7 | 25 |
| Opus 4.6 | 18 |
| Sonnet 4.6 | 15 |

Opus 4.7 is more permissive — it's the one most often labelling things as UX-relevant alone. Consistent with its higher base rate (68.5% vs ~67%).

### Triad sanity vs pilot gold on the 20-review overlap

Because the pilot sample is drawn from the same corpus, those 20 review_ids appear in every N=600 run. Re-computing the triad on the overlap cross-checks that nothing changed between pilot and full-N (same skill_hash, same prompt, same corpus → should give the same numbers):

| Model | is_ux acc | mean Jaccard | conf delta |
|---|---|---|---|
| Opus 4.6 | 0.850 | 0.955 | +0.057 |
| Sonnet 4.6 | 0.850 | 0.905 | +0.032 |
| Opus 4.7 | 0.850 | 0.863 | +0.059 |

Bit-identical to the pilot v2 numbers. The replay cache and `key_hash` scheme (ADR-011) behave as specified — the same input under the same skill_hash serves the same output whether computed once or many times.

## Key findings

1. *Pilot N=20 overstated inter-model agreement.* Kappa=1.000 at pilot scale looked decisive; at N=600 the same pairs sit at 0.89–0.92 — still high, but showing real disagreement on ~4% of decisions.
2. *Opus 4.6 and 4.7 are not interchangeable for L1.* Inter-model κ=0.889 (same family) is actually lower than either model's κ with Sonnet. The transition altered classification boundaries, not only the tag vocabulary.
3. *Tag-substitution pattern in Opus 4.7 is statistically confirmed.* 52 solo-`interface_other` cases on N=600 — roughly an order of magnitude more than the other two models. The pilot's single example was representative.
4. *Confidence is a useful proxy for difficulty.* All three models report substantially lower confidence on the reviews where inter-model consensus breaks (delta up to +0.207 for Opus 4.6). Downstream layers can treat `confidence < ~0.7` as a heuristic flag for harder cases.
5. *Pipeline reproducibility confirmed end-to-end.* The 20-row pilot overlap produced bit-identical triad numbers under full-N runs, validating both the replay log contract and the skill_hash / key_hash design.

## Decision

Opus 4.6 selected for canonical L1 on full N=600. Rationale recorded in **ADR-009 → "L1 pilot findings (2026-04-22)"**. This evaluation document extends the pilot evidence with full-N data and with the triangle-consensus analysis — it does not change the decision; it confirms it and raises the confidence in the call beyond what a 20-row pilot could support alone.

Sonnet 4.6 and Opus 4.7 runs are retained as *shadow* artefacts. They're not inputs to L2 but are preserved in `data/derived/l1_classification/` with matching meta sidecars, so any future claim about L1 model behaviour can be re-verified without an API key.

## Limitations

- *No gold at N=600.* Inter-model agreement is a proxy, not ground truth. A review where all three models agree could still be collectively wrong. Triangle consensus is suggestive, not proof.
- *Single prompt version.* Prompt v2 was not further iterated on full-N findings. If the `interface_other` overuse in Opus 4.7 is partly prompt-induced (e.g. some taxonomy edge in the rubric drives 4.7 there), a prompt v3 might reduce it. We did not run that experiment.
- *Confidence self-report.* The `classifier_confidence` field is the model's own stated confidence — it's not an independently calibrated probability. The consensus-agreement pattern is suggestive of real calibration, but a reliability-diagram analysis would be a proper next step.
- *Opus 4.6 EOL 2026-06-15.* The canonical model will not be callable live past this date. Reviewers after that date must use `--mode replay`, which reads from `data/cache/responses.jsonl`. This is the intended post-deadline path per ADR-011.

## Evidence pointers

### Input
- Corpus: `data/raw/corpus.jsonl` — `sha256=a1ed84d0c31ac7fffb4f54a9b10745a55b056129aad04124bd75dc24c207a672`
- Gold: `data/eval/l1_gold.csv` — 20 rows, post-v2 corrections applied
- Prompt: `src/auditable_design/layers/l1_classify.py` — `skill_hash=b5325779f762e545a383acf1e99b4a820dd08e14374730cdedf3853f5bb42909`

### Outputs
- `data/derived/l1_classification/l1_full_opus46.jsonl` — canonical (feeds L2)
- `data/derived/l1_classification/l1_eval_sonnet46.jsonl` — shadow
- `data/derived/l1_classification/l1_eval_opus47.jsonl` — shadow

Each with `.meta.json` sidecar carrying `artifact_sha256`, `run_id`, `code_version`, and input/skill hashes.

### Tools
- `scripts/compare_models.py` — two-way vs-gold comparison (pilot)
- `data/cache/responses.jsonl` — full replay log (all three full-N runs committed)

### Pilot runs (N=20, v2 prompt)
- `l1-pilot-sonnet-20-v2`
- `l1-pilot-opus46-20-v2`
- `l1-pilot-opus47-20-v2`

### Full runs (N=600, v2 prompt)
- `l1-full-opus46`
- `l1-eval-sonnet46`
- `l1-eval-opus47`
