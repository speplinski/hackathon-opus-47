# Auditable Design

**Built with Claude Opus 4.7.** Decision infrastructure for product
teams: preserves the chain of reasoning from raw user feedback to
defensible design direction.

> Feedback enters the product process as evidence.  
> It leaves as opinion.  
> Auditable Design keeps the reasoning intact.

Auditable Design reads user feedback, clusters recurring friction,
grounds it in the actual product surface, audits pain through six
design lenses, prioritises the problem, proposes a direction, and
re-audits that direction before handoff to a human designer.

The output is not a summary, not a generated UI, and not a shipped
decision. It is an **evidence-backed direction brief**: a structured
artifact that says what hurts, where it hurts, why it matters, what
direction could reduce it, and exactly which evidence supports every
claim.

This submission showcases Opus 4.7 where product agents need more
than fluent summarisation: **grounded dissent, false-positive
correction, and independent defect discovery.**

Submission for the **Anthropic Built with Opus 4.7** hackathon
(21–26 April 2026).

---

## Why this exists

Product teams do not lack feedback. They lack an auditable path
from feedback to decision.

A thousand complaints become five themes. The themes become a
roadmap item. The roadmap item becomes a backlog ticket. Six weeks
later, nobody can clearly defend why that decision was made.

Generic LLMs summarize the corpus. Research platforms organize
studies. Analytics tools show behavior.

Auditable Design creates the missing layer between feedback
analysis and product decision-making: a traceable argument for what
should change next.

Qualitative research platforms such as Dovetail, UserTesting, and
Maze are rigorous but narrow: a bounded study, a small participant
set, a specific research question. Naive LLM summarisation is fast
but produces prose recommendations with no evidence chain and no
audit trail that a designer can trust.

Between those poles, Auditable Design reads the **full** feedback
corpus, grounds each hypothesis in the **real** product, measures
pain through **named** heuristics, and emits a direction brief with
**traceable** evidence — at minutes-per-cluster inference cost.

The thesis is simple: **AI should not make design decisions for
teams. It should make the reasoning behind design decisions visible,
testable, and challengeable.**

That is what Auditable Design does.

See [`docs/value_proposition.md`](docs/value_proposition.md) for the
full positioning statement and [`docs/auditable_design_pitch.md`](docs/auditable_design_pitch.md)
for the submission pitch.

---

## What one run produces

The pipeline's shipping artifact is a single markdown document the
designer opens and starts work from.

**[Example — cluster_02 design brief on Opus 4.7](examples/design_brief_cluster02_opus47.md).**

Ten sections:

1. Executive summary — severity baseline, final state, grounded verdict breakdown
2. User pain signal — representative quotes and informing review IDs
3. Measured pain spaces — named heuristics, severity, grounded verdict, adjusted severity, evidence
4. Priority reasoning — five-dimensional weighted score
5. Validated direction — before/after snapshot and per-heuristic delta
6. Out-of-baseline observations — defects the product-grounding step saw that reviews did not name
7. Audit trail — every loop iteration, including rejected attempts and reasons
8. Signal quality indicators — transparent components, not a single opaque score
9. Handoff notes — what the brief guarantees and what it does not
10. Provenance — sha256 of every input file

---

## Key eval findings

All numbers come from the hackathon's matched-model grid on
`cluster_02`: Opus 4.6, Sonnet 4.6, and Opus 4.7 running the same
pipeline.

Full eval docs live under [`docs/evals/`](docs/evals/).

The eval question was not:

> Can Claude summarize reviews?

It was:

> Can an agent preserve, test, and correct the reasoning chain from
> feedback to design direction?

### The loop closes the gap

| Stage                                    | Severity sum — Opus 4.7 |
|------------------------------------------|--------------------------|
| L5 reconciled baseline — 8 heuristics    | 82                       |
| L8 thin-spine — one L7 decision re-audit | 11                       |
| L8 loop — iterative refinement           | **0**                    |
| Naive single-shot baseline — B1          | 3                        |

On the strongest model in the grid, iterative refinement beats both
the single-round audit and the naive single-shot baseline on the
same heuristic list.

The point is not the absolute severity number. The point is that
the pipeline can preserve a measurable pain baseline, generate a
direction, re-audit it, reject failed attempts, and converge toward
a state that reduces the same measured pain.

See [`docs/evals/l8_loop.md`](docs/evals/l8_loop.md) and
[`docs/evals/baseline_b1_matched.md`](docs/evals/baseline_b1_matched.md).

### Real-product grounding catches a false-positive — Opus 4.7 only

Opus 4.7 verifies the L5 hypotheses against real Duolingo
screenshots and **refutes** `deceptive_feedback__scarcity_timer`
(baseline severity 7 → 0).

