# Auditable Design

**A methodology for feedback-driven design that can be audited, challenged, and defended.**

**Project concept — Built with Opus 4.7 Hackathon**
**Author:** Szymon P. Pepliński
**Event dates:** 21–26 April 2026
**Status:** source of truth for implementation

---

## 0. Important context

Everything in this system is built from zero during the hackathon. No pre-existing code is reused. The author has prior methodological experience with Claude Code skills, audit frameworks, and argument-graph analysis (SOT methodology) — this experience serves as **benchmarking reference and methodological grounding**, not as starting codebase. Every skill, every script, every UI component is committed during the 21–26 April window.

This matters for three reasons:
- Submission represents full scope of what was built during the event
- No hidden assets outside the submitted repository
- "Build from what you know" from the hackathon brief refers to methodological knowledge, not existing files

---

## 1. Central thesis

A system that transforms raw user voice into justified, traceable design decisions — through structural analysis of feedback, audit by canonical design theory, and iterative redesign optimization with full traceability from specific complaints to specific UI components.

Three sub-theses:

**T1.** An AI-driven auto-research loop can produce design insights comparable to traditional user research — faster, and with traceability that classical research does not offer.

**T2.** Structural analysis of feedback (typed argument graphs) is methodologically superior to conventional summarization, because it preserves logical relationships between complaints, expectations, and their consequences.

**T3.** Traceability between a specific user complaint and a specific design decision is the key differentiator from other AI design tools (v0, Galileo, Uizard), which generate without justification.

**T4 (added via optimization loop).** Design quality can be improved iteratively by treating canonical design heuristics as a loss function, producing a gradient-free optimization process with native convergence criteria and complete audit trail.

## 2. Case study

Real corpus of Duolingo reviews (~500–1000 items from Google Play, last 12 months).

**Framing:** "Proof of method on real feedback from a popular app" — not "critique of Duolingo". Every insight in the demo must be anchored in a literal quote from a specific, publicly available review. The system demonstrates a reproducible method; the choice of Duolingo is instrumental, not targeted.

**Justification for case selection:**
- Publicly documented AI-first crisis (2025), strong user backlash, rich corpus of UX-relevant complaints (~50% of corpus)
- Product recognizable by jury without need for explanation
- Reviews are publicly accessible, no scraping ethics issues
- Strong meta-resonance: AI used to amplify user voice in a product that publicly retreated from user voice

## 3. Full pipeline — ten layers

```
[1] Classification                    (UX-relevant vs rest)
[2] structure-of-complaint            (per-review mini-graph — new skill)
[3] Aggregation                       (insight clusters — embedding + clustering)
[4] Multi-perspective audit           (6 canonical skills × cluster)
[5] SOT meta-reconciliation           (argument graph over skill verdicts)
[6] Business weighting                (5-dimensional scoring)
[7] Decision generation               (initial redesign proposal)
[8] Optimization loop                 (iterative improvement via canon metrics)
[9] Final redesign                    (highest-scoring version from log)
[10] Evolution graph                  (time-ordered rationale trail for demo)
```

Layers 1–6 are **setup phase** (feedback → weighted insights).
Layers 7–9 are **optimization phase** (iterative redesign with measurable quality).
Layer 10 is **narrative artifact** (demo-facing visualization of the entire process).

## 4. Layer 1 — Classification

**Purpose:** separate UX-relevant reviews from content, billing, and customer-service complaints.

**Mechanism:** Claude prompted with explicit rubric. UX-relevant = interface, layout, navigation, interaction, visual design, flow, paywall UX. Not UX = grammar errors in lessons, billing disputes, support unresponsiveness, pedagogical content quality.

**Expected split:** approximately 50% UX-relevant, based on preliminary analysis of Trustpilot and Google Play samples.

**Success signal:** 40–60% of corpus classified as UX, distribution passes manual spot-check on 20-review sample.

## 5. Layer 2 — `structure-of-complaint` (new skill)

**Purpose:** transform short, emotional review into a structured mini-graph of design-relevant insights.

**Rationale for a dedicated skill:** SOT was designed for long-form argumentative texts (500–20,000 words, single author, unified thesis). Reviews are short, fragmentary, non-argumentative — direct application of SOT would be a misapplication. `structure-of-complaint` is designed specifically for feedback data, with its own typed vocabulary.

**Node types:**
- `pain` — experienced problem
- `expectation` — unmet expectation (often implicit)
- `triggered_element` — specific UI element causing the complaint
- `workaround` — how the user navigates around the problem
- `lost_value` — what the user lost (time, motivation, money, streak)

