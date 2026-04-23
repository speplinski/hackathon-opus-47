# L3b cluster labelling — matched-model per-pipeline eval

**Date:** 2026-04-23
**Related:** ADR-010 (adversarial-input discipline), ADR-011 (replay log contract), `docs/evals/l3b_full_corpus_three_way.md` (Haiku baseline), `docs/evals/l3_full_corpus_three_way.md` (L3 input), `skills/label-cluster/SKILL.md`, `skills/label-cluster/rubric.md`, `src/auditable_design/layers/l3b_label.py`, `scripts/run_l3b_matched.sh`
**Status:** Empirical record. Companion to the Haiku-baseline eval. Each pipeline branch is labelled here by the model that produced its upstream L1/L2 output (opus46→Opus 4.6, opus47→Opus 4.7, sonnet46→Sonnet 4.6), preserving per-branch model consistency end-to-end. Enables a 4-way labeller comparison: Haiku vs Opus 4.6 vs Opus 4.7 vs Sonnet 4.6 on the same three L3 inputs.

## Purpose

The Haiku baseline (`l3b_full_corpus_three_way.md`) used one labeller across three inputs, isolating *input* divergence. This eval holds the labeller-model paired with each pipeline's upstream model, answering a different question: **does the `Mixed complaints` rate and label specificity depend on the labeller?** If yes, then "how many Mixed clusters" is not a property of the cluster inventory — it is a property of the labeller's willingness to commit to a theme. That changes what the downstream L4 audit is actually measuring.

The second purpose is a rubric-adherence check. The rubric at `skills/label-cluster/rubric.md` specifies a three-tier specificity ladder with explicit guidance to prefer `Mixed complaints` over a weak affect-paraphrase (tier 3, "emit sparingly"). Running four different models against the same rubric surfaces where the rubric holds and where it drifts.

## Executive summary

| Labeller | Input | Clusters | Themed | Mixed | Mixed % | Tracker spend (USD) |
|---|---|---|---|---|---|---|
| Haiku 4.5 (baseline) | opus46 | 14 | 6 | 8 | 57% | $0.0198 |
| Haiku 4.5 (baseline) | opus47 | 10 | 5 | 5 | 50% | $0.0138 |
| Haiku 4.5 (baseline) | sonnet46 | 7 | 3 | 4 | 57% | $0.0081 |
| **Opus 4.6 (matched)** | opus46 | 14 | 13 | 1 | **7%** | $0.2557 |
| **Opus 4.7 (matched)** | opus47 | 10 | 7 | 3 | **30%** | $0.2472 |
| **Sonnet 4.6 (matched)** | sonnet46 | 7 | 3 | 4 | **57%** | $0.0256 |
| **Baseline total (Haiku across all 3)** | — | 31 | 14 | 17 | 55% | $0.0417 |
| **Matched total (3 models, 3 inputs)** | — | 31 | 23 | 8 | 26% | $0.5285 |

Mixed rate is **not a property of the cluster inventory** — it is dominated by labeller choice. Holding the 31 L3 clusters fixed:

- Opus 4.6 emits `Mixed` once (the ES/PT bilingual cluster). The other 13 clusters all receive a theme label — but 6 of those are *"Generic X"* / *"General X"* affect paraphrases that the rubric explicitly asks to avoid.
- Opus 4.7 emits `Mixed` three times, and interestingly twice *where Haiku committed to a theme* (`Excessive monetization` → Mixed; `Daily lesson completion limits` → Mixed). Opus 4.7 reads these clusters as less coherent than Haiku does.
- Sonnet 4.6 produces labels that are near-verbatim matches to Haiku — 6/7 clusters have identical label semantics (modulo synonym wording like "Lesson progress not recorded" vs "Lesson completion not recorded"). Sonnet and Haiku converge on this task.

Operationally this means the current L3b skill is **not model-invariant**. A downstream consumer ("how many coherent product themes surfaced in this corpus?") gets a different answer depending on which labeller ran.

## Methodology

### Inputs

Identical to the Haiku baseline — three L3 cluster inventories:

| pipeline | L3 input sha256 |
|---|---|
| opus46 | `2f0258d4526432643a8230f74cf300961460a68f95d6640385acffb97f7739d7` |
| opus47 | `2cbcac1fc2152612812e21a9174fd849480629a5da976e42c2cc9479ba271eff` |
| sonnet46 | `223e0bf452f60cda6a5570ee01e2aee3de44e99e9ccaa5c6abb34b391c6ea14e` |

### Matched runs

| run_id | model | clusters | themed | Mixed | artefact sha256 | written_at |
|---|---|---|---|---|---|---|
| `l3b-full-opus46-matched` | `claude-opus-4-6` | 14 | 13 | 1 | `614f3b48c24c9f9c89271356915adb10aa1b28287626962bceeea6181fe0695a` | 2026-04-23T10:09:53Z |
| `l3b-full-opus47-matched` | `claude-opus-4-7` | 10 | 7 | 3 | `c1ad65f0689cbf1e1d1c5935b15d6990fd3dae231f085321ca805eb6854d9d98` | 2026-04-23T10:09:56Z |
| `l3b-full-sonnet46-matched` | `claude-sonnet-4-6` | 7 | 3 | 4 | `6df4f702b4eef21d83b664ce9aa6983ca5b126a5aaee7d5c73caea93d8356640` | 2026-04-23T10:09:59Z |