The review-inferred hypothesis said that a scarcity timer was part
of the paywall pressure. The product evidence did not support that:
the 22h regeneration label lives on a non-blocking energy surface,
not on the blocking paywall modal where scarcity pressure would
matter.

Opus 4.6 softened the same hypothesis to partial severity 5.
Sonnet 4.6 softened it to partial severity 3.

**Neither refuted it.**

Only Opus 4.7 produced a clean correction the pipeline could act
on.

Without Opus 4.7's grounded dissent, the pipeline would have
shipped a direction addressing a defect that does not exist on the
paywall modal.

See [`docs/evals/verify_on_product_matched.md`](docs/evals/verify_on_product_matched.md).

### Grounding adds defects the feedback did not name — Opus 4.7 only

Opus 4.7 also flags three defects the L5 heuristic list did **not**
name:

1. The Super option appears pre-selected with a checkmark before
   user input — a default-bias dark pattern.
2. There is no "continue without energy" or "pause" affordance,
   leaving the punitive "LOSE XP" link as the only non-paid exit.
3. The Recharge row is rendered in low-contrast grey and may fall
   below WCAG minimums.

Sonnet 4.6 and Opus 4.6 verified the same screenshots and flagged
none of these.

This is the critical-analysis capability this submission is meant
to showcase: not summarisation or transformation of the input, but
**independent observation beyond what the input describes**.

---

## Why Opus 4.7

The pipeline needs a model that can disagree with its own upstream
evidence when product evidence contradicts it.

The matched-model grid is not a vendor comparison. Opus 4.6 and
Sonnet 4.6 run the same pipeline for contrast. The point is to show
which behaviours are necessary for this system to work.

The critical behaviours appear at the Opus 4.7 tier.

### Broader L5 decomposition

On `cluster_02`, Opus 4.7 produced a broader and more severe L5
decomposition on the same complaint corpus:

- Opus 4.7 baseline severity: **82**
- Opus 4.6 baseline severity: **57**

In this pipeline, that matters because the verifier has more
explicit pain spaces to test, reduce, or reject.

A higher number is not valuable by itself. It is valuable when the
resulting pain spaces are named, traceable, grounded, and available
to the refinement loop.

### Dissent-willingness in grounded verification

Three models verified the L5 hypotheses against real Duolingo
product screenshots.

| Model      | Confirmed | Partial | Refuted |
|------------|-----------|---------|---------|
| Sonnet 4.6 | 6         | 1       | 0       |
| Opus 4.6   | 5         | 2       | 0       |
| Opus 4.7   | 4         | 2       | **1**   |

The refuted hypothesis was not cosmetic. It was a genuine
false-positive.

The review corpus suggested scarcity-timer pressure, but Opus 4.7
observed that the 22h regeneration label lives on a non-blocking
surface, not on the paywall modal. That distinction matters because
the design direction should address the actual blocking interaction,
not an imagined defect.

### Independent out-of-baseline defect discovery

Opus 4.7 surfaced product defects not named in the L5 baseline:

- pre-selected Super checkmark
- missing pause / continue-without-energy affordance
- low-contrast Recharge row

These are signals the other models did not find and the feedback
text alone could not surface.

Over time, these observations can feed back into the clustering
cycle as new heuristic candidates.

### Capability allocation

The pipeline reflects this model profile:

- earlier-generation models remain useful for throughput-bounded
  layers such as L3b labelling or rule-like checks;
- Opus 4.7 is reserved for reasoning-heavy steps: L5 reconciliation,
  L7 direction generation, L8 iterative refinement, and
  verify-on-product grounding.

Its role is not to make the system more fluent.

Its role is to protect the system from confirmation-loop failure.