**Relation types:**
- `triggers`, `violates_expectation`, `compensates_for`, `correlates_with`

**Critical constraint:** every node must quote an exact substring from the source review. Nodes cannot be generated "from context" — this is a structural defense against hallucination.

**Output:** 3–7 typed nodes per review, with mapping to `raw_review_id`.

**Implementation note:** this skill is authored during the hackathon, following the methodological patterns the author uses in other skill design. SKILL.md, node type definitions, example graphs, and reconciliation logic are all committed within the event window.

## 6. Layer 3 — Aggregation

**Purpose:** reduce 500–1000 mini-graphs into 5–8 insight clusters.

**Mechanism:** embed `pain` and `expectation` nodes using sentence transformers (local models, runs on H100). Cluster using HDBSCAN (primary) or KMeans (fallback). Cluster labels generated by Claude from representative quotes within each cluster.

**Success signal:** 5–8 clusters, each containing minimum 20 reviews, cluster names are interpretable and non-overlapping.

## 7. Layer 4 — Multi-perspective audit (6 canonical skills)

**Purpose:** each insight cluster is audited in parallel through six canonical design perspectives, providing multi-dimensional critique grounded in established design theory.

**The six perspectives:**

- **`audit-usability-fundamentals`** — Norman's ten usability heuristics (visibility of system status, match to real world, user control, consistency, error prevention, recognition over recall, flexibility, aesthetic minimalism, error recovery, help documentation)
- **`audit-interaction-design`** — Cooper's interaction patterns (posture, idiomatic controls, flow, excise elimination, personas)
- **`audit-ux-architecture`** — Garrett's five planes (strategy, scope, structure, skeleton, surface) and inter-plane coherence
- **`audit-decision-psychology`** — Kahneman's cognitive frameworks (cognitive load, choice architecture, loss aversion, framing effects, dual systems)
- **`audit-business-alignment`** — Osterwalder's Business Model Canvas (value proposition, channels, revenue streams, customer relationships)
- **`audit-accessibility`** — WCAG 2.2 criteria, Inclusive Design Principles, cognitive accessibility (ADHD, dyslexia, neurodivergent considerations)

**Rationale for six perspectives:** accessibility is not "nice to have" — in 2026, with the European Accessibility Act in force, it is regulatory. Treating accessibility as an equal perspective alongside usability, interaction, architecture, psychology, and business alignment reflects how design audit actually needs to work now. It is canon, not bonus.

**Shared structural contract across all six skills:** each skill accepts the same input (insight cluster + UI context), returns verdicts in the same YAML structure (violated heuristics, severity 0-10, evidence reviews, reasoning). This uniformity is a design decision — it makes the audit layer composable and extensible, even though all six skills are part of the canonical set for this hackathon submission.

**Crucial adaptation for all six skills:** canonical skills traditionally audit an *existing UI* (screenshots, wireframes). Here they audit an *insight cluster in the context of a UI*. The input is richer (UI description + user voice), the heuristic framework is unchanged. This is an extension of use, not a distortion of method.

**Output per skill per cluster:**
```yaml
insight_cluster: <cluster_id>
audit_verdict:
  skill_id: <skill name>
  relevant_heuristics:
    - heuristic: <heuristic name>
      violation: <specific violation description>
      severity: <0-10 score>
      evidence_reviews: [<review_ids supporting the verdict>]
      reasoning: <brief justification>
```

**Demo strategy:** three backbone skills shown frontally — **Norman (usability), Kahneman (cognition), Accessibility (inclusion)** — together these three capture the clearest multi-perspective signal without overloading the demo surface. Cooper, Garrett, and Osterwalder available in drill-down. Six perspectives present, three amplified.

**Future direction (not part of hackathon scope):** the uniform structural contract across these six skills naturally extends to a plugin architecture — new audit perspectives (regulatory compliance, medical UX, gaming ethics, sector-specific frameworks) could be added without architectural change. This is natural post-hackathon work, referenced in the future work section but not part of the current submission.


## 8. Layer 5 — SOT meta-reconciliation

**Purpose:** identify agreements and tensions across skill verdicts, prioritize heuristic violations.

**Why SOT fits naturally here:** skill verdicts are *structured argumentative texts* with implicit theses (this heuristic is violated), claims (severity levels), evidence (review quotes), and method (canonical heuristic framework). This is exactly the kind of text SOT was designed for — unlike individual reviews, which are not.

**Output:** ranked list of violated heuristics per cluster, with explicit tensions between design schools highlighted (potential demo moment: "legitimate tension between Norman's user_control and Cooper's idiomatic_control on this affordance").