All three runs: `temperature=0.0`, `max_tokens=128`, mode `live`, same skill hash as the baseline (`8f6bffe52347796050792e1016355d969e950db5102f0bcafc89650e4e2cf10b`). Zero fallbacks, zero transport failures across 31 labels.

Outputs at `data/derived/l3b_labeled_clusters/matched_rubric_v1/l3b_labeled_clusters_full_{opus46,opus47,sonnet46}.jsonl`. The baseline artefacts at `data/derived/l3b_labeled_clusters/l3b_labeled_clusters_full_*.jsonl` are untouched — the two eval sets coexist on disk for the comparison tables below. (The `_rubric_v1` suffix disambiguates this matched run from the later rubric v2 re-run at `matched_rubric_v2/`; both coexist on disk.)

### Cost calibration

Tracker total across the three matched runs: **$0.5285**. Applying the calibration rule (Opus 4.x tracker ÷ 3, Sonnet/Haiku 1:1): predicted real spend **$0.1932**. Console confirmed **$0.19** — exact match. This extends the calibration table: the Opus-family 3× overestimate applies to both 4.6 and 4.7, not just 4.6. Tracker spend on Opus runs should always be divided by 3 before being cited as the real cost; Sonnet and Haiku remain 1:1.

Observation: Opus 4.7 cost more per call than Opus 4.6 ($0.0247 vs $0.0183 per cluster label). Prompts are identical shape; the delta is almost certainly output-token length — Opus 4.7 produces longer labels ("General frustration without specific cause", "General dissatisfaction with Duolingo quality") than Opus 4.6's tighter *"General dislike without specific reason"* / *"Generic negative sentiment without specifics"* phrasing. For a ≤60-char output contract, both are verbose.

## Results

### Side-by-side labels (all four labellers, all 31 clusters)

**opus46 pipeline — 14 clusters.**

| id | n | first quote | Haiku (baseline) | Opus 4.6 (matched) |
|---|---|---|---|---|
| 00 | 8 | "aprendí por mucho TIEMPO PARA NADA" | Mixed complaints | Mixed complaints |
| 01 | 5 | "I gave it 3 star" | Mixed complaints | **Lowered star rating** |
| 02 | 7 | "my streak is not maintained" | Streak tracking not maintained | Streak not maintained or lost |
| 03 | 4 | "I keep getting it wrong" | Voice recognition marks correct answers wrong | Voice recognition marks correct answers wrong |
| 04 | 5 | "helping me to learn new languages" | Limited language selection or learning focus | Language learning scope and focus |
| 05 | 10 | "I don't like it" | Mixed complaints | General dislike without specific reason |
| 06 | 9 | "used to be good" | App quality declined over time | App quality declined over time |
| 07 | 13 | "TERRIBLE" | Mixed complaints | Generic negative sentiment without specifics |
| 08 | 12 | "Duolingo kind of sucks" | App quality declined over time | General dissatisfaction with Duolingo quality |
| 09 | 6 | "very disappointing" | Mixed complaints | General disappointment |
| 10 | 46 | "This used to be a great app" | App quality declined over time | App quality declined over time |
| 11 | 12 | "completing my lesson every day" | Mixed complaints | **Lesson completion progress** |
| 12 | 18 | "annoying" | Mixed complaints | General annoyance |
| 13 | 6 | "Very frustrating" | Mixed complaints | General frustration without specific cause |

**opus47 pipeline — 10 clusters.**

| id | n | first quote | Haiku (baseline) | Opus 4.7 (matched) |
|---|---|---|---|---|
| 00 | 4 | "aprendí por mucho TIEMPO" | Excessive monetization of previously free features | **Mixed complaints** |
| 01 | 7 | "is incorrect" | Voice recognition marks correct answers wrong | Voice recognition marks correct answers wrong |
| 02 | 10 | "Duolingo kind of sucks" | Mixed complaints | Mixed complaints |
| 03 | 12 | "terrible" | Mixed complaints | Generic negative sentiment |
| 04 | 9 | "very disappointed" | Mixed complaints | General disappointment |
| 05 | 10 | "completing my lesson every day" | Daily lesson completion limits | **Mixed complaints** |
| 06 | 11 | "frustrating" | Mixed complaints | Generic frustration |
| 07 | 19 | "annoying" | Mixed complaints | Generic annoyance |
| 08 | 16 | "used to love this app" | App quality declined over time | App quality declined over time |
| 09 | 6 | "freezing all the time" | App freezes repeatedly | App freezes repeatedly |

**sonnet46 pipeline — 7 clusters.**

| id | n | first quote | Haiku (baseline) | Sonnet 4.6 (matched) |
|---|---|---|---|---|
| 00 | 6 | "duolingo that i know before" | Mixed complaints | Mixed complaints |
| 01 | 3 | "learn chess" | Chess feature discoverability | Chess feature discoverability |
| 02 | 6 | "very disappointing" | Mixed complaints | Mixed complaints |
| 03 | 8 | "Very frustrating" | Mixed complaints | Mixed complaints |
| 04 | 17 | "annoying" | Mixed complaints | Mixed complaints |
| 05 | 32 | "used to be a great app" | App quality declined over time | App quality declined over time |
| 06 | 5 | "I had completed the lesson" | Lesson progress not recorded | Lesson completion not recorded |

### Labeller behavioural profiles

