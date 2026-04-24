# Auditable Design — Value Proposition

## Positioning

Auditable Design is a **product-integrated research-and-validation
agent**. It sits between the raw user-feedback stream and the
design team's backlog, producing evidence-backed direction briefs
— direction because the brief is a starting point for design
work, not a specification; evidence-backed because every finding
carries a typed chain back to the user complaints and product
artifacts that informed it. The briefs are grounded in the actual
product and verified against anchored heuristic metrics by a
multi-model ensemble. The agent
augments the existing design organisation — researcher, designer,
engineer — rather than occupying any of their seats; the brief is
an input to human work, not its replacement.

## The problem the agent addresses

Product organisations collect user feedback at scale — reviews,
support tickets, NPS comments, community threads, Slack messages.
This corpus contains high-value signal about where the product
fails users, but extracting that signal is expensive and slow.

Two adjacent tooling categories attempt the job and leave a
specific gap:

- **Qualitative research platforms** (Dovetail, UserTesting, Maze,
  Lookback) excel at organising interviews, unmoderated tests, and
  video sessions. They surface deep empirical insight on the
  problems they cover — but each study covers a narrow slice by
  design (typically around fifteen participants over a few weeks,
  at programme costs in the tens of thousands of euros). The
  depth-per-study is the point; the coverage-per-month is bounded
  by it.
- **Naive LLM summarisation** (ChatGPT on a CSV of reviews, generic
  AI assistants) is fast and cheap but produces prose recommendations
  with no evidence chain, no priority reasoning, and no audit trail.
  Designers correctly distrust these outputs.

The gap is a tool that reads the full feedback corpus, grounds
each hypothesis in the actual product, measures pain through named
heuristics, and emits a direction brief with a traceable evidence
chain — at minutes-per-cluster inference cost and pennies-per-run
spend. This gap is what Auditable Design fills.

A note on category comparison up front: qualitative research
platforms and the agent produce **different deliverables**, not
substitutes. A user study surfaces qualitative insight from real
people; the agent surfaces measurable pain in the product with a
self-verified direction (ensemble-internal; see §Validation limits
for what that does and does not guarantee). The cost and time
figures throughout this document are **scale comparisons** (how
many problems can be explored per month at what spend), not
substitutability comparisons (which method to pick). Both
categories coexist in a mature design org.

## What the agent does

The agent reads the user-feedback corpus, identifies recurring
aggregates of friction (not individual complaints, but patterns),
and forms working hypotheses about where pain exists on the
product.

It then **grounds those hypotheses in the product itself**. In the
target architecture the agent has first-class access to the product
ecosystem — code repository, Figma design files, usage analytics,
DOM of running pages, screenshots of actual production surfaces —
via MCP connectors. The current hackathon implementation
demonstrates this principle with a minimal grounding step
(screenshot + DOM evidence on a pilot cluster); full production
integration with code/Figma/analytics is explicitly scoped as v2.
The distinction matters: the agent is designed from the start to
operate on real artifacts, not on model-inferred prose about them.

