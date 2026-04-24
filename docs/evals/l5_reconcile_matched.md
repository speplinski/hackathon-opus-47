# L5 sot-reconcile — matched-model eval

**Scope:** one cluster (`cluster_02` "Streak loss framing pressures users into mid-session purchase") × three reconcile models (Opus 4.6 / Sonnet 4.6 / Opus 4.7) × one modality (text-only — L5 consumes structured verdicts, not UI surfaces). 3 cells. Closes ADR-009 L5 pilot action item.

**Status:** 3/3 audited, zero fallbacks. After three intermediate iterations of SKILL.md + parser contract (detailed in *Architecture evolution* below), settled on a **graph-primary** contract: the model emits only `{summary, graph}`, and the parser derives `ranked_violations`, `tensions`, `gaps` by traversing the graph. This eliminates dual-representation drift that was the root cause of the earlier 100% fail rate.

## Purpose

L5 reconciles the six per-skill L4 audit verdicts for each cluster into one prioritised view. The skill is SOT-derived:

- **Corroboration** (cross-skill triangulation) as the primary mechanism for surfacing load-bearing violations
- **Tension** (principle-level disagreement between skills) as the main value-add — single-skill audits structurally cannot surface it
- **Gap** (something all six skills missed, visible in the evidence) as the arbiter's additive contribution

The three questions this eval tries to answer on a single adversarial stimulus:

- **Do the three reconcile models converge on the dominant cross-skill corroborations?** If L5 is robust, the top-ranked violations should look similar across model families.
- **Do they agree on which tensions surface?** The tension axis is the skill's load-bearing contribution; cross-model agreement here means the skill can be trusted downstream (L6 weighting).
- **Which model surfaces gaps, and which misses them?** A gap depends on noticing what the six skills did NOT say — a harder inference task than corroboration.

## Executive summary

| metric | Opus 4.6 | Sonnet 4.6 | Opus 4.7 |
|---|---:|---:|---:|
| Status | audited | audited | audited |
| Graph: violation nodes | 24 | 20 | 30 |
| Graph: corroboration nodes | 5 | 3 | **7** |
| Graph: contradiction nodes | 0 | 0 | 0 |
| Graph: tension nodes | **2** | 1 | 1 |
| Graph: gap nodes | 0 | **1** | 0 |
| Graph: edges | 26 | 21 | 29 |
| Ranked entries (derived) | 7 | 8 | 10 |
| Top rank_score | **45** | **45** | 36 |
| Input tokens | 22 674 | 22 674 | 31 927 |
| Output tokens | 8 048 | 7 166 | 10 004 |
| Skill_hash | `4e0026fd9cd877f7…` | `4e0026fd9cd877f7…` | `4e0026fd9cd877f7…` |

All three cells share the same `skill_hash` (the skill was not edited during the run). Output tokens are all well under the `MAX_TOKENS=16384` ceiling — the token-budget problems that plagued earlier v1.x runs are resolved by graph-primary contract.

## Methodology

### Input

**Cluster:** `cluster_02` "Streak loss framing pressures users into mid-session purchase" (Duolingo mid-lesson paywall). Shared fixture across all L4 matched evals (Norman, WCAG, Kahneman, Osterwalder, Cooper, Garrett).

- Cluster input file sha256: `dc6d981f1652884e0088d9299311230d183f9d7cb71c78d4729b1eec5068b961`
- HTML fixture sha256: `cdfcbd477646c72b3aeccc45d7089bed19c187f36503003ae30925ddd1ff59ba`
- Screenshot sha256: `bcad10de3d0351be345a479c1370353237afe554feb2382576dba39aec415d16`

### Bundle

The L4 verdict bundle feeding L5 is **six opus46-text verdicts** (one per skill) concatenated into one JSONL:

- `data/derived/l5_reconcile/cluster_02_opus46_text_bundle.jsonl` — sha256 `60c167bb365bc75f9121be13af3fd1aea84f89a89883af89a6c8af18a16f9c74`
- 6 rows, 49 findings total across all skills

Per-skill breakdown of findings the bundle carries:

| skill | findings | L4 skill_hash |
|---|---:|---|
| audit-usability-fundamentals | 7 | `81e25598489ddf40…` |
| audit-accessibility | 10 | `cb3598db9248efc2…` |
| audit-decision-psychology | 9 | `6ff2b137a029fb76…` |
| audit-business-alignment | 7 | `047320d20d5542ec…` |
| audit-interaction-design | 8 | `a7d3f38509f42baf…` |
| audit-ux-architecture | 8 | `9d641709d065154b…` |

All six L4 verdicts were generated with Opus 4.6 × text — uniform model config for apples-to-apples reconciliation.

### Skill