**Opus 4.6 — "refuses to emit Mixed."** Only 1 Mixed in 14 clusters, and that one (cluster_00, bilingual ES/PT regret) is unambiguous. Everywhere else Opus 4.6 produces a label. The wins are real but narrow — cluster_01 *"Lowered star rating"* names a behaviour Haiku left in the Mixed bucket, and cluster_11 *"Lesson completion progress"* promotes a coherent-enough-to-label cluster. The rest of the 13 themed labels split into:

- **Rubric-honest themed labels (5):** cluster_02 (streak), cluster_03 (voice-recog), cluster_04 (language coverage), cluster_06 and cluster_10 (regression). Same theme as Haiku.
- **Tier-3 affect paraphrases (6):** cluster_05 *"General dislike"*, cluster_07 *"Generic negative sentiment"*, cluster_08 *"General dissatisfaction"*, cluster_09 *"General disappointment"*, cluster_12 *"General annoyance"*, cluster_13 *"General frustration without specific cause"*. All six are exactly the affect-only clusters the rubric tier-3 paragraph says to suppress in favour of Mixed. Cluster_13 is particularly on-the-nose — the label itself admits "without specific cause", which is the rubric's definition of Mixed.
- **Wins (2):** cluster_01, cluster_11 above.

**Opus 4.7 — inconsistent but more conservative on content-mixed clusters.** Three Mixed in 10, and the interesting part is *which* three:

- cluster_00 (ES/PT bilingual regret) — Haiku confidently labelled *"Excessive monetization of previously free features"*, synthesising from the Spanish/Portuguese quote *"o intuito de ser gratuito"* and *"TODO ES DINERO AHORA"*. Opus 4.7 declined the synthesis. Both are defensible; Opus 4.7 is being more careful about emitting a label that paraphrases content the bulk of the representative quotes don't mention.
- cluster_02 (Duolingo-sucks family) — both Haiku and Opus 4.7 emit Mixed. Agreement on a content-mixed cluster.
- cluster_05 (lesson completion family) — Haiku labelled *"Daily lesson completion limits"*. The 5 representative quotes span *"completing my lesson every day"* / *"only do 1 lesson a day"* / *"I completed a lesson"* / *"so many lessons in one day"* / *"having to leave lessons and restart multiple times"* — which is three distinct sub-themes (completion, limits, interruption). Opus 4.7 reading Mixed here is arguably more rubric-faithful than Haiku's confident label.

But Opus 4.7 still emits **four Generic X affect paraphrases** on clusters 03, 04, 06, 07 — so it is *not* consistently honouring tier-3 either, just doing so on different clusters.

**Sonnet 4.6 — tracks Haiku.** 4 Mixed, 3 themed, all decisions agree with Haiku's calls on the same input. The only delta is wording (`"Lesson progress not recorded"` → `"Lesson completion not recorded"`). For the sonnet46 pipeline, the choice of Haiku vs Sonnet as labeller is operationally neutral.

### Rubric-adherence scoring (tier-3 violations)

Counting cases where a labeller emits a "Generic X" / "General X" affect paraphrase instead of `Mixed complaints` on an affect-only cluster:

| Labeller | Tier-3 violations | Denominator | Rate |
|---|---|---|---|
| Haiku 4.5 (baseline) | 0 | 17 affect-only clusters | 0% |
| Opus 4.6 (matched) | 6 | 6 affect-only clusters in opus46 | 100% |
| Opus 4.7 (matched) | 4 | ~5 affect-only clusters in opus47 | ~80% |
| Sonnet 4.6 (matched) | 0 | 4 affect-only clusters in sonnet46 | 0% |

Opus 4.6 violates tier-3 on every pure-affect cluster it sees. Opus 4.7 does so on most. Haiku and Sonnet do not. This is a rubric-adherence gap, not a quality gap per se — the Opus labels ("General annoyance" over a cluster whose quotes are all variants of "annoying") are literally accurate; they just carry no more element-level information than the Mixed sentinel would, and emitting them defeats the purpose of the sentinel as a first-class L4 audit signal.

Two plausible readings:

1. **The rubric is under-specified for frontier models.** The SKILL.md tier-3 paragraph is phrased as preference ("prefer Mixed over weak affect-paraphrase"), not prohibition. Adding a negative examples block ("do not emit labels like 'General annoyance'") would likely close the gap. Haiku and Sonnet interpret the preference strictly; Opus treats it as a soft guideline.
2. **Opus has a helpfulness-over-sentinel bias.** Frontier models may be trained against a target of "always emit a useful-looking response" that conflicts with the rubric's "emit the sentinel when you can't do better". This isn't instruction ignoring — it is the skill prompt and the model's base training pulling in opposite directions.

The implication for the first L4 audit module (cluster-coherence) is concrete: **do not use `label == "Mixed complaints"` as the sole coherence signal — it is labeller-dependent.** An embedding-variance metric computed directly from member node vectors is labeller-independent and should be the primary L4 input; the label can be a weak secondary signal.

### Cross-labeller convergence on themed labels

Where multiple labellers commit to a non-Mixed label on the same cluster, do they agree on the theme?

