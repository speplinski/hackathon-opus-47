# Design brief — matched eval

The design brief is the pipeline's **shipping artifact** — the
single markdown document the designer opens and starts work from.
Zero Claude calls; pure aggregation of per-layer outputs the
pipeline already produced. Per the v4 value proposition in
`docs/value_proposition.md`, this is the final-mile deliverable
that makes the whole backbone useful to a real design team.

## Scope and grid

- **Cluster:** `cluster_02` (Duolingo streak-loss modal + energy
  paywall pressure).
- **Inputs (per model):** L5 reconciled verdict, L6 priority
  score, L7 design decision, L8 thin-spine iterations, L8-loop
  iterations (tchebycheff verifier by default), verify-on-product
  grounded evidence.
- **Models:** Opus 4.6, Sonnet 4.6, Opus 4.7 — matched grid with
  all layer outputs per model.
- **Grid:** 1 cluster × 3 models = 3 cells.
- **Cost:** $0.00 per cell — no inference; aggregator only.

## Headline

| Model       | Baseline sum (L5) | Final sum (loop) | Iters | Rejected | Verify included |
|-------------|-------------------|------------------|-------|----------|-----------------|
| Opus 4.6    | 57                | 0                | 3     | 0        | yes             |
| Sonnet 4.6  | 64                | 6                | 5     | 2        | yes             |
| **Opus 4.7**| **82**            | **0**            | 3     | 0        | yes             |

Two findings stand out immediately — neither was visible before
the brief aggregator was run across all three models.

### Finding 1 — L5 decomposition varies strongly by model

Baseline severity sums differ by 44 % across models on **the same
cluster** (cluster_02 aggregates the same 7–10 user reviews in
every run; reconciliation is what changes). Opus 4.7 decomposes
the complaint space into more heuristics / higher per-heuristic
severities than Opus 4.6; Sonnet 4.6 sits in between.

This is *not* a defect. It is a **measurement of the model's
interpretive rigour**. A stricter L5 run surfaces more named
pain spaces; a looser L5 run aggregates pain into fewer labels.
Both are defensible; the brief honestly reports which L5 run was
used, so the designer can calibrate trust accordingly.

The pipeline consumer (designer) should know which model ran the
L5 step, because a brief with baseline 82 and final 0 is a
stronger claim than a brief with baseline 57 and final 0 — the
former demonstrates the direction resolves more identified pain.

### Finding 2 — Sonnet 4.6 stalls; Opuses converge

Sonnet 4.6 produced 5 iterations with 2 rejected before the loop
stalled at final sum 6. Both Opus models converged cleanly in 3
iterations (baseline + L7-based iter 1 + one loop tweak) with
zero rejections and final sum 0.

This is consistent with prior loop-layer matched evals
(`docs/evals/l8_loop.md`): Opus-class models have enough tweak
capacity to resolve the reconciled pain space in a single loop
round; Sonnet needs more rounds and eventually saturates. The
brief's "Audit trail" section makes this transparent — the
designer sees all 5 Sonnet iterations including the 2 rejected
attempts, with verifier reasoning per rejection.

## Brief structure (all models)

Every brief contains the same 10 sections, populated from the
model-specific layer outputs:

1. **Header** — cluster id, label, model, verifier, review count,
   generation timestamp.
2. **Executive summary** — severity baseline → final, grounded
   verification breakdown, ensemble-internal caveat.
3. **User pain signal** — representative quotes + UI context +
   informing review IDs.
4. **Measured pain spaces** — table: heuristic × L5 sev ×
   grounded verdict × adjusted sev × evidence (when verify-on-
   product available).
5. **Priority reasoning** — L6 dimensions + meta-weights +
   weighted total.
6. **Validated direction** — L7 before/after snapshot + per-
   heuristic delta table + L7 resolves_heuristics list.
7. **Out-of-baseline observations** — VLM summary flagging
   defects visible on product but not in reconciled heuristic
   list (when verify-on-product available).
8. **Audit trail** — every loop iteration with status, severity
   sum, parent pointer, reasoning/rejection reason.
9. **Signal quality indicators** — transparent components
   (severity reduction %, loop convergence, grounded-evidence
   ratio). Deliberately not rolled up into a single score.
10. **Handoff notes** — explicit guarantees and non-guarantees
    of the brief; honest framing per v4 VP.

Plus a **Provenance** footer with sha256 of every input file.

## Per-model qualitative differences