**Implementation note:** SOT is authored during the hackathon. Node types (thesis, claim, assumption, contradiction, gap) and relation types (supports, contradicts, elaborates, evidences, assumes, questions) are defined within the event window. Methodology follows patterns the author has previously developed and benchmarked against.

## 9. Layer 6 — Business weighting

**Purpose:** assign a priority weight to each cluster of heuristic violations.

**Mechanism:** 5-dimensional scoring by Claude using explicit rubric plus a **public context document** about Duolingo (Duolingo model of freemium, industry KPIs, publicly known strategy, AI-first crisis) with each claim footnoted to a public source.

**Five dimensions (each 0–10):**
- `user_volume_affected` — percentage of reviews mentioning this insight
- `severity_of_pain` — emotional intensity of complaints
- `retention_risk` — explicit churn or deletion mentions in reviews
- `conversion_risk` — impact on paywall or upgrade moments
- `brand_signal` — reputational weight, virality potential

**Meta-weights (explicit, editable via demo UI):**
- severity_of_pain × 1.0
- user_volume_affected × 1.2
- retention_risk × 1.5
- conversion_risk × 1.3
- brand_signal × 1.1

**Validation:** double-pass consistency check. If difference greater than 1 point on any dimension between passes, run a third pass and use median.

**Transparency by design:** weights and meta-weights are visible in the UI as interactive sliders. Rankings update in real time as meta-weights are adjusted. This turns arbitrariness (which weight dimension matters most) into a feature (transparency and interactivity).

## 10. Layer 7 — Decision generation (initial)

**Purpose:** produce initial redesign proposals grounded in prioritized violations.

**Two-step generation:**

**Step A:** insight + violated heuristics + public context → `design_principle`.
Rubric: CONSTRAINING (excludes classes of solutions), TRACEABLE (derives from specific insight), OPERATIONAL (enables concrete decisions), NAMED (short memorable formula).

**Step B:** principle + current UI description → `design_decision`.
Rubric: SPECIFIC (names concrete elements), ACTIONABLE (buildable in under one day), COMPARABLE (has visible before/after), JUSTIFIED (references heuristics, not aesthetics).

**Required field:** every decision must include `resolves_heuristics` — naming specific canonical heuristics it addresses. Decisions without this field are rejected.

## 11. Layer 8 — Optimization loop (core novelty)

**Purpose:** iteratively improve redesigns using canonical design heuristics as the loss function.

This layer is the **core innovation of the project**. It transforms design from a single-pass generative act into a gradient-free optimization problem with explicit, measurable quality criteria.

**Loop structure:**

```
initialize:
    v_0 = decision from Layer 7
    score_0 = audit(v_0)
    log = [(v_0, score_0, "initial")]
    best = v_0

iterate:
    v_n = claude_generate(
        previous_design = best,
        weak_points = lowest-scoring heuristics from score(best),
        constraint = "address weak_points without regressing strong ones"
    )
    score_n = audit(v_n)                    # same 5 canon skills
    
    if dominates(score_n, score(best)):
        log.append((v_n, score_n, reasoning, delta_per_heuristic))
        best = v_n
        no_improvement_count = 0
    else:
        log.append((v_n, score_n, "dismissed", regression_reason))
        no_improvement_count += 1
    
    stop if:
        no_improvement_count >= 3      (convergence)
        iterations >= 8                (budget ceiling)
        score(best) >= 90 across all   (quality ceiling)
```

**Scoring function:**

Each of 5 canonical skills returns severity scores (0–10) across multiple heuristics. Aggregation uses **vector pareto dominance** as primary criterion:

> v_n dominates v_previous if: score(v_n) is greater or equal on every heuristic, AND greater on at least one.

If pareto dominance fails, fall back to **weighted sum** with meta-weights aligned to business weighting. Improvement under weighted sum is accepted only if no single heuristic regresses by more than 1 point.

**Append-only log (critical for traceability):**

Every iteration is logged with:
- Full design artifact (spec, wireframe or code)
- Per-heuristic scores across all canon skills
- Reasoning for the proposed change
- Delta per heuristic (what improved, what regressed)
- Acceptance decision and rationale
- Evidence links (which reviews informed this iteration)

Log is append-only, written to file after each iteration. Even dismissed iterations are recorded — the log is a complete **audit trail of the design evolution process**, not just a success record.

**Anti-gaming safeguards:**

Risk: Claude generating designs that *score well on audit* without being genuinely better.