| pipeline | cluster | first quote | Haiku | Matched | agree? |
|---|---|---|---|---|---|
| opus46 | 02 | "my streak is not maintained" | Streak tracking not maintained | Streak not maintained or lost | yes (synonym) |
| opus46 | 03 | "I keep getting it wrong" | Voice recognition marks correct answers wrong | Voice recognition marks correct answers wrong | yes (verbatim) |
| opus46 | 04 | "helping me to learn new languages" | Limited language selection or learning focus | Language learning scope and focus | partial (Haiku frames as limitation, Opus 4.6 as neutral scope) |
| opus46 | 06 | "used to be good" | App quality declined over time | App quality declined over time | yes (verbatim) |
| opus46 | 08 | "Duolingo kind of sucks" | App quality declined over time | General dissatisfaction with Duolingo quality | partial (same direction, different abstraction) |
| opus46 | 10 | "This used to be a great app" | App quality declined over time | App quality declined over time | yes (verbatim) |
| opus47 | 01 | "is incorrect" | Voice recognition marks correct answers wrong | Voice recognition marks correct answers wrong | yes (verbatim) |
| opus47 | 08 | "used to love this app" | App quality declined over time | App quality declined over time | yes (verbatim) |
| opus47 | 09 | "freezing all the time" | App freezes repeatedly | App freezes repeatedly | yes (verbatim) |
| sonnet46 | 01 | "learn chess" | Chess feature discoverability | Chess feature discoverability | yes (verbatim) |
| sonnet46 | 05 | "used to be a great app" | App quality declined over time | App quality declined over time | yes (verbatim) |
| sonnet46 | 06 | "I had completed the lesson" | Lesson progress not recorded | Lesson completion not recorded | yes (synonym) |

Where both committed to a theme, agreement is near-perfect — 9/12 verbatim or pure synonym, 2/12 partial (same direction, different abstraction level), 0/12 true disagreement. The themed labels are stable across labellers; what varies is the *threshold* for committing to a theme at all.

### Haiku ↔ matched disagreements on themed labels (going the other way)

Two cases where Haiku committed to a specific theme and the matched Opus 4.7 declined to:

- **opus47 cluster_00 — Haiku:** *"Excessive monetization of previously free features"*. **Opus 4.7:** Mixed. Representative quotes: *"aprendí por mucho TIEMPO"*, *"mucho tiempo perdido"*, *"o intuito de ser gratuito"*, *"vergonhoso o que esse aplicativo virou"*, *"TODO ES DINERO AHORA"*. The monetisation theme is carried by the last two quotes; the first three are time-wasted / regret. This is a content-mixed cluster where Haiku synthesised a theme from the minority mention and Opus 4.7 declined to. The Haiku baseline eval already flagged this as "close to the no-evaluative-adjectives-absent-from-quotes edge".
- **opus47 cluster_05 — Haiku:** *"Daily lesson completion limits"*. **Opus 4.7:** Mixed. Quote span is three sub-themes (completion / limits / interruption). Opus 4.7's Mixed is the more rubric-honest call.

In both cases Opus 4.7's Mixed appears *more* accurate than Haiku's theme, not less. The labeller-variance isn't all in the same direction — Opus 4.6 and Opus 4.7 push different ways from the same baseline.

## Interpretation

Three separate observations worth keeping apart:

1. **"Mixed rate" is labeller-dependent, not an intrinsic corpus property.** Comparing 7% (Opus 4.6) vs 57% (Sonnet 4.6) on the same three L3 inputs demonstrates this clearly. Any downstream metric built on Mixed rate must either fix the labeller or measure the property differently (e.g., via embedding variance).
2. **Frontier-model helpfulness bias fights the sentinel rubric.** Opus 4.6 in particular emits "General annoyance" style labels on affect-only clusters where the rubric asks for Mixed. The labels are accurate but carry no element-level information — they look useful while duplicating what Mixed already signals.
3. **Themed-label agreement is high when labellers commit.** On 12 clusters where both Haiku and a matched labeller gave a non-Mixed label, 9 are verbatim/synonym matches. The labels themselves are stable; the *threshold* for emitting one varies.

For a deployment decision, this means:

- The Haiku baseline is a **conservative labeller** that is tight on the rubric and cheap to run. Good default if "a Mixed label means something".
- The matched-model option is a **specificity-aggressive labeller** that pushes more themes but leaks tier-3 affect paraphrases. Good if a human reviewer is going to post-filter anyway.
- **Pick one; don't mix runs** in a downstream audit unless the audit explicitly uses labeller identity as a feature.

## Iteration: rubric v2 (all three matched labellers re-run)

The *"Close the rubric gap"* follow-up in the original *What's next* section was executed. `skills/label-cluster/SKILL.md` was hardened with a new **Forbidden label shapes** section explicitly disallowing `"General X"` / `"Generic X"` / `"X without specific cause"` / pure-emotion shapes, plus a new **affect-only** clause in *Thin or incoherent clusters* routing those clusters toward `Mixed complaints`, plus a worked example showing an affect-only cluster resolving to `Mixed complaints` (with the counterexample *"Not 'General frustration'. Not 'Generic negative sentiment'"* spelled out). The edit changes the SYSTEM_PROMPT body, which changes `skill_hash`, which invalidates all 31 cached matched entries — every call in the re-run is fresh.

All three matched labellers were re-run under the hardened skill. Driver at `scripts/run_l3b_matched_rubric_v2.sh`, outputs at `data/derived/l3b_labeled_clusters/matched_rubric_v2/` (separate directory — the original `matched_rubric_v1/` artefacts are untouched, both coexist). Cost: opus46 tracker $0.41 (+$0.00 on the second replay pass — cache stable), opus47 tracker $0.38, sonnet46 tracker $0.04. Real billing estimate ~$0.17 (Opus ÷3 + Sonnet 1:1).

### Headline results