### Opus 4.6 — efficient baseline

- 7 named heuristics, baseline sum 57 (lowest).
- Loop converges in one additional round; all 7 heuristics to 0.
- Verify-on-product: 5 confirmed, 2 partial, 0 refuted. Moderate.
- Brief is the tightest (lowest entropy) — fewer pain spaces
  named, all resolved cleanly.

### Sonnet 4.6 — broader decomposition but partial resolution

- 8 named heuristics, baseline sum 64.
- Loop makes 2 accepted moves + 2 rejected = final sum 6 (not 0).
- Verify-on-product: 6 confirmed, 1 partial, 0 refuted.
  Conservative ("yes to everything").
- Brief surfaces 2 rejected iteration attempts in audit trail —
  a designer using Sonnet brief sees what the pipeline *tried*
  but could not validate.

### Opus 4.7 — maximally strict and most critical at every stage

- 8 named heuristics, baseline sum 82 (highest).
- Loop converges in one round; all heuristics to 0.
- Verify-on-product: 4 confirmed, 2 partial, **1 refuted** —
  Opus 4.7 actively disagrees with one L5 hypothesis
  (`scarcity_timer` is visible only on non-blocking surface).
- Brief **flags out-of-baseline defects** in the VLM summary:
  "Super pre-selected with checkmark", "no pause/continue
  without energy affordance", "Recharge row low-contrast".
  These are candidates for the next clustering cycle.
- Most information-rich brief in the grid.

## Which model's brief should ship?

Depends on purpose:

- **Sales demonstration** — use Opus 4.7 brief. Highest severity
  signal (82 baseline → 0 final = 100 % reduction), explicit
  dissent on one L5 hypothesis (demonstrates real-product
  correction), out-of-baseline defect flags (demonstrates
  product-hook value-add).
- **Cost-optimised production run** — use Sonnet 4.6 brief with
  full awareness that it stalls. The designer who reads the
  audit trail will see the pipeline's limits honestly.
- **Balanced default** — Opus 4.6. Baseline not inflated, full
  resolution, moderate verify-on-product posture, stable.

The **matched grid itself is an artefact** — a product team
considering adopting the pipeline gains confidence from seeing
three independent model runs all produce convergent-but-not-
identical briefs. Disagreement patterns (e.g. Opus 4.7 refutes
scarcity_timer, Sonnet doesn't) are the honest signal.

## Costs

**Zero inference cost.** The brief aggregator does no Claude
calls — it reads files produced by upstream layers. Per-cell
wall-clock is ~1-2 seconds. Running the full grid costs
whatever was spent on the upstream layers (L3b → L5 → L6 → L7
→ L8 → L8-loop → verify_on_product), which for the hackathon's
cluster_02 pilot is approximately $1.50–$2.00 cumulative
across all three models.

## Scope gaps

1. **Single cluster.** Only cluster_02 has briefs across the
   3-model grid because it is the only cluster driven through
   the full spine. For a production run on N clusters, the
   aggregator would generate N × M briefs (M = models used);
   the matched grid is exploratory, not a monthly production
   run.
2. **No cross-model brief synthesis.** The matched grid produces
   three independent briefs. A future enhancement could emit a
   fourth "consensus" brief highlighting agreement vs divergence
   between the three — but that adds complexity the designer
   may not need if they already read the audit trail.
3. **No delta report between successive runs.** If the pipeline
   is rerun next month on an updated review corpus, there is no
   automated diff showing "heuristics added / removed / changed
   severity since last brief". Useful for long-running
   deployments, out of scope for the hackathon.
4. **No HTML / PDF rendering.** The brief is markdown. A
   design org preferring PDF or Confluence-ready HTML would
   pipe through pandoc; not bundled.

## Artifacts

- Script: `scripts/export_design_brief.py`
- Runner: `scripts/run_export_design_brief_matched.sh`
- Per-cell outputs:
  - `data/derived/design_brief/design_brief_cluster02_{opus46,sonnet46,opus47}.md`
  - `…{modelshort}.provenance.json` (input file sha256s, counts,
    verify_included flag)

## One-sentence takeaway

**The design brief closes the pipeline's handoff gap — one
markdown per (cluster, model) with named pain, validated
direction, grounded evidence, full audit trail, and honest
guarantees — and the matched-grid comparison itself is a second-
order signal showing how model choice shifts interpretive
rigour, iteration count, and dissent willingness.**