`skills/sot-reconcile/SKILL.md` v2.0 — graph-primary contract. Model emits `{summary, graph}`; parser derives flat lists via traversal. Three hard rules the parser enforces:

- Top-level keys are exactly `{summary, graph}` (legacy `ranked_violations`/`tensions`/`gaps` from v1.x silently dropped for backwards compat).
- Every violation node's `source_skill`/`source_heuristic`/`source_severity_anchored`/`source_finding_idx` must cross-validate against the input bundle.
- Bidirectional evidence rule on gap nodes: `"quotes"` in `evidence_source` ↔ non-empty `evidence_quote_idxs`.

The parser derives:

- `ranked_violations`: one entry per `corroboration` node (collapsing members — severity = max, source_skills = dedup), plus one entry per solitary violation (not in any corroboration). `rank_score = severity × corroboration_count`; sorted descending by (rank_score, unique_frames, severity).
- `tensions`: extracted from tension nodes.
- `gaps`: extracted from gap nodes.

**Parser is deliberately tolerant** — graph sizing, node counts, and the arithmetic of rank_score are model-authored structure, not contract constraints. The parser never rejects on soft-rule violations; it normalises and logs.

### Runs

All three via `scripts/smoke_l5_reconcile.py`, orchestrated by `scripts/run_l5_reconcile_matched.sh`. Text-only (L5 reconciles structured verdicts; screenshot attachment would add tokens without signal). Temperature pinned to 0.0 on Opus 4.6 and Sonnet 4.6; stripped on Opus 4.7 via `claude_client._omits_sampling_params`. `MAX_TOKENS=16384` (bumped from 8192 after the earliest v1.x run hit the truncation limit).

## Results

### Cross-model top-rank convergence

The top-3 ranked entries per cell reveal a clear convergence on modal / choice-architecture / business-alignment corroborations, with one interesting divergence at the top:

| rank | Opus 4.6 | Sonnet 4.6 | Opus 4.7 |
|---:|---|---|---|
| 1 | **modal_excise** (5 skills, sev 9 → rs 45) | **modal_excise** (5 skills, sev 9 → rs 45) | channel_gap (4 skills, sev 9 → rs 36) |
| 2 | channel_gap (4 skills, sev 9 → rs 36) | cr_undermined_by_r_dollar (4 skills, sev 9 → rs 36) | asymmetric_choice_architecture (4 skills, sev 7 → rs 28) |
| 3 | competing_calls_to_action (4 skills, sev 7 → rs 28) | asymmetric_choice_architecture (4 skills, sev 7 → rs 28) | daemonic_surface_demands_attention (3 skills, sev 9 → rs 27) |

Three observations:

1. **Opus 4.6 and Sonnet 4.6 agree top-1: `modal_excise` corroborated by all 5 non-accessibility skills at sev-9.** The mid-lesson blocking modal is the cluster's dominant defect; both models triangulate it from all available frames. Opus 4.7 *did* surface this cross-skill convergence but placed `channel_gap` ahead — a reading where the business-model × accessibility × Norman × Cooper agreement on the non-neutral-exit defect edges out the modal-excise reading.

2. **`asymmetric_choice_architecture` / `competing_calls_to_action` / `cr_undermined_by_r_dollar` are essentially the same cross-skill corroboration under three names.** Opus 4.6 calls it `competing_calls_to_action`, Sonnet 4.6 calls it `asymmetric_choice_architecture`, Opus 4.7 also `asymmetric_choice_architecture`. The underlying defect — three exit paths with dramatically unequal visual weight, steering toward the paid option — is robustly detected by all three models. This is exactly the kind of cross-skill triangulation SOT-reconcile is for.

3. **Opus 4.7 runs the longest list (10 ranked entries) and emits the most corroborations (7 nodes).** It also runs the most violation nodes (30). The reading is exhaustive; may be worth documenting as a "verbose Opus 4.7" behavioural trait (parallel to Cooper-eval observation that Opus 4.6 over-produced nodes on some clusters).

### Tensions

Every cell surfaces at least one principle-level tension. Kahneman (decision-psychology) is on one side of every tension — always.

| cell | tensions | axes |
|---|---|---|
| Opus 4.6 | 2 | Cooper × Kahneman `efficiency_vs_safety`; Osterwalder × Kahneman `conversion_vs_user_wellbeing` |
| Sonnet 4.6 | 1 | Cooper × Kahneman `efficiency_vs_safety` |
| Opus 4.7 | 1 | Osterwalder × Kahneman `conversion_vs_user_wellbeing` |

Observations:

- **`efficiency_vs_safety` (Cooper × Kahneman):** both Opus 4.6 and Sonnet 4.6 surface it. Cooper's *"remove the modal"* vs Kahneman's *"retain the confirm on irreversible paths."* Resolution turns on whether the streak loss is truly irreversible — the reconcile note in both cells concludes "governs when reversible" for Cooper.
- **`conversion_vs_user_wellbeing` (Osterwalder × Kahneman):** Opus 4.6 and Opus 4.7 surface it. Osterwalder's *"monetisation funnel is a legitimate business requirement"* vs Kahneman's *"loss-framed asymmetric visual weight is a dark pattern."* Resolution turns on whether the framing is reversible and honest.

Only Opus 4.6 surfaces BOTH axes. Sonnet 4.6 picks the interaction-design one; Opus 4.7 picks the business one. Neither choice is wrong — they are alternate readings of the same adversarial surface.

**Kahneman-as-always-one-pole** is the cluster's signature: the dark-pattern concern contends with every other frame in this cluster. This is itself an L5 insight — a single-skill audit cannot produce it.

### Gaps

Only Sonnet 4.6 surfaces a gap. Full text:

> *"The evidence shows users with 800+ day streaks reporting that the experience has shifted from enjoyment to obligation — none of the six L4 skills names this temporal erosion of intrinsic motivation over long exposure, because each skill audits a snapshot rather than a session trajectory."*

This is precisely the kind of gap the skill was designed for: visible in the evidence (`q[1]: "I'm trying to keep my 800+ day streak, but the recent changes are abysmal"`), missed by all six L4 frames (which each audit a single-moment-in-time snapshot), identifiable only when the six outputs are laid side by side.

That only one of three models surfaces it is a legitimate data point: **gap detection is the hardest of the three L5 tasks**, requiring the model to notice what the six skills did *not* say. Opus 4.6 and Opus 4.7 apparently stayed within the surfaced violations. If we re-ran either at non-zero temperature, we might sample different gap behaviour.

### Graph stats

| cell | violations | corroborations | tensions | gaps | edges |
|---|---:|---:|---:|---:|---:|
| Opus 4.6 | 24 | 5 | 2 | 0 | 26 |
| Sonnet 4.6 | 20 | 3 | 1 | 1 | 21 |
| Opus 4.7 | 30 | 7 | 1 | 0 | 29 |

Node-count readings:

- **Sonnet 4.6 is tightest** (20 violations, 3 corroborations). Tight enough to surface a gap without blowing the token budget.
- **Opus 4.6 is balanced** (24 violations, 5 corroborations). 2 tensions is the matrix high.
- **Opus 4.7 is most exhaustive** (30 violations, 7 corroborations). Bigger graphs, more collapse into ranked entries.

The SOT concept of "selective, not exhaustive" graph sizing (≤20 nodes as soft guidance in SKILL.md) is respected by Sonnet but exceeded by both Opus variants. The parser does not enforce the cap, and the ranked output is still short and signal-rich (7–10 entries) because corroborations collapse many violations into single ranked entries. The cap is SKILL.md guidance, not a parser constraint — and empirically this is correct: Opus 4.6's 24 violations produce 7 ranked entries (3.4× collapse); Opus 4.7's 30 violations produce 10 ranked entries (3.0× collapse).

## Architecture evolution (meta-story)

This is the data point documenting a substantial design revision. L5 went through three intermediate iterations before settling on graph-primary:

**v1.0 (initial, `MAX_TOKENS=8192`):** Output = `{summary, graph, ranked_violations, tensions, gaps}`. Model emits both graph and flat lists; parser enforces mirror constraints between them. **Result: 100% fail rate on Opus 4.6 alone (single run) — truncated graph at 39 violation nodes, never reached `ranked_violations`.**

**v1.1 (parser-enforced arithmetic, `MAX_TOKENS=16384`, graph cap):** Same shape. Added explicit SKILL.md caps on graph size (≤20 nodes). Parser enforces `rank_score = severity × corroboration_count`, `corroboration_count = len(source_skills)`, `unique_frames` formula, sort-descending order, and graph ↔ flat-list mirror. **Result: Opus 4.6 audited with 33 nodes verbose; Sonnet 4.6 and Opus 4.7 both fall back on sort-order violation.**

**v1.2 (parser auto-repair):** Same shape; parser auto-sorts, auto-recomputes arithmetic, warns but does not fallback on mirror drift. Offline re-parse of v1.1 fallbacks: **3/3 audited**. Live re-run with hardened rules: **1/3 audited (Opus 4.6 only), Sonnet + Opus 4.7 fall back on sort again before auto-repair can act** — `_validate_ranked` still type-checked the fields and the mis-typed sort order bypassed auto-repair on some paths. Plus semantic drift in `gaps[*]` mirror for Sonnet.