| Labeller | tier-3 baseline | tier-3 v2 | fallback v2 | notable effect |
|---|---|---|---|---|
| Opus 4.6 | 6/14 (43%) | 0/14 (0%) | 2/14 (14%) | reasoning-drift on ambiguous clusters |
| Opus 4.7 | 4/10 (40%) | 0/10 (0%) | 0/10 (0%) | +1 cluster recovered from Mixed → themed commit |
| Sonnet 4.6 | 0/7 (0%) | 0/7 (0%) | 0/7 (0%) | byte-identical output — null-effect |
| **Total** | **10/31 (32%)** | **0/31 (0%)** | **2/31 (6%)** | |

Three distinct response patterns to the same rubric change — diagnostic of the labeller, not just the rubric.

### Opus 4.6 (14 clusters): tier-3 eliminated, reasoning-drift introduced

| cluster_id | baseline matched label | rubric v2 label | verdict |
|---|---|---|---|
| cluster_00 | `Mixed complaints` | `UNLABELED:cluster_00` | regression (parse-fail) |
| cluster_01 | Lowered star rating | Rating downgrade due to declining satisfaction | ≈ acceptable variation |
| cluster_02 | Streak not maintained or lost | Streak not maintained or lost | = identical |
| cluster_03 | Voice recognition marks correct answers wrong | Voice recognition marks correct answers wrong | = identical |
| cluster_04 | Language learning scope and focus | `UNLABELED:cluster_04` | regression (parse-fail) |
| cluster_05 | **General dislike without specific reason** | `Mixed complaints` | **fixed** ✓ |
| cluster_06 | App quality declined over time | App quality declined over time | = identical |
| cluster_07 | **Generic negative sentiment without specifics** | `Mixed complaints` | **fixed** ✓ |
| cluster_08 | **General dissatisfaction with Duolingo quality** | App quality declined over time | **fixed** ✓ |
| cluster_09 | **General disappointment** | `Mixed complaints` | **fixed** ✓ |
| cluster_10 | App quality declined over time | App quality declined over time | = identical |
| cluster_11 | Lesson completion progress | Lesson completion progress tracking | ≈ acceptable variation |
| cluster_12 | **General annoyance** | `Mixed complaints` | **fixed** ✓ |
| cluster_13 | **General frustration without specific cause** | `Mixed complaints` | **fixed** ✓ |

Tier-3 violation count: **6/14 → 0/14** (100% → 0%). Ship threshold was ≤1/14 — exceeded.

Of the six previously-violating clusters, five moved to `Mixed complaints` (the sentinel was the correct answer — these are affect-only clusters) and one (cluster_08) moved to `App quality declined over time`, which is the label Opus 4.6 emits on adjacent clusters 06 and 10 that share the "used to be good" register. That is not a sentinel-fill — it is the model correctly merging a cluster that was previously labelled as a standalone "generic dissatisfaction" theme into the atomised "quality declined" family already present in this pipeline's output. Consistent with the per-label commentary in the original doc's *Side-by-side* section.

### New failure mode: output-contract violation on Opus 4.6

The two regressions at cluster_00 and cluster_04 are not rubric failures — the model reasoned correctly but violated the *"Respond with ONLY a JSON object, no prose"* constraint and wrote its reasoning out loud instead of emitting the JSON object. `l3b_label` parsed 0 JSON from the response and emitted `UNLABELED:<cluster_id>` placeholders per the ADR-011 fallback contract.

Sampled reasoning (cluster_00): *"These share a feeling of frustration/disappointment but point at different triggers: wasted learning progress, app decline, lesson duration, unmet expectations."* — which is a textbook Mixed complaints verdict. cluster_04 was the same pattern, truncated at `max_tokens=128` mid-sentence because the reasoning ate the budget.

Interpretation: hardening the rubric content pushed Opus 4.6 into a more deliberative mode on ambiguous clusters, and the model's tendency to show its work overwhelmed the thin output-contract phrasing. The extended re-run on Opus 4.7 (below) did *not* reproduce this failure — so the drift is Opus 4.6-specific, not Opus-family-wide. The fix is either (a) strengthen the output contract (move it to the top of SKILL.md, make it more emphatic), (b) prefill the assistant turn with `{"label": "` to force JSON continuation, or (c) raise `max_tokens` so reasoning + JSON both fit. Option (b) is the cleanest — it moves the constraint from persuasion to mechanism — but given the narrow scope (one labeller, 14% rate), it is not urgent. Logged in *What's next*.

Net effect of the hardening on Opus 4.6: tier-3 violations eliminated (the stated goal), but introduced a 14% fallback rate on this pipeline that wasn't there before. In a downstream-audit context this is preferable — `UNLABELED:<cluster_id>` is a known-unknown signal a human can handle, whereas `"General dislike without specific reason"` is a false-positive label that looks actionable. The iteration ships.

### Opus 4.7 (10 clusters): tier-3 eliminated, one confidence recovery, no drift

| cluster_id | baseline matched label | rubric v2 label | verdict |
|---|---|---|---|
| cluster_00 | `Mixed complaints` | Shift from free to paid model | **↑ committed** (confidence recovery) |
| cluster_01 | Voice recognition marks correct answers wrong | Voice recognition marks correct answers wrong | = identical |
| cluster_02 | `Mixed complaints` | `Mixed complaints` | = identical |
| cluster_03 | **Generic negative sentiment** | `Mixed complaints` | **fixed** ✓ |
| cluster_04 | **General disappointment** | `Mixed complaints` | **fixed** ✓ |
| cluster_05 | `Mixed complaints` | `Mixed complaints` | = identical |
| cluster_06 | **Generic frustration** | `Mixed complaints` | **fixed** ✓ |
| cluster_07 | **Generic annoyance** | `Mixed complaints` | **fixed** ✓ |
| cluster_08 | App quality declined over time | App quality declined over time | = identical |
| cluster_09 | App freezes repeatedly | App freezes repeatedly | = identical |