See [ADR-009](docs/ADRs.md#adr-009) for the full model-allocation
policy.

---

## Architecture at a glance

```text
raw user reviews
  │
  ├─ L1 classify          filter noise, route signal
  ├─ L2 structure         complaint graph
  ├─ L3 cluster           semantic grouping
  └─ L3b label            cluster name + representative quotes
        │
        └─ L4×6 audit     six design-theory lenses
           │              Norman, WCAG, Kahneman, Osterwalder, Cooper, Garrett
           │
           └─ L5 reconcile
              │            named pain spaces with severity
              │
              └─ L6 priority
                 │          priority-weighted ranking
                 │
                 └─ L7 decide
                    │        design direction per priority
                    │
                    └─ L8 optimize
                       │      verifier-gated iterative refinement
                       │      Pareto + weighted Tchebycheff
                       │
                       └─ verify_on_product
                          │    VLM verification against real product screenshots
                          │
                          └─ export_design_brief
                               final-mile ten-section markdown brief
```

See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full system design
and [`concept.md`](concept.md) for the central thesis.

---

## What exists vs what is v2 roadmap

### Hackathon implementation

Built and evaluated across three models:

- Signal extraction — L1–L3b
- Six-lens audit — Norman, WCAG, Kahneman, Osterwalder, Cooper, Garrett
- Cross-lens reconciliation — L5
- Priority scoring — L6
- Direction generation — L7
- Iterative refinement with verifier — L8 thin spine + L8 loop
- Evolution DAG with typed traceability — L10
- Real-product grounding MVP via VLM on screenshots
- Design brief aggregator

### Post-hackathon roadmap

- Full MCP connectors to code repository, Figma, and analytics platforms
- Bidirectional feedback: designer rejection → pipeline re-proposal
- Cross-model re-audit: generate with one model family, verify with another
- Multi-cluster batch runs

---

## Reproducing the pipeline

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11 or 3.12.

```bash
uv sync --extra dev
uv run pytest -q
```

The test suite runs offline: no network and no API key required.

### Running layers locally

Layer-by-layer smoke scripts for `cluster_02` on the matched grid:

```bash
bash scripts/run_l4_*_matched.sh
bash scripts/run_l5_reconcile_matched.sh
bash scripts/run_l6_weight_matched.sh
bash scripts/run_l7_decide_matched.sh
bash scripts/run_l8_optimize_matched.sh
bash scripts/run_l8_loop_matched.sh
bash scripts/run_verify_on_product_matched.sh
bash scripts/run_export_design_brief_matched.sh
bash scripts/run_baseline_b1_matched.sh
```

Each runner iterates the three-model grid:

- Opus 4.6
- Sonnet 4.6
- Opus 4.7

Outputs are written to:

```text
data/derived/<layer>/<layer>_<cluster>_<model>.*
```

Each output has a `.provenance.json` sidecar with input hashes.

### Replay mode

For end-to-end reproducibility, the Claude client supports
`mode="replay"` using:

```text
data/cache/responses.jsonl
```

Every Claude call from the committed replay log is reproduced
byte-identical.

See [`ARCHITECTURE § 11.5`](ARCHITECTURE.md#115-what-a-reviewer-sees)
for the reviewer path.

---

## Honest limits

This is what the agent tells the designer.

1. **Feedback-corpus bias**  
   Only vocal users write reviews. The brief reflects the available
   corpus, not the whole user base.

2. **Self-referential validation**  
   The L8-loop verifier is internal to the model ensemble.
   "Direction self-verifies" means ensemble consistency, not
   user-facing validation. The verify-on-product step partially
   mitigates this by inspecting real product pixels, but it is still
   not external validation by humans or live users.

3. **Prose-vs-product drift**  
   The hackathon MVP grounds against screenshots and DOM evidence.
   Full code, Figma, and analytics grounding is v2.

4. **Cross-lens arbitrariness**  
   Six lenses cover broad ground, but not every defect class.
   Performance, internationalisation, and infrastructure issues may
   need additional lenses.

5. **Priority mis-weighting**  
   Default L6 weights are equal across dimensions. Teams with strong
   preferences, for example accessibility as non-negotiable, should
   adjust weights before acting.

External validation — A/B testing, longitudinal studies, human UX
researcher review — is **not** part of the agent's scope and is
**not** claimed.

The point is not to hide these limits.

The point is to make disagreement possible on the basis of visible
evidence.

---

## Repository layout

```text
src/auditable_design/
├── layers/             L1–L10 pipeline modules
├── evaluators/         Pareto and weighted Tchebycheff gates
├── schemas.py          Pydantic contracts for cross-layer artifacts
├── claude_client.py    live/replay transport with cache-as-replay-log
└── storage.py          atomic writes and sha256 manifests

skills/                 SKILL.md prompt bundles
scripts/                runners and matched-grid smoke scripts
tests/                  unit and replay-mode tests

docs/
├── value_proposition.md
├── auditable_design_pitch.md
├── evals/
└── reviews/

examples/               whitelisted output samples
data/derived/           gitignored; populated by pipeline runs
data/cache/responses.jsonl
```

---

## Further reading

- [Value proposition](docs/value_proposition.md) — positioning statement
- [Pitch](docs/auditable_design_pitch.md) — hackathon submission pitch
- [Concept](concept.md) — central thesis and design principles
- [Architecture](ARCHITECTURE.md) — system design
- [Implementation plan](IMPLEMENTATION_PLAN.md) — day-by-day hackathon plan
- [Architectural decisions](docs/ADRs.md) — ADRs recorded during development
- [Eval docs](docs/evals/) — per-layer matched-model findings

---

> The point is not to automate taste.  
> The point is to make product reasoning inspectable.
>
> Not louder recommendations. Better accountability.

*Submission 1.0 — 27 April 2026.*