Mitigations:
- Audits run in fresh contexts (skills don't see that this is iteration 5 — they see a design to audit)
- Each skill has its own rubric and operates independently
- Pareto dominance is strict (improvement on one heuristic cannot compensate for regression on another)
- Manual spot-check at convergence: does the final design visibly solve the original complaint?

## 12. Layer 9 — Final redesign

**Purpose:** produce deliverable artifact from the highest-scoring iteration in the log.

The optimization loop produces an ordered log of design iterations. Layer 9 selects the pareto-optimal version (or weighted-sum winner if pareto set has multiple candidates) and renders it as a three-layer output:

**Layer 9a — Specification:** structured JSON describing the component, its states, interactions, copy, and mapping to decisions.

**Layer 9b — Prototype:** HTML/React implementation. Claude uses Tailwind + shadcn component library (introduced during the hackathon) and a design system influenced by the author's existing work (achromatic palette, precise typography, minimum chrome). The prototype is generated once, from the selected best version, not during every iteration (iterations work at spec + wireframe level to reduce cost).

**Layer 9c — Rationale:** the evolution graph from Layer 10, associated with this specific final design.

**Graceful degradation:** if generative HTML proves unreliable, fall back to wireframe SVG. If that fails, use manual Figma screens with generated specification. The core innovation (traceability, optimization process) survives any of these fallbacks.

## 13. Layer 10 — Evolution graph

**Purpose:** demo-facing visualization of the entire journey from raw feedback to final design, rendered as a time-ordered trace rather than a static rationale tree.

**Why "evolution graph" rather than "rationale graph":**

Original conception was a static tree showing insight → decision → element. The optimization loop produces something richer: a **trajectory of decisions across iterations**, each justified by specific heuristic deltas. This is narratively stronger — the demo shows process, not just product.

**Structure — two views:**

**View A — Timeline view** (primary demo view):

```
v_0 (initial) ────────► score: N_0 ──► reviews informing this version
    │
    ▼ improve X heuristic
v_1 ──────────────────► score: N_1 ──► review quotes informing change
    │                      (accepted)
    ▼ improve Y heuristic  
v_2 ──────────────────► score: N_2 ──► ...
    │                      (dismissed: Z regressed)
    ▼ different approach
v_3 ──────────────────► score: N_3 ──► ...
    │                      (accepted — convergence start)
    ▼
v_4, v_5 ─────────────► score unchanged ──► ...
                           (converged)
```

Each row in the timeline is clickable: reveals full design artifact, per-heuristic scores, informing reviews, and reasoning for change.

**View B — Final rationale** (drill-down from timeline):

Traditional rationale graph for the *final* design — but now each node links back to the iteration where that design element was introduced. Complete provenance: from raw review through which iteration produced which element.

**Interactivity:**
- Click on iteration → full design artifact with scores breakdown
- Click on design element in final view → jump to iteration where it emerged
- Click on review quote → see all iterations that cited this review
- Click on heuristic score → see history of this heuristic across iterations

## 14. Baseline comparison

**Purpose:** defend T3 (traceability as differentiator) and T4 (optimization as differentiator) with measurable comparisons.

**Three baseline levels:**

- **B1 — Naive prompt:** entire corpus → Claude → "propose a paywall redesign"
- **B2 — Manual clustering + single-pass generation:** pre-grouped reviews → Claude → redesign (no optimization loop)
- **B3 — Full system** (this project)

**Four metrics:**

| Metric | B1 | B2 | B3 |
|--------|----|----|----|
| Traceability coverage (% decisions with specific review quotes) | 0–15% | 30–50% | 95–100% |
| Priority justification (% with explicit weights) | 0% | 0% | 100% |
| Iterations with audit trail (n) | 1 (static) | 1 (static) | 3–8 (logged) |
| Explainability score (user study, n=5, 1–10) | 4–5 | 6–7 | 8–9 |

**Honest framing:** the system does not guarantee prettier UI. It guarantees auditable, defensible UI. Positioning is pragmatic, not defensive. Subjective design quality may even favor naive baselines — this is stated openly, and the system's claimed value is precisely the dimensions naive baselines cannot provide.

**Demo section:** "Compare approaches" — three panels side by side showing the same insight producing three different outputs.

## 15. Failure modes and fallback strategy

Every layer has defined success signals, identified failure modes, fallbacks, and graceful degradation paths. Detailed per-layer analysis is maintained in working documents.

**Meta-operational strategy:**

- **Days 1–2:** build layer by layer, each tested on 20-review sample before proceeding
- **Day 3:** first end-to-end run, decision checkpoint
- **Day 4:** demo integration (UI, interactivity, baseline comparison, evolution graph)
- **Day 5:** polish, demo script, backup video, final submission artifacts
- **Sunday 02:00 Polish time:** submit

**Hard rule:** Saturday is fallback day, not fix day. A working 7-layer system beats a broken 10-layer system.

**Fallback priority (cuts under pressure, from least to most critical):**
1. Cooper + Garrett canonical skills (reduce to 4 backbone skills: Norman, Kahneman, Osterwalder, Accessibility)
2. Validation double-pass (validation confidence disappears)
3. Interactive graph (static SVG instead of D3 dynamic)
4. Generative HTML prototype (wireframe SVG + specification)
5. Baseline B1 and B2 (only B3 remains in demo)

**Non-negotiable (thesis foundations):**
- `structure-of-complaint`
- Business weighting with rubric
- Optimization loop with append-only log
- Evolution graph
- Accessibility as canonical perspective (the 6th skill is not optional)
- Honest framing

## 16. Defenses against external risks

**Reputational risk (Duolingo as real case):**
- Framing discipline: "proof of method," not "critique"
- Every insight anchored to a literal quote from a public review
- No claims about Duolingo designers' intent
- Traceability as protection: every statement verifiable through public sources

**Legal risk:**
- Reviews treated as public data (Google Play and App Store TOS permit analytical use)
- User IDs hashed, not by name
- Duolingo brand used in context of public knowledge, not commercial purpose
- Evolution graph always cites source (public verifiability)

**Over-engineering risk:**
- Pitch describes *one journey*: feedback → heuristics → evolution → redesign
- Ten layers under the hood, but reviewer sees a single narrative
- Complex mechanism, simple surface

## 17. Intellectual value of the project

**Differentiator vs other AI design tools:**
- v0, Galileo, Uizard: generation without justification
- This system: generation with traceability to user voice AND canonical design theory, with explicit optimization process through canonical heuristics as loss function

**Differentiator vs classical user research:**
- Classical: deep insight, but time-consuming and hard to audit
- This system: structural, reproducible, every decision auditable, full trail of design evolution

**Differentiator vs other hackathon submissions:**
- Most submissions will be Claude Code wrappers or single-skill applications
- This system demonstrates composable skills architecture *with* optimization loop (LLM-as-compiler applied to design), grounded in six canonical design perspectives including accessibility
- Positioned at the current moment in AI: gradient-free optimization, self-audit, canonical grounding

**Meta-layer for Anthropic:**
- The project addresses the explicit tension between AI-first strategies and user-centered design
- Shows that AI can amplify user voice in design, rather than replace it
- Demonstrates composable skills in action — the direction Anthropic is publicly pursuing with Claude Code Skills
- A constructive response to a question Anthropic's ecosystem is currently grappling with

**Brand position for the author:**
- *Auditable Design* establishes a methodological signature: design as a system of traceable, defensible decisions, not as aesthetic output
- The name extends naturally to keynote, consulting, methodology practice, and internal transformation programs
- Fits the author's existing body of work: SOT methodology, audit frameworks at enterprise, and the *Beyond the Loop* manifesto

## 18. Success criteria for the hackathon

**Must deliver:**
- Working pipeline on real Duolingo corpus (even with reduced scope)
- At least 4 of 6 canonical skills active (minimum: Norman, Kahneman, Osterwalder, Accessibility)
- Optimization loop with minimum 3 iterations showing convergence
- Evolution graph for at least one flagship decision (paywall redesign)
- Interactive demo (even with fallback to wireframes)
- Baseline comparison (even if only B3 vs B1)
- Complete README, short technical write-up, backup video

**Nice to have:**
- All 6 canonical skills fully active (Cooper and Garrett alongside the four backbones)
- Functional HTML/React prototype
- Full validation with validation confidence visible
- B1 + B2 baseline in side-by-side comparison
- Multiple optimization trajectories (for multiple decisions)

**Success signals for pitch:**
- Jury understands value proposition in 30 seconds
- One strong moment in demo: timeline view of optimization loop — "watch the design improve itself through audit"
- Reviewer question closed with quote from specific review

---

## 19. Pre-implementation checklist

Before coding begins, the following must exist in written form:

1. This document (concept — source of truth)
2. Target technical architecture (how ten layers physically compose into a running application)
3. Public context document about Duolingo (~500 words with footnotes)
4. Repository skeleton with folder structure matching the architecture

Items 2–4 are the immediate next deliverables before coding begins.

---

**End of concept document.**

*This document is the single source of truth for implementation. Any deviation during coding should be reflected back into this document. Version controlled in the submission repository.*