Tier-3 violation count: **4/10 → 0/10**. All four previously-violating clusters moved to `Mixed complaints` — the sentinel was the correct answer for each. No parse failures, no reasoning drift: Opus 4.7 honoured the output contract on every call despite the thicker rubric.

The one surprising effect is cluster_00, which moved in the *opposite* direction: Opus 4.7 committed to a themed label (`"Shift from free to paid model"`) under v2 after playing safe with `Mixed complaints` under v1. This is the rubric v2's *Thin or incoherent clusters* section narrowing the Mixed criterion to two specific cases (heterogeneous triggers OR affect-only); an ambiguous-but-thematic cluster like the bilingual monetisation fragments no longer passes that filter, so Opus 4.7 commits. Side-effect was not part of the design goal but reads as a positive — one more themed label recovered without the false-positive risk the old rubric tolerated.

### Sonnet 4.6 (7 clusters): null-effect, byte-identical output

sha256 of the rubric-v2 output artefact is **identical** to the baseline matched artefact: `6df4f702b4eef21d83b664ce9aa6983ca5b126a5aaee7d5c73caea93d8356640` in both places. Sonnet 4.6 produced byte-identical labels under v1 and v2 rubrics. This holds despite 7 fresh API calls on the v2 run (the skill_hash changed, so the v1 cache entries were not hit) — Sonnet arrived at the same 4 Mixed + 3 themed labels independently under both versions of the rubric.

Interpretation: Sonnet 4.6 was already honouring the *"emit Mixed on affect-only clusters"* rule before it was made explicit. The negative examples added to v2 (*"Not 'General frustration'"*) codify behaviour Sonnet was already exhibiting. This is the cleanest signal in the iteration — it confirms the rubric change is additive, not behaviourally coercive: it brings Opus models up to Sonnet's existing adherence without regressing Sonnet.

### Cost

Re-run tracker spend: $0.4104 (opus46, 14 fresh calls on first execution) + $0.0000 (opus46 replay, second execution cached) + $0.3785 (opus47, 10 fresh calls) + $0.0385 (sonnet46, 7 fresh calls) = $0.8274 total across 31 unique calls. Expected real billing ~$0.17 after Opus ÷3 + Sonnet 1:1 calibration. Console reconciliation pending.

**Artefact hashes (rubric v2):**

| File | item_count | sha256 |
|---|---|---|
| `matched_rubric_v2/l3b_labeled_clusters_full_opus46.jsonl` | 14 | `a1e4b60c455b7b59231695c3efcd47741422d03f664eae8bcf3b7649d8650610` |
| `matched_rubric_v2/l3b_labeled_clusters_full_opus47.jsonl` | 10 | `8fa0152c83d4c2931fdfe96155d2b24ca5e537f6f923b3395fe0adfaed431d06` |
| `matched_rubric_v2/l3b_labeled_clusters_full_sonnet46.jsonl` | 7 | `6df4f702b4eef21d83b664ce9aa6983ca5b126a5aaee7d5c73caea93d8356640` |

The sonnet46 v2 hash matches the baseline matched sonnet46 hash byte-for-byte — separate measurement noted above.

Skill hash change (concrete evidence of cache invalidation): `label-cluster` v1 = `8f6bffe52347796050792e1016355d969e950db5102f0bcafc89650e4e2cf10b` (original matched run), v2 = `df9289ee5645bbb4cf6eb5c2131a7dc3711ec9e9a6a7b710d1ac5394a409780e` (this run). Different skill_hash → different cache key → 31 fresh calls, no replay of v1 entries.

### Summary: rubric v2 ships

Across 31 clusters and three labellers: **tier-3 violations 10/31 → 0/31**. The hardening achieves its stated goal with no regressions on themed-label accuracy (verified by Sonnet's byte-identical output and Opus 4.7's fix-only delta), plus one bonus confidence recovery (opus47 cluster_00). The only side-effect is a 14% fallback rate on Opus 4.6 specifically, which is preferable to false-positive affect paraphrases in a downstream-audit context and is plausibly closeable via the prefill mechanism in *What's next*.

## Follow-up: closing the 2/14 Opus 4.6 fallback gap

*What's next* proposed three fixes in leverage order: (a) prefill the assistant turn with `{"label": "` so JSON continuation is structurally forced, (b) move the output-contract line to the top of SKILL.md, (c) raise `max_tokens` from 128. The follow-up tried (a) first, discovered it is blocked at the model layer, and fell back to (c) with a root-cause reframe that upgrades the option from "regressive" to "correct".

### Option (a): API-level block, not a near miss

