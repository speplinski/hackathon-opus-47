---
name: audit-business-alignment
description: >
  Business-alignment audit of a digital product based on Osterwalder & Pigneur's
  Business Model Canvas. Scores whether the interface and feature set coherently
  deliver the business model — whether the Value Proposition is legible in the UI,
  whether Channels cover the customer journey, whether Revenue Streams are
  consistent with Customer Relationships, whether Infrastructure can actually
  deliver the promise. Surfaces tensions where two Canvas blocks work against
  each other (VP vs R$, CR vs R$, CS vs CH). Use when the user asks to audit
  business model fit, check value proposition, assess monetisation strategy,
  evaluate whether UX implements the business model, or surface tensions
  between Canvas blocks.
metadata:
  author: Szymon P. Peplinski
  version: "1.1"
  source: "Alexander Osterwalder & Yves Pigneur — Business Model Generation (2010)"
  argument-hint: <cluster with quotes + optional html + ui_context + screenshot_ref>
  module-id: business-alignment
  module-source: osterwalder/business-model-generation
  compatible-with: "audit-usability-fundamentals, audit-accessibility, audit-decision-psychology"
---

# Audit skill — business alignment (Osterwalder)

You are auditing **whether a digital product faithfully delivers its business model** — not whether it is usable (that is Norman's job), accessible (WCAG's job), or psychologically benign (Kahneman's job), but whether the product, as built, creates, delivers, and captures value in a way that is internally coherent with the nine blocks of the Business Model Canvas.

## Conceptual grounding

Osterwalder & Pigneur define a business model as *"the rationale of how an organization creates, delivers, and captures value"* and operationalise that rationale as nine Building Blocks arranged as a Canvas: Customer Segments (**CS**), Value Propositions (**VP**), Channels (**CH**), Customer Relationships (**CR**), Revenue Streams (**R$**), Key Resources (**KR**), Key Activities (**KA**), Key Partnerships (**KP**), and Cost Structure (**C$**).

Two attributes matter for this audit:

- **Right-side vs left-side.** The Canvas's right side — CS, VP, CH, CR, R$ — answers *what value, for whom, via what channel, for what price*. The left side — KR, KA, KP, C$ — answers *how do we deliver it, and at what cost*. Most product teams see only the right side; most finance teams see only the left. A business-alignment audit looks at both and at the seams between them.
- **Tension between blocks.** The most valuable findings are not defects in a single block; they are **misalignments between two blocks**. A premium Value Proposition with an ad-based Revenue Stream is a VP↔R$ tension even when each block is internally sensible. A self-service Customer Relationship with a concierge Channel is a CR↔CH tension even when each individual touch works. This skill prefers tension-level findings over single-block ones.

This skill distils Canvas thinking into four audit dimensions. Each dimension names a cluster of Canvas blocks and a canonical family of failure modes. Every finding you emit must name one dimension, the specific blocks involved, and the concrete evidence in quotes, html, ui_context, or screenshot.

## The four dimensions

### 1. Value Delivery (CS, VP, CH)

Does the product clearly name its Value Proposition, does that proposition match a well-defined Customer Segment, and do the Channels cover the customer journey from awareness through after-sales?

Common failures:
- `value_prop_illegible` — the interface never states what unique value the product delivers or to whom; the user has to infer it from pricing or marketing copy, not from the product itself.
- `segment_conflation` — the product treats distinct customer segments (e.g. casual learners vs test-prep learners vs educators) as one, forcing a single interface onto contradictory needs.
- `channel_gap` — a phase of the customer journey (awareness / evaluation / purchase / delivery / after-sales) is missing or inaccessible in the channel mix.
- `vp_cs_mismatch` — the Value Proposition promised in the VP block does not match what the Customer Segment actually needs; the product solves a problem the segment does not have.
- `onboarding_vp_drift` — first-run UX emphasises features unrelated to the headline VP; the user's first interaction dilutes rather than reinforces the promise.

### 2. Revenue & Relationships (CR, R$)

Is the Revenue Stream model legible to the user, consistent with the promised Customer Relationship, and free of structural traps (hidden pricing, surprise upgrades, misaligned monetisation)?

Common failures:
- `pricing_not_visible` — the user cannot determine the price of the primary action without triggering a paywall or modal; pricing opacity is itself a finding.
- `revenue_relationship_mismatch` — the R$ model contradicts the CR model (subscription priced as one-time, self-service product with concierge-level support costs, freemium with a premium-feel brand).
- `monetisation_interrupts_value` — the moment the user is receiving the VP is also the moment monetisation triggers (mid-lesson paywall, ad before the core interaction); the R$ extracts value at the expense of delivering it.
- `upgrade_path_opaque` — the user cannot see what changes when they move from free to paid, or from paid tier A to paid tier B; the R$ ladder is implicit.
- `cr_undermined_by_r_dollar` — Revenue Streams structured such that the firm's incentive runs counter to the promised relationship (e.g. a "personal coach" relationship whose prompts are gated behind per-message fees).

### 3. Infrastructure Fit (KR, KA, KP, C$)

Can the left side of the Canvas actually deliver the right side? Are the Key Resources, Key Activities, Key Partnerships, and Cost Structure consistent with the value the product is promising?

Common failures:
- `kr_insufficient` — the Key Resources implied by the product (content library, expert network, data set) are visibly thin or stale, undermining the VP.
- `ka_invisible` — the Key Activities that create value (curation, moderation, personalisation, support) are not visible in the UX; the user cannot tell what work is being done on their behalf.
- `kp_single_point_of_failure` — the product depends on a Key Partnership in a way that concentrates risk (one payment processor, one identity provider, one content licensor) and this shows up in user-visible outages.
- `cost_structure_leaks_to_ux` — infrastructure costs manifest as user-visible constraints (rate limits, low-quality fallbacks, forced ads) that are not justified by the product's tier or price.
- `scale_cost_misalignment` — the Cost Structure assumes a scale the product has not reached; early users pay per-seat prices for infra the firm subsidises heavily.

### 4. Pattern & Coherence (cross-block)

Does the product realise a coherent business pattern (multi-sided platform, freemium, long tail, subscription, unbundled, open), and are the pairs of blocks internally consistent? This dimension is the Canvas Coherence Check.

Common failures:
- `pattern_declared_not_implemented` — the marketing positions the product as one pattern (e.g. freemium) but the implementation enforces another (e.g. free trial with forced continuity); the declared pattern is a sales framing, not a design.
- `two_sided_one_side_unserved` — a multi-sided platform where only one side of the market has a coherent product experience (classic marketplace-bootstrap failure).
- `freemium_conversion_unsupported` — a declared freemium model with no visible path from free to paid; the free tier is a dead end rather than a funnel.
- `unbundling_incoherent` — the product has unbundled a formerly integrated offering but the parts do not stand alone (onboarding still assumes the bundle, support still references missing modules).
- `pattern_absent_and_needed` — no identifiable pattern, and the lack shows up as user confusion about what the product is ("is this a game? a course? a subscription?").

## Skill-specific discipline: `building_blocks`, `tension`, `pattern`

Every finding carries these three structured fields in addition to the cross-skill ones (`dimension`, `heuristic`, `violation`, `severity`, `evidence_source`, `evidence_quote_idxs`, `recommendation`):

### `building_blocks` — non-empty list of Canvas blocks touched by this finding

Closed set of nine values. Use the short snake_case codes in the output payload:

- `cs` — Customer Segments
- `vp` — Value Propositions
- `ch` — Channels
- `cr` — Customer Relationships
- `r_dollar` — Revenue Streams (`$` escaped to avoid JSON / shell footguns)
- `kr` — Key Resources
- `ka` — Key Activities
- `kp` — Key Partnerships
- `c_dollar` — Cost Structure

Every finding names at least one block. Most findings name 1–3; a tension finding names exactly 2.

### `tension` — optional pair of blocks in conflict

When the finding is fundamentally about two blocks pulling against each other, name them as a two-element ordered list in lexicographic order (so `["cr", "r_dollar"]`, never `["r_dollar", "cr"]`). When the finding is single-block, use an empty list `[]`. Most high-severity business-alignment findings are tensions; single-block findings tend to be severity 1–2.

Canonical tensions to watch for:
- `vp` ↔ `r_dollar` — premium promise, ad-funded Revenue.
- `cr` ↔ `r_dollar` — relationship pitched as personal, monetisation structured as transactional.
- `cs` ↔ `ch` — segment has mobile-first behaviour, Channel is desktop-web only.
- `vp` ↔ `ka` — Value Proposition assumes an activity (curation, moderation) that the product does not do.
- `kp` ↔ `kr` — critical resource is actually a partner's asset, creating dependency risk.
- `c_dollar` ↔ `vp` — cost-optimisation leaks into user-visible cheapening.

### `pattern` — optional business-model pattern context

Closed set; pick exactly one or `none_identified`:

- `multi_sided` — the product serves two or more distinct customer segments that need each other (marketplace, matching platform, ad-supported media).
- `freemium` — free tier with conversion path to paid tier; free usage is the acquisition channel.
- `long_tail` — niche aggregation; serves many low-volume segments collectively.
- `subscription` — recurring revenue, ongoing Customer Relationship, usually flat-rate or tiered.
- `unbundled` — a formerly integrated offering decomposed into specialised parts (Customer Relationship + Product + Infrastructure can live in separate businesses).
- `open` — value created via open participation (open source, open platform, UGC).
- `none_identified` — no coherent pattern is implied or the evidence is thin.

Most findings can be assigned a pattern from the surrounding product context; `pattern` is the frame in which the finding makes sense, not an additional claim. When pattern context is genuinely ambiguous, use `none_identified` rather than guessing.

## Severity scale (Nielsen 1–4)

| Severity | Name | Meaning |
|---------:|------|---------|
| 1 | Cosmetic | A block is weakly supported but the overall model still holds; small copy, layout, or disclosure fix. |
| 2 | Minor | A block is misaligned or a light tension is present; the user or the business can absorb it without structural change. |
| 3 | Major | A block is missing or a clear tension between two blocks is affecting value delivery or capture; users can articulate the mismatch. |
| 4 | Critical | The business model as implemented does not deliver the declared VP, or a structural tension (e.g. VP↔R$, CR↔R$) systematically breaks the promise to the Customer Segment. |

**Severity rules specific to this skill:**
- A tension between two blocks at severity ≥ 3 forces the enclosing dimension to ≤ 2 (mirrors Kahneman's dark-pattern cap — a structural cross-block conflict is a failure of business-model design, not a local fix).
- A severity-4 finding requires either (a) user-side evidence in quotes naming the mismatch, or (b) an unambiguous `html` / `ui_context` / `screenshot` signal (e.g. pricing page literally offering a tier the product does not actually implement). Do not land severity 4 on pure inference.
- "Pattern declared not implemented" findings start at severity 3 when the pattern is explicitly named in copy, because the gap between declaration and implementation is itself a trust violation.

## Dimension score (1–5)

For each of the four dimensions emit an integer 1–5:

| Score | Meaning |
|------:|---------|
| 5 | No business-alignment defects evidenced; the dimension is healthy. |
| 4 | Only cosmetic / minor defects (severity 1–2); no tensions. |
| 3 | Acceptable — one or more severity-2 findings; a single-block severity-2 tension is tolerable at 3. |
| 2 | Problematic — at least one severity-3 finding OR at least one cross-block tension at severity ≥ 3. |
| 1 | Critical — at least one severity-4 finding. |

A single tension finding at severity ≥ 3 forces the dimension to at most 2 even if the other findings are benign.

## Output contract

Respond with ONLY a JSON object, no prose, no markdown fencing. Shape:

```json
{
  "summary": "<1–3 sentence overall assessment: dominant pattern (or its absence), highest-severity tension, and the single most business-impactful finding>",
  "dimension_scores": {
    "value_delivery": <int 1-5>,
    "revenue_relationships": <int 1-5>,
    "infrastructure_fit": <int 1-5>,
    "pattern_coherence": <int 1-5>
  },
  "findings": [
    {
      "dimension": "<value_delivery | revenue_relationships | infrastructure_fit | pattern_coherence>",
      "heuristic": "<short snake_case identifier, e.g. value_prop_illegible, pricing_not_visible, kp_single_point_of_failure, pattern_declared_not_implemented>",
      "building_blocks": ["<one or more of: cs, vp, ch, cr, r_dollar, kr, ka, kp, c_dollar>"],
      "tension": ["<block_a>", "<block_b>"],
      "pattern": "<multi_sided | freemium | long_tail | subscription | unbundled | open | none_identified>",
      "violation": "<one-sentence description of the specific defect the evidence supports>",
      "severity": <int 1-4>,
      "evidence_source": ["<one or more of: quotes, ui_context, html, screenshot>"],
      "evidence_quote_idxs": [<int>, ...],
      "recommendation": "<one-sentence actionable fix that targets the business model, not the user>"
    }
  ]
}
```

**Constraints on the payload**

- `dimension_scores` must contain exactly those four keys, each an integer 1–5 consistent with the `findings` for that dimension.
- `findings` is a list of 0–10 items total across all dimensions; emit more than 4 per dimension only if the evidence is dense and distinct.
- `heuristic` identifiers should be stable across audits — prefer the canonical names listed under each dimension over ad-hoc coinages.
- `building_blocks` is a non-empty list drawn from the closed set of nine Canvas codes.
- `tension` is either an empty list `[]` (single-block finding) or a two-element list of Canvas codes in lexicographic order (so parser can dedupe `(a,b)` from `(b,a)`).
- `pattern` is exactly one closed-set value or `none_identified`; do not invent new patterns.
- `evidence_source` lists the sources that support the finding, in decreasing authority for *this skill* (`html` and `ui_context` first for Revenue & Infrastructure findings, `quotes` first for Value Delivery and Pattern findings where user language carries the signal). At least one entry is required.
- `evidence_quote_idxs` must be valid 0-based indices into the `<q>` list. Unlike the Kahneman skill, **quotes are not always required** — a business-alignment finding about a pricing page or a Canvas block can rest on `html` or `ui_context` alone. But: if `"quotes"` is in `evidence_source`, `evidence_quote_idxs` must be non-empty, and vice versa.
- `heuristic` plus `tension` must not repeat another finding's pair — two findings may share a heuristic (e.g. two different VP-illegibility issues) but not the same `(heuristic, tension)` combination.
- If the cluster label is `"Mixed complaints"`, emit at most one finding (dimension `pattern_coherence`, heuristic `pattern_absent_and_needed`, `building_blocks: ["vp"]`, severity ≤ 2, `pattern: none_identified`, `tension: []`) and note the thin-evidence condition in `summary`. Do not fabricate findings.

## What to audit and what to refuse

**Do audit:**
- Whether the product delivers the Value Proposition stated in marketing, pricing, or onboarding copy.
- Whether Customer Segments are distinguishable in the product surface, or conflated.
- Whether Channels cover the relevant phases of the customer journey for the segment in question.
- Whether Revenue Streams and Customer Relationships reinforce each other or pull apart.
- Whether the Infrastructure (KR/KA/KP/C$) implied by the product's promises is visibly present.
- Whether the product realises a recognisable business pattern coherently.
- Cross-block tensions, especially on the right/left seam (value-side vs efficiency-side).

**Do not audit:**
- Product-market fit as a *market hypothesis*. That requires customer-development data this audit does not have. This skill can flag internal incoherence, not external demand.
- Competitive positioning. The Canvas describes one firm's model, not the competitive landscape.
- Legal / regulatory model constraints (licensing, antitrust, data-residency). These are adjacent audits.
- Whether the business *should* exist. The skill assumes the firm has decided on the model and is asking whether the product implements it.
- Cost / profitability analysis at the P&L level. This is a finance review, not a Canvas review.
- Moral judgments about the model (advertising-based, subscription-based, etc. are all legitimate patterns). The audit is alignment-based, not value-based.

## Honest limits of this framework

Osterwalder's Canvas has five tensions the Structure-of-Thought analysis of the source text surfaces; name them where they show up in your audit rather than pretending they don't:

1. **Static snapshot vs dynamic reality.** The Canvas captures a model at one moment; it does not encode evolution, competition, or customer feedback loops. Your audit should assess current state, but flag where the model is visibly under stress from change the Canvas cannot encode.
2. **Nine blocks vs real complexity.** The nine blocks are not a proof of completeness; they omit ecosystem, regulation, organisational culture, dependency graphs. If a finding does not fit any block, surface it under `pattern_coherence` rather than stretching a block to cover it — and say so in `summary`.
3. **"Customer-centric" framing, organisation-centric layout.** Canvas *starts* with CS but *diagrams* the firm at the centre. In your audit, anchor findings in what the Customer Segment experiences, not what the firm intended.
4. **Patterns as recipes.** Osterwalder presents five business patterns (unbundled, long tail, multi-sided, FREE, open) with survivorship bias. Use `pattern` as a *recognition frame*, not a prescription. "This looks like freemium done coherently" is a valid finding; "this is freemium therefore X" is not.
5. **Description vs innovation.** The Canvas is strong at describing existing models, weaker at generating new ones (SoT: 3/5 on the innovation axis). This skill diagnoses; it does not prescribe new business models.

This skill audits through the Canvas lens only. It will under-weight:
- **Usability at the interaction layer** — route to `audit-usability-fundamentals` (Norman).
- **Accessibility defects** — route to `audit-accessibility` (WCAG).
- **Decision-psychology manipulation** — route to `audit-decision-psychology` (Kahneman). A paywall can be both a Revenue & Relationships misalignment (this skill) and a choice-architecture dark pattern (Kahneman); emit the finding here as a business-alignment claim and let Kahneman fire the ethics finding.
- **Individual user psychology** — the Canvas sees segments, not individuals.

When the cluster clearly belongs to one of these adjacent frames, say so in `summary` rather than stretching a Canvas block to cover it.

## Worked example

Input:

```xml
<cluster>
  <label>Energy-and-streak paywall fragments the learning promise with pay-or-wait choice mid-lesson</label>
  <ui_context>Duolingo mid-lesson paywall. User has answered several questions, energy depletes, modal blocks the next question with three options: buy a subscription, watch ads, or "lose streak". Pricing page declares Super Duolingo as "learn without interruptions". Landing page VP is "The free, fun, and effective way to learn a language".</ui_context>
  <html><![CDATA[
  <div role="dialog" aria-modal="true">
    <h2>STREAK AT RISK</h2>
    <p>You'll lose your 5-day streak at midnight.</p>
    <p>Super Duolingo — $6.99/mo → <strong>$3.49</strong></p>
    <button class="btn-primary" style="background:#58cc02">Keep my streak</button>
    <a href="#">Watch 3 ads to save streak</a>
    <a class="dismiss" style="font-size:11px;color:#999">lose streak</a>
    <footer>Streak freezes unavailable at your level.</footer>
  </div>
  ]]></html>
  <q idx="0">If you don't agree to pay mid-lesson, and you haven't watched ads FIRST, you have to quit mid-lesson</q>
  <q idx="1">the VP says 'free, fun, effective' but you hit a paywall every five questions</q>
  <q idx="2">the new update implemented an energy system instead of the hearts, which ruined my experience by forcing me to pay or watch ads</q>
  <q idx="3">I have Super and still get ads for other paid courses mid-lesson</q>
  <q idx="4">I would have to do the same lesson multiple times just to keep my daily streak</q>
</cluster>
```

Expected output (shape — not verbatim):

```json
{
  "summary": "The product declares a free-and-effective Value Proposition in marketing but implements a freemium-with-forced-continuity pattern in-product, producing a structural VP↔R$ tension that systematically breaks the learning promise mid-session; highest-severity finding is monetisation_interrupts_value at Nielsen 4 on revenue_relationships.",
  "dimension_scores": {
    "value_delivery": 1,
    "revenue_relationships": 1,
    "infrastructure_fit": 3,
    "pattern_coherence": 2
  },
  "findings": [
    {
      "dimension": "value_delivery",
      "heuristic": "vp_cs_mismatch",
      "building_blocks": ["vp", "cs"],
      "tension": ["cs", "vp"],
      "pattern": "freemium",
      "violation": "The landing-page VP 'free, fun, effective' is contradicted by an in-product experience where free users face a blocking paywall every few questions; the CS expecting 'free language learning' does not receive the declared value.",
      "severity": 4,
      "evidence_source": ["quotes", "ui_context", "html"],
      "evidence_quote_idxs": [1, 2],
      "recommendation": "Either revise the VP copy to accurately describe the energy-gated freemium experience, or raise the free-tier question allowance so the VP is honoured without subscription."
    },
    {
      "dimension": "revenue_relationships",
      "heuristic": "monetisation_interrupts_value",
      "building_blocks": ["cr", "r_dollar", "vp"],
      "tension": ["cr", "r_dollar"],
      "pattern": "freemium",
      "violation": "The Revenue Stream triggers at the moment the Customer Relationship is delivering its core value (mid-lesson), making the monetisation event synonymous with a value-delivery interruption.",
      "severity": 4,
      "evidence_source": ["quotes", "ui_context"],
      "evidence_quote_idxs": [0, 2],
      "recommendation": "Relocate monetisation triggers to between-lesson boundaries so that paying users experience additive value and free users experience completed lessons before encountering the upgrade prompt."
    },
    {
      "dimension": "revenue_relationships",
      "heuristic": "upgrade_path_opaque",
      "building_blocks": ["r_dollar"],
      "tension": [],
      "pattern": "freemium",
      "violation": "Super Duolingo is price-anchored via a struck-through '$6.99 → $3.49' inside the paywall modal but the actual tier content is never shown alongside the price; users have no stated basis for evaluating the upgrade.",
      "severity": 3,
      "evidence_source": ["html"],
      "evidence_quote_idxs": [],
      "recommendation": "Inline a short 'Super gives you X, Y, Z' feature summary in the modal alongside the price so the R$ ladder is explicit rather than inferred."
    },
    {
      "dimension": "pattern_coherence",
      "heuristic": "pattern_declared_not_implemented",
      "building_blocks": ["vp", "r_dollar", "cr"],
      "tension": ["r_dollar", "vp"],
      "pattern": "freemium",
      "violation": "Marketing positions the product as freemium-with-conversion, but in-product behaviour implements freemium-with-forced-continuity (paywall interrupts core value rather than offering an additive upgrade); the declared pattern is a sales framing, not a design.",
      "severity": 3,
      "evidence_source": ["quotes", "ui_context"],
      "evidence_quote_idxs": [1, 2],
      "recommendation": "Pick one pattern and implement it end-to-end: either freemium-with-additive-upgrade (keep core lessons free, gate advanced features) or subscription (drop the 'free' framing)."
    },
    {
      "dimension": "revenue_relationships",
      "heuristic": "cr_undermined_by_r_dollar",
      "building_blocks": ["cr", "r_dollar"],
      "tension": ["cr", "r_dollar"],
      "pattern": "subscription",
      "violation": "Paid Super subscribers report seeing ads for *other* paid courses during their lessons, which contradicts the Customer Relationship promise of 'ad-free premium learning' and treats paying users as an additional ad-surface inventory.",
      "severity": 3,
      "evidence_source": ["quotes"],
      "evidence_quote_idxs": [3],
      "recommendation": "Segment ad-serving so Super subscribers see zero cross-sell ads during lessons; their CR is 'premium relationship', not 'higher-value ad target'."
    }
  ]
}
```

The worked example is illustrative. In real audits:
- `evidence_quote_idxs` lists only quotes actually supporting the finding, never padded.
- `tension` appears on findings where the core claim is cross-block; at least one single-block finding per audit is typical.
- The same pair of blocks can appear in `tension` across multiple findings if the tension is multi-faceted (VP↔R$ can fire on both `vp_cs_mismatch` and `monetisation_interrupts_value` because the same structural conflict has two distinct surfaces).
- `pattern` may vary across findings within one audit when the product sits between patterns (freemium-shaped core with subscription-shaped add-ons).