Once grounded, the agent measures the pain through six
design-theory lenses — Norman (usability fundamentals), WCAG
(accessibility), Kahneman (decision psychology), Osterwalder
(business alignment), Cooper (interaction design), and Garrett (UX
architecture). The choice of six is deliberate coverage over a
breadth of concern (cognition, accessibility, behaviour, business,
interaction, strategy) rather than an exhaustive list. Alternative
lenses (Fitts, Gestalt, Nielsen's ten) are plausible extensions;
the framework is additive, not exclusive. Each lens produces
per-heuristic severity scores on an anchored 0–9 scale
(`{0, 3, 5, 7, 9}`) designed for cross-run reproducibility rather
than for calibrated intensity measurement. A reconciliation step
collapses cross-lens evidence into a single ranked list of named
pain spaces. A priority step weights those spaces by severity,
reach, persistence, business impact, and cognitive cost.

For the highest-priority space, the agent proposes a design
direction — a concrete description of the surface as it should be
after the change. That direction is then re-evaluated: the agent
re-audits the proposed after-state against the same heuristic list
and runs a verifier (weighted Tchebycheff scalarization, with Pareto
dominance as a sanity baseline) to confirm that the direction
**self-verifies** (ensemble-internal; external validation is
deliberately out of scope — see §Validation limits). "Self-verifies"
means the direction reduces the measured pain under the same
ensemble that produced the measurement; it is a proof of internal
consistency, not of user impact. If the verifier rejects the
direction, the agent iterates; rejected attempts stay in the audit
log.

The final output is a **evidence-backed direction brief** — a
structured document containing the named pain spaces with grounded
evidence, the self-verified direction (ensemble-internal) with its
proof-of-pain-reduction, the priority reasoning, references to the
concrete product artifacts (file paths, component names, Figma
frame URLs, analytics event queries), the audit trail of rejected
iterations, and a confidence score. The brief is direction, not
specification: the designer translates it into wireframes,
components, and flows in their own tooling.

## Worked example — cluster_02 (Duolingo streak-loss modal)

The pilot cluster aggregates ten user reviews complaining about a
mid-session paywall blocking lesson completion on streak loss. The
pipeline's pass across three models:

| Stage                                            | Severity sum (Opus 4.7) |
|--------------------------------------------------|-------------------------|
| L5 reconciled baseline (7 named heuristics)      | 57                      |
| L8 thin-spine (one L7 decision → one re-audit)   | 11                      |
| L8 loop (iterative refinement with verifier)     | 0                       |
| Naive single-shot baseline (B1, same model)      | 3                       |

On the same cluster with Sonnet 4.6 the trajectory is 57 → 11 → 6,
and naive B1 scores 19. Two read-outs: (1) iterative refinement
beats single-pass audit for the final state on every model;
(2) on weaker models the full pipeline strongly outperforms naive
at every stage, on the strongest model naive is competitive with
the loop (0 vs 3 out of baseline 57, a five-point gap whose
statistical significance on n=1 we do not claim — see §When the
agent may be wrong). The audit trail behind the final direction
— DAG of 107 nodes and 109 typed edges — is the durable
deliverable; the severity numbers are a byproduct of the
measurement process, not the output.

**Wall-clock** for this cluster on Opus 4.7 end-to-end (L3b → L8
loop) was roughly 4–6 minutes of inference time; the full 3-model
matched-evaluation grid (3 models × all layers × all matched cells)
took approximately 40–60 minutes. These are pipeline-execution
times; human review of the resulting brief is a separate step
outside the agent.

See `docs/evals/baseline_b1_matched.md` and
`docs/evals/l8_loop.md` for the full per-heuristic matrices.

## Who uses the output

The brief is consumed by a designer or engineer, who combines it
with their product knowledge, constraints, and aesthetic judgment
and produces the actual prototype in their own tooling — Claude
Code, Figma, Linear, whatever the team uses. The agent does not
commit. It does not merge. It does not ship. Its work ends at the
brief, and the human team retains all authorship of what follows.

## Differentiators

**Against naive LLM review**: Auditable Design grounds every
observation in real-product artifacts rather than in model-inferred
prose about those artifacts. It produces measurable severity per
named heuristic rather than qualitative impressions. It
self-verifies proposed directions against the same metric baseline
rather than asserting that a generated redesign is better.

**Against qualitative research platforms** (Dovetail, UserTesting,
Maze, Lookback): Auditable Design processes the full feedback
corpus rather than a sampled panel, at minutes-per-cluster
inference cost. It complements them — the agent surfaces and
pre-prioritises measurable pain spaces, which a design org can
then route to qualitative methods for deep empirical inquiry.
Neither category substitutes for the other: the qualitative
platform does what it does best (moderated discovery, usability
testing, longitudinal studies) with real users; the agent does
scalable corpus-level pain-space scoping with design-theory
framing.

**Against product analytics platforms** (Amplitude, Mixpanel,
PostHog): Auditable Design explains *why* users complain in terms
connected to design theory, not only *what* happens in event
streams. It proposes directions to address the complaint and
verifies those directions before handoff.

## Competitive landscape

*Claims in this section describe each tool's primary positioning
as observed through public product pages and documentation
available up to Q2 2026. The market moves quickly; specific
feature statements may be outdated by the time a reader encounters
them. Where a named product has since added overlapping
capability, the positioning claim should be read as "this was not
that tool's core offering" rather than "the gap is permanent".*

No single existing tool combines corpus-scale feedback processing,
real-product grounding via code/Figma/analytics, multi-lens
heuristic measurement, and verifier-gated iterative refinement
into one traceable output. Dovetail and similar research platforms
position around AI-assisted analysis of feedback — clustering,
tagging, video-transcript synthesis — but the product-grounding
and multi-lens measurement stages of Auditable Design sit outside
their current scope. Maze and UserTesting run real tests with real
users but operate downstream, after the direction has been chosen.
Analytics platforms (Amplitude, Mixpanel, PostHog) see behaviour
without interpretive design-theory framework. Naive LLM assistants
generate prose without evidence chain.

Auditable Design occupies the middle: research-grade rigor at
LLM-scale cost, with a traceable brief as the handoff artifact.
The closest adjacencies are AI-augmented research-ops tools
(AirOps, Reforge's research assistant), which overlap on
corpus analysis; at the time of writing these tools are not
documented as integrating real-product grounding or multi-lens
heuristic measurement, though either may ship such capability
before the reader arrives here.

## When the agent may be wrong

A brief with high internal confidence can still be wrong. Known
failure modes the designer should watch for:

1. **Feedback-corpus bias.** Only vocal users write reviews. If the
   cluster reflects a minority with strong preferences, the brief
   reflects that minority. The agent reports which reviews informed
   each finding; designers should sanity-check whether the corpus is
   representative before acting.

2. **Self-referential validation.** The verifier is internal to the
   model ensemble — the same family of models generates and scores
   the direction. A direction that self-verifies at severity 0 may
   still fail a real user study. The "proof of pain reduction" is a
   proof of ensemble consistency, nothing more. Treat it as a
   starting hypothesis, not a shipped result.

3. **Prose-vs-product drift.** Until full code/Figma/analytics
   integration (v2), much of the grounding is partial. If the
   brief's evidence cites prose but not code-file-paths or
   frame-URLs, the grounding is thin and the direction is a
   hypothesis about the product, not a diagnosis of it.

4. **Cross-lens arbitrariness.** Six lenses cover broad ground but
   not everything. A cluster with acute performance or
   internationalisation concerns may have no strong lens defending
   it; the brief will underweight such pain. Designers should
   flag missing lenses and escalate accordingly.

5. **Priority mis-weighting.** L6 weights severity × reach ×
   persistence × business impact × cognitive cost equally by
   default. Teams with strong preferences (e.g. "accessibility
   non-negotiable") should adjust weights before reading the brief.

The brief does not shield the designer from these failure modes;
it describes them, names which evidence supports which finding,
and makes it easy to disagree with the machine on the basis of
visible data.

## Validation and its limits

The agent proves that its proposed direction reduces the measured
pain. This is validation **internal to the model ensemble**: the
same family of models that generated the direction also scores its
reduction. External validation — human UX researcher review, A/B
testing on live traffic, longitudinal user studies — is not part
of the agent's scope and is not claimed. The brief produces a
strong starting point; the product team retains full
responsibility for deciding whether the direction is correct and
whether the implementation succeeds.

This is the honest framing and is stated to the designer in every
brief. The alternative — implying that ensemble consistency is
equivalent to user-facing validation — would erode the trust on
which the entire handoff depends.

## Scope — what exists and what is planned

The hackathon implementation is the research-and-validation
backbone. Feedback ingestion, clustering, multi-lens audit,
reconciliation, priority, direction generation, and iterative
validation with loop-based refinement are all built and evaluated
across three models (Opus 4.6, Sonnet 4.6, Opus 4.7).
Cross-layer traceability is operational: every iteration carries a
typed path back to the original informing user complaints.

Three components complete the vision and are explicitly scoped as
follow-on work. **Real-product grounding**: the hackathon
demonstrates the principle with screenshot + DOM evidence on a
pilot cluster; production-grade integration via MCP connectors to
Figma, GitHub, and analytics platforms is the v2 roadmap. **Design
brief aggregator**: the final-mile artifact that collapses
per-layer outputs into a single document structured for the
designer. **Bidirectional feedback**: the v3 extension where a
designer's rejection of a direction (with reason) flows back into
the pipeline and drives re-proposal.

## Costs and adoption

Inference cost for a full pipeline pass on one cluster on Opus 4.7
is approximately $0.50–$1.00 (rough order of magnitude from the
hackathon's matched-model evaluations; Sonnet 4.6 runs are
roughly 4–5× cheaper, see `data/derived/**/provenance.json` files
for per-layer breakdown). Scaling to a corpus of ten clusters is
therefore $5–$10 per full-grid run on Opus, $1–$2 on Sonnet.

This is a **scale number**, not a substitution number: qualitative
user-study programmes (tens of thousands of euros per study)
produce qualitative insight from real users which the agent cannot
substitute. The value of cheap runs is that many more clusters can
be routed through this research-preparation stage per month,
letting the expensive user-study budget be spent on the problems
most worth validating with real users.

The first adopter profile is a product or design lead at a SaaS
scaleup with a large public feedback surface (app reviews,
community forum, support inbox) who wants measurable pain-space
prioritisation before committing to a full user-study cycle. Solo
founders, enterprise UX-research teams, and consumer-app studios
all have the relevant feedback volume; the SaaS scaleup profile
is simply the one with the most available public
feedback-at-scale and tightest cycle between research and
implementation — it is a typical first adopter, not the only
viable one. Enterprise deployment requires the v2 product-
integration layer; smaller teams can use the hackathon backbone as
a standalone corpus analyser today.

## Summary

Auditable Design takes user feedback as signal, grounds it in the
actual product, measures pain through six reconciled design lenses,
proposes directions that self-verify against the measured pain
(ensemble-internal; external validation remains the product team's
responsibility), and emits evidence-backed direction briefs that
reference concrete artifacts with full audit trail. It is an agent
that makes design research and direction validation scalable,
traceable, and auditable — the brief is input for a human team's
work, and the process is designed to be disagreed with on the
basis of visible evidence, not accepted on trust.