The prefill mechanism was plumbed end-to-end — `prefill` parameter added to `claude_client.Client.call()` and threaded through `_key_hash()`, `_dispatch()`, and the replay-log entry; l3b_label's call-site passed `prefill='{"label": "'`; `response_text` was reconstructed as `prefill + model_continuation` before JSON parse. A dry rerun against Opus 4.6 returned **400 on 14/14 calls** with message *"This model does not support assistant message prefill. The conversation must end with a user message."* This is a model-specific API restriction, not a transport bug — Anthropic returns the same error for Opus 4.6 regardless of how the assistant turn is constructed.

The prefill plumbing was fully reverted from `claude_client.py` and `tests/test_claude_client.py` — the baseline is restored byte-for-byte, no dead code left behind. If Anthropic opens prefill on Opus 4.x in a future model revision, the diff is recoverable from git history; there is no ongoing reason to keep the plumbing half-built.

### Root-cause reframe: budget ceiling, not reasoning drift

The *Results → New failure mode* section above framed the 2/14 fallbacks as "Opus 4.6 pushed into a more deliberative mode by the hardened rubric, and the model's tendency to show its work overwhelmed the thin output-contract phrasing". Inspecting the two failing cache entries contradicts the first half of that framing. Both had `output_tokens == 128` — the budget ceiling hit, not a deliberate "reason first, then write JSON" completion. The model was mid-sentence when the sampler cut off, and no `{...}` was ever emitted. Sample (cluster_00 continuation, last tokens before truncation): *"…but point at different triggers: wasted learning progress, app decline, lesson duration, unmet expectations."* — this is a reasoning prologue, not a completed response. With 128 tokens of budget the model never had room for the JSON payload after the prologue.

That reframes option (c) from "regressive — raises cost and only hides the drift" to "correct — the budget was the bottleneck, raise it". Billing is on actually-emitted tokens, so raising the ceiling is free on the 12/14 non-reasoning cases and costs only the incremental prologue tokens on the 2/14. And `MAX_TOKENS` is part of the cache `key_hash`, so a bump is a clean cache-invalidation event — no stale entries.

### Fix: MAX_TOKENS 128 → 512

One-line change in `src/auditable_design/layers/l3b_label.py` (`MAX_TOKENS: int = 512`) with a root-cause comment, plus a matching assertion update in `tests/test_l3b_label.py`. 512 leaves headroom for ~400 tokens of preamble plus the short JSON payload; a JSON object of shape `{"label": "<≤60 chars>"}` is well under 50 tokens, so the rest is pure budget for the model's own style.

`skill_hash` unchanged (`df9289ee5645bbb4cf6eb5c2131a7dc3711ec9e9a6a7b710d1ac5394a409780e`) — the skill text was not touched. The `max_tokens=512` change alone invalidates all 31 matched_rubric_v2 cache entries.

### Measurement (all three labellers re-run under rubric v2 + MAX_TOKENS=512)

| Labeller | labeled | mixed | fallback | tracker spend |
|---|---|---|---|---|
| Opus 4.6 | 14 | 7 | **0** (was 2) | $0.4104 |
| Opus 4.7 | 10 | 6 | 0 (unchanged) | $0.3832 |
| Sonnet 4.6 | 7 | 4 | 0 (unchanged) | $0.0385 |
| **Total** | **31** | **17** | **0** (was 2) | **$0.8321** |

Expected real billing ~$0.28 after Opus ÷3 + Sonnet 1:1 calibration. Zero transport failures across 31 calls.

**Per-labeller deltas, cluster by cluster:**

*opus46 (the target of the fix).* Both previous UNLABELED placeholders resolved to the rubric's intended sentinel — cluster_00 (bilingual ES/PT regret, heterogeneous triggers): `UNLABELED:cluster_00` → `Mixed complaints`. cluster_04 (language-learning span, reasoning-truncated): `UNLABELED:cluster_04` → `Mixed complaints`. Recall the sampled reasoning prologue from cluster_00: *"These share a feeling of frustration/disappointment but point at different triggers…"* — that prologue reached `Mixed` as its verdict, the budget just never let it write one. With 512 tokens the prologue + JSON both fit. The 12 clusters that already had labels under rubric v2 retained them verbatim. Mixed rose from 5 to 7 — exactly the 2 recovered UNLABELED placeholders. No other delta.

*opus47 (no fallback either way — a control).* Headline counts identical to rubric v2 baseline: 6 Mixed, 4 themed. The only diff is at cluster_00: `Shift from free to paid model` → `Monetization of previously free features`. Same theme, different phrasing. `temperature=0.0` should make this deterministic, but Opus 4.7 drops caller-requested sampling params (`_omits_sampling_params`) and runs with model-default sampling; this wording flip is consistent with server-side sampling variance rather than any effect of the MAX_TOKENS change. 9/10 clusters are verbatim matches with the rubric v2 baseline.

*sonnet46 (control).* Output artefact sha256 **byte-identical** to both earlier sonnet46 runs (matched baseline and rubric v2) — `6df4f702b4eef21d83b664ce9aa6983ca5b126a5aaee7d5c73caea93d8356640` across all three. Sonnet 4.6 is stable across all three skill/budget configurations; this is the null-effect control confirming the MAX_TOKENS bump is a surgical fix for Opus 4.6's budget truncation, not a regime change affecting other labellers.

### Artefact hashes (rubric v2 + MAX_TOKENS=512)

| File | item_count | sha256 |
|---|---|---|
| `matched_rubric_v2/l3b_labeled_clusters_full_opus46.jsonl` | 14 | `3c548c1530353a76c0b6f07014b3df557e6024ff0d64c5d9e2f715cc9ae58de9` |
| `matched_rubric_v2/l3b_labeled_clusters_full_opus47.jsonl` | 10 | `e950bc5d2c3a03262ef00bd949b050c44a99719e47b293b7266532c8cffd7e59` |
| `matched_rubric_v2/l3b_labeled_clusters_full_sonnet46.jsonl` | 7 | `6df4f702b4eef21d83b664ce9aa6983ca5b126a5aaee7d5c73caea93d8356640` |

The opus46 and opus47 hashes **replace** the earlier rubric v2 hashes at the same path (the MAX_TOKENS=128 artefacts were overwritten on re-run). The sonnet46 hash is unchanged — byte-identical output for the third time in this eval, across three different skill/budget combinations. Driver script: `scripts/run_l3b_matched_rubric_v2.sh`.

### Anti-fix note for future readers

If a future iteration ever hits a "force JSON output by prefilling the assistant turn" impulse on Opus 4.x, the relevant prior-art is: (i) the mechanism is supported by the Anthropic API in general, (ii) Opus 4.6 specifically returns 400 on any request that ends with an assistant message, (iii) Opus 4.7 was not tested against this restriction in this follow-up (no fallbacks there, so no motivation). Don't spend time re-plumbing prefill for Opus 4.6 until an Anthropic model-card update lifts the restriction. The budget-bump fix is the right answer for this specific failure mode anyway — the 2/14 fallbacks were sampler truncation, not a genuine "model refuses to emit JSON" issue that would need a structural forcing function.

## Caveats

- **Only one run per (input, labeller) pair.** Temperature is 0.0 so determinism should be close to perfect, but this is not a variance study — a single tie-break in Opus 4.6's sampler could flip one of the "Generic X" decisions. Budget for a second live run per pair before making any strong claim about per-labeller Mixed rates.
- **The rubric-adherence scoring is my informal judgment, not a scored rubric run.** A formal scoring pass using `skills/label-cluster/rubric.md` over the 23 themed labels here would convert the tier-3 count into a defensible number. Not done in this eval.
- **Cost calibration for Opus 4.7 now covered.** Console confirmed $0.19 on this run, matching the prediction from the Opus ÷3 rule applied to both 4.6 and 4.7 tracker values. Future Opus 4.7 runs can use the ÷3 calibration.
- **Opus 4.7 omits temperature / top_p / top_k at API layer** (`_omits_sampling_params` in `claude_client.py`). Cache keys still record the caller-requested temperature for audit replay. This is a documented special-case and does not affect label output here (`temperature=0.0` would be the default anyway), but means that `temperature=0.9` requests on Opus 4.7 would *look* sampled in the replay log while actually running with the Opus 4.7 default. Not hit in this eval — flagged for downstream auditors.
- **`Mixed complaints` verdicts on affect-only clusters are by-design, not by-accident.** Reading the 17 Haiku Mixed labels as "17 failed attempts to label" would be wrong — 13 of them are the rubric's intended outcome on affect-only clusters.

## Reproducing this document

Driver script at `scripts/run_l3b_matched.sh`. Run from the repo root:

```bash
bash scripts/run_l3b_matched.sh
```

The script runs three live `uv run python -m auditable_design.layers.l3b_label` invocations with `--model` set to each pipeline's matched model and output paths under `data/derived/l3b_labeled_clusters/matched_rubric_v1/`. Mode is live — the cache didn't contain Opus/Sonnet keys for these prompts when this eval was written; a re-run from a cold cache will replay once the responses are logged to `data/cache/responses.jsonl`.

Verify:

```bash
sha256sum \
  data/derived/l3b_labeled_clusters/matched_rubric_v1/l3b_labeled_clusters_full_opus46.jsonl \
  data/derived/l3b_labeled_clusters/matched_rubric_v1/l3b_labeled_clusters_full_opus47.jsonl \
  data/derived/l3b_labeled_clusters/matched_rubric_v1/l3b_labeled_clusters_full_sonnet46.jsonl
```

Expected:

| File | sha256 |
|---|---|
| `matched_rubric_v1/l3b_labeled_clusters_full_opus46.jsonl` | `614f3b48c24c9f9c89271356915adb10aa1b28287626962bceeea6181fe0695a` |
| `matched_rubric_v1/l3b_labeled_clusters_full_opus47.jsonl` | `c1ad65f0689cbf1e1d1c5935b15d6990fd3dae231f085321ca805eb6854d9d98` |
| `matched_rubric_v1/l3b_labeled_clusters_full_sonnet46.jsonl` | `6df4f702b4eef21d83b664ce9aa6983ca5b126a5aaee7d5c73caea93d8356640` |

Byte-identical replay holds if the replay cache contains the 31 matched entries and the three L3 input sha256 values match the ones in the *Methodology → Inputs* table.

## What's next

- ~~**Strengthen the output contract against Opus 4.6's reasoning drift.**~~ **Closed** — see *Follow-up* section above. Option (a) prefill was attempted and is blocked at the model layer (Opus 4.6 API returns 400). Option (c) MAX_TOKENS bump shipped with the root-cause reframe: the truncations were sampler budget hits, not reasoning drift. 0/31 fallback on the re-run.
- **L4 cluster-coherence should NOT rely on Mixed rate.** The labeller-variance demonstrated above is the argument. Build L4 on intra-cluster embedding variance (over member pain/expectation node vectors) as the primary signal; allow `label == "Mixed complaints"` as a weak secondary feature at most.
- **Formal rubric-scoring run.** Execute `skills/label-cluster/rubric.md` over the 23 themed labels from the original 4-way matched run (or the post-rubric-v2 opus46 set if that is the chosen deployment). Produces a defensible anchoring/specificity score per label, not the informal reading in *Results → Labeller behavioural profiles*.