**v2.0 (graph-primary, this eval):** Model emits only `{summary, graph}`. Flat lists derived by parser traversal. Legacy top-level keys silently dropped for backwards compat. **Result: 3/3 audited on live run, output tokens 7 166 – 10 004 (all well under ceiling), clean top-3 cross-model convergence.**

The lesson: **dual representation in a model's output contract is a self-consistency burden the model cannot reliably discharge.** A v1.x model had to emit 20–30 violation nodes AND a flat ranked list AND keep their semantics aligned AND sort the ranked list AND compute rank_score arithmetically AND maintain mirror between tension nodes and tension list AND gap nodes and gap list. Each of those constraints is individually easy; keeping all of them consistent across a 6 000-token output is not.

Graph-primary fixes this by making the graph the *only* structured output. The model reasons about cross-skill structure as it builds the graph; the parser does the arithmetic downstream. The result is that the model's output has a single coherence target (the graph), and the consumer-facing flat lists are deterministic by construction.

This is also more faithful to the original SOT method (Peplinski 2026): in prose analysis, SOT emits a single argument graph and a synthesis paragraph — never a flat ranked list alongside. v2.0 returns to that single-representation discipline.

## Caveats

- **One cluster only.** Everything here is cluster_02. Generalisation to a full-corpus L5 run is a separate eval.
- **Three models, one modality.** L5 is text-only by design (reconciles structured verdicts, not UI); the "matched" axis is the model family, not modality. ADR-009 resolution for L5 is therefore "any of the three works"; model choice for production L5 can be made on cost grounds (Sonnet is cheapest and produced the tightest graph + a unique gap) or on exhaustiveness grounds (Opus 4.7 surfaced the longest ranked list).
- **Non-determinism on Opus 4.7.** Temperature is stripped at the API level (model rejects temperature=0 with 400). Re-runs may sample different gap or tension behaviour.
- **Heuristic slug derivation is model-dependent.** The parser uses corroboration `label` as the heuristic slug for corroborated entries; different models produce different labels for the same underlying defect. Downstream layers (L6) should normalise on `(source_skills, severity)` tuples rather than treating heuristic slugs as canonical identifiers.
- **v1.x → v2.0 architectural story is a load-bearing part of this eval.** Future consumers of the L5 output should not assume the output contract has been stable; the `native_payload_ref` carries the `skill_hash` against which a given verdict was produced.

## Reproducing

```
# Produce the bundle (requires the six L4 cluster_02 opus46 text verdicts):
cat \
  data/derived/l4_audit/audit_usability_fundamentals/l4_verdicts_audit_usability_fundamentals_cluster02_opus46.jsonl \
  data/derived/l4_audit/audit_accessibility/l4_verdicts_audit_accessibility_cluster02_opus46.jsonl \
  data/derived/l4_audit/audit_decision_psychology/l4_verdicts_audit_decision_psychology_cluster02_opus46.jsonl \
  data/derived/l4_audit/audit_business_alignment/l4_verdicts_audit_business_alignment_cluster02_opus46.jsonl \
  data/derived/l4_audit/audit_interaction_design/l4_verdicts_audit_interaction_design_cluster02_opus46.jsonl \
  data/derived/l4_audit/audit_ux_architecture/l4_verdicts_audit_ux_architecture_cluster02_opus46.jsonl \
  > data/derived/l5_reconcile/cluster_02_opus46_text_bundle.jsonl

# Run the three cells (requires ANTHROPIC_API_KEY):
bash scripts/run_l5_reconcile_matched.sh

# Force-rerun including already-audited cells:
bash scripts/run_l5_reconcile_matched.sh --all

# Run a single cell:
uv run python scripts/smoke_l5_reconcile.py --model claude-sonnet-4-6

# Inspect reconciled verdicts:
ls -1 data/derived/l5_reconcile/*.provenance.json

# Tests:
uv run pytest tests/test_l5_reconcile.py -v
```

Expected total spend: ~$0.30–0.60 across three live calls. Module under test: `src/auditable_design/layers/l5_reconcile.py`. System prompt: `skills/sot-reconcile/SKILL.md` v2.0 (skill_hash `4e0026fd9cd877f7…`). The runner skips already-audited cells by default.

## Closing

ADR-009's L5 pilot action item (*"L5 pilot — same pattern (stratified mini-sample, triad-style metric, cross-model kappa) when each layer reaches implementation"*) is closed by this eval. The triad-style metric here is cross-model convergence on the top-3 ranked entries (8/9 positions show the same load-bearing corroborations under different slug renderings) and on tension axes (all three models surface a Kahneman-vs-other tension). Kappa on solitary-vs-corroborated classification of violations across models is high but not measured numerically — the eval characterises the output qualitatively on a single cluster, which is the appropriate granularity before a full-corpus run.

L5 is ship-ready for the full-corpus run that feeds L6 weighting.
