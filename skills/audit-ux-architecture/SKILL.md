---
name: audit-ux-architecture
description: >
  UX-architecture audit of a digital product based on Jesse James Garrett —
  The Elements of User Experience: User-Centered Design for the Web and
  Beyond (2nd ed., 2011). Scores whether the product's five planes —
  strategy (user needs + product objectives), scope (functional specs +
  content requirements), structure (interaction design + information
  architecture), skeleton (interface + navigation + information design),
  and surface (visual, sensory) — are individually coherent, cascade
  downward without losing their justification, and whether each design
  decision along the way was made consciously rather than by default,
  mimicry, or fiat. Use when the user asks to audit UX architecture,
  evaluate coherence between planes, check whether strategy maps to
  scope, scope to structure, structure to skeleton, skeleton to surface,
  or diagnose "design by default" symptoms — features that exist because
  the team copied them, never decided, or imposed them top-down without
  a user-centered reason.
metadata:
  author: Szymon P. Peplinski
  version: "1.0"
  source: "Jesse James Garrett — The Elements of User Experience, 2nd ed. (2011)"
  argument-hint: <cluster with quotes + optional html + ui_context + screenshot_ref>
  module-id: ux-architecture
  module-source: garrett/elements-2e
  compatible-with: "audit-usability-fundamentals, audit-accessibility, audit-decision-psychology, audit-business-alignment, audit-interaction-design"
---

# Audit skill — UX architecture (Garrett)

You are auditing **whether a product's UX is an architecture or a pile** — not whether its controls are discoverable (Norman), accessible (WCAG), psychologically benign (Kahneman), behaviourally considerate (Cooper), or business-aligned (Osterwalder), but whether the product's five Garrett planes — from strategy down to surface — each carry their own decisions, pass those decisions down coherently, and leave no plane in a state of "we never decided, it just happened this way."

## Conceptual grounding

Garrett frames UX as a stack of five planes, from most abstract to most concrete: **strategy** (user needs + product objectives), **scope** (functional specs + content requirements), **structure** (interaction design + information architecture), **skeleton** (interface + navigation + information design), **surface** (visual, sensory). Each plane is shaped and constrained by the plane below it; a decision on surface cannot legitimately override a decision on strategy, and a skeleton that drifts away from structure is a skeleton that has lost its reason to exist.

Three commitments thread the book and structure this audit:

- **Every element of the user experience should result from a conscious, user-centered decision.** Garrett's central moral claim. The opposite — design by default (the team never decided), mimicry (copied from a competitor without asking whether it fits), or fiat (a stakeholder imposed it top-down) — are the three ways UX decisions go bad. Audit findings should name which mode a defect came from when the evidence supports it.
- **Planes cascade downward: strategy shapes scope, scope shapes structure, structure shapes skeleton, skeleton shapes surface.** Defects at a higher plane (surface) that cannot be justified by a lower plane (structure or skeleton) are *floating* — visually present, architecturally orphaned. Defects at a lower plane (strategy) propagate upward and corrupt everything above them — an unclear strategy guarantees that scope will be unprincipled.
- **Every plane has a functional aspect and an informational aspect.** Garrett's fundamental duality: a product is simultaneously a *tool* (the user is trying to do something) and a *medium* (the user is trying to find, absorb, or navigate information). Scope splits into functional specs vs content requirements; structure into interaction design vs information architecture; skeleton into interface design, navigation design, and information design. The strategy and surface planes do not split — strategy is unitary (the product has one set of objectives), surface is unitary (the user sees one rendered page). Findings that ignore this duality collapse two different defects into one.

The Elements carries tensions the audit must honour rather than paper over: *planes are hierarchical* but *work on them overlaps*; *content is king* but Garrett admits some products are dominated by tools; *the model is universal beyond the web* but the vocabulary is web-native; *user-centered design* but every plane has legitimate business constraints too. Use the framework as diagnostic scaffolding and name its internal tensions where a finding sits on the seam.

## The five dimensions

### 1. Strategy coherence (CH3 — the strategy plane)

Does the product have a clear, articulated strategy — user needs plus product objectives — that can resolve scope-level disputes? Or does every plane above operate on guessed, inherited, or contradictory assumptions about why the product exists?

Common failures:
- `user_needs_unarticulated` — the product's design choices reveal no evidence that the team ever named a primary user, segment, or job-to-be-done; every surface reads as "for everyone", which in practice means "for us."
- `product_objectives_unstated` — there is no visible business or mission reason why the product should exist in this form; successive features land as decoration on top of nothing.
- `strategy_contradicts_itself` — user needs and product objectives are both articulated but mutually incompatible (the product promises a fast, distraction-free flow yet the objectives require growth-loop surfaces that necessarily interrupt the flow).
- `segment_mismatch` — the declared target user and the actual UX affordances address different people (a beginner-oriented landing page leading to an expert-only workflow, or vice versa).
- `strategy_by_competitor_mimicry` — the strategy plane is empty because the team copied a competitor's product wholesale, inheriting a strategy that was shaped for a different user, market, or era.

### 2. Scope coverage (CH4 — the scope plane)

Given the strategy, does the product's scope — the functional specifications and content requirements — include what it must and exclude what it should? Is the scope visibly bounded, or does it read as an unstoppable accretion of features and copy?

Common failures:
- `featuritis` — the product keeps accumulating features that no articulated user need justifies; every release grows the surface without removing anything.
- `content_unspecified` — content requirements were never documented: voice, length, tone, asset types, and freshness expectations drift per page, so the reading experience is incoherent.
- `functional_and_content_confused` — functional specs and content requirements are bundled into a single backlog, so a content decision is deferred to "whoever writes the template" and a functional decision is dressed up as "just copy."
- `priority_absent` — the scope has no visible priority; everything is labelled "must have"; when constraints bite, cuts happen by arbitrary triage rather than principled de-scoping.
- `scope_creep_mid_build` — the scope plane shows the geological layers of multiple mid-build re-scopings; feature A was built under one spec, feature B under a different spec, and neither was revisited for consistency.
- `scope_tracks_competitor_checklist` — the scope is a competitor's feature list reskinned, not a derivation from the audited product's own strategy.

### 3. Structure navigation (CH5 — the structure plane)

Does the product have a coherent structure — interaction design plus information architecture — such that a user moves through it with an accurate mental model and can predict where things live before they arrive?

Common failures:
- `information_architecture_implicit` — there is no articulated IA (no sitemap, no taxonomy, no content model); users cannot form a mental model because there is nothing systematic to learn.
- `interaction_model_inconsistent` — the same action is modelled differently in different parts of the product (clicking a result in search navigates, but clicking the same-looking result in a saved list opens a modal); the user cannot generalise.
- `navigation_structure_mirror_of_org_chart` — the structure reflects the organisation's internal departments rather than the user's tasks; users have to translate "my goal" into "which department owns this feature."
- `orphaned_content` — content exists in the product with no path in from the primary structure; users find it only via search or external links.
- `cross_linking_absent` — the information architecture is a strict hierarchy with no lateral links; users who arrive at the wrong leaf must climb back to the root and descend again.
- `too_many_root_nodes` — the top-level navigation has so many peers that the choice is paralysing; the structure front-loads a decision the user has no basis to make.
- `functional_and_informational_structure_fused` — the product's task flows (functional) and its content reading paths (informational) share a single structure, so each compromises the other.

### 4. Skeleton wireframe (CH6 — the skeleton plane)

Given the structure, is the skeleton — interface design + navigation design + information design — a principled arrangement of components on the page, or a layout that happens to exist?

Common failures:
- `interface_components_default_platform` — UI components are used straight out of the framework or component library without any adaptation to the product's specific structure; the skeleton is a design-system catalogue, not a designed thing.
- `navigation_design_disconnected_from_structure` — the visible navigation (nav bars, tabs, menus) does not mirror the structure's IA; users see one hierarchy and experience another.
- `information_design_absent` — data is presented in raw, unprioritised form (big tables, long lists, fact sheets) with no indication of what is primary, secondary, or incidental.
- `call_to_action_buried` — the primary action the structure plane identified is visually secondary on the skeleton (small, low-contrast, below the fold, or behind a second click).
- `competing_calls_to_action` — multiple elements compete for the primary-action role; the user must choose which to trust, and the skeleton does not help.
- `skeleton_does_not_honour_priority` — the priority established at the scope plane (what matters most) is not reflected in the skeleton's arrangement (what draws the eye first).
- `skeleton_unlabelled_regions` — regions of the page have no headings, grouping, or chrome to tell the user what kind of content they are looking at.

### 5. Surface sensory (CH7 — the surface plane)

Does the surface — the visual, sensory rendering — reinforce everything below it, or does it fight its own skeleton, structure, and strategy?

Common failures:
- `surface_contradicts_skeleton_priority` — the skeleton says element A is primary; the surface (colour, size, weight, motion) draws attention to element B. The user's eye and the designed hierarchy are in tension.
- `visual_language_inherited_from_brand_without_product_fit` — the surface is a direct lift of brand guidelines designed for marketing, applied to a product UI they were not shaped for (corporate-report typography in a dense data tool).
- `surface_trend_mimicry` — the visual style is copied from whatever is trending (neobrutalism, glassmorphism, dense dashboards) without asking whether it fits the product's strategy or the user's context.
- `visual_density_vs_content_mismatch` — the visual density (whitespace, element size) is wrong for the content density (spacious visual on a content-rich tool leaves the user scrolling endlessly; dense visual on a sparse content tool looks cramped and rushed).
- `typography_hierarchy_flat` — the type system does not carry the information hierarchy; h1 and body look similar enough that the reader has no visual guide.
- `colour_system_unprincipled` — colours are assigned per-screen rather than per-role; users cannot learn that "blue means interactive" because blue is used for six different things.
- `motion_decorative_not_functional` — animation is used to add interest rather than to communicate state change, continuity, or spatial relationship; motion is excise at the surface.
- `surface_performance_collapse` — the surface ignores performance constraints (enormous images, heavy webfonts, many custom effects) so the rendered experience regularly arrives late or broken.

## Skill-specific discipline: `product_type`, `decision_mode`

Every finding carries these two structured fields in addition to the cross-skill ones (`dimension`, `heuristic`, `violation`, `severity`, `evidence_source`, `evidence_quote_idxs`, `recommendation`):

### `product_type` — the Garrett duality the finding concerns

Closed set of four values:

- `functional` — the finding is about the product-as-tool: a task, workflow, interaction pattern, or functional affordance.
- `informational` — the finding is about the product-as-medium: a content model, reading path, information hierarchy, or editorial voice.
- `hybrid` — the finding is genuinely cross-cutting; the defect exists because the functional and informational aspects of the same surface were not separated (a task form drowning in explanatory prose, a content page whose "read more" accidentally creates a task).
- `not_applicable` — the finding is on the strategy or surface plane and does not meaningfully split across the duality (a strategy that is unarticulated has no functional/informational split to make; a surface colour system concerns both aspects equally by definition).

Use `hybrid` only when the finding is *about* the functional/informational seam, not merely spans it. A search input whose query and results are confusing is `functional`. A reading page that accidentally exposes a workflow (save, share, export, annotate) more prominently than its reading affordances is `hybrid` — the defect is the confusion of modes.

### `decision_mode` — how this design decision appears to have been made

Closed set of five values:

- `conscious` — the evidence suggests a deliberate, user-centered decision that happens to be wrong, partial, or outdated; the defect is in the decision, not in the absence of one.
- `default` — the decision was never made; the current state is the platform default, the component library default, or the framework's out-of-the-box behaviour. Garrett's "design by default."
- `mimicry` — the decision was imported wholesale from another product without adaptation; the surface, flow, or structure reads as "copied from X" with no evidence it was audited for fit.
- `fiat` — the decision was imposed top-down without a user-centered justification; a stakeholder said "put this here", "use our brand blue", or "don't touch this." The finding is that the product overrode its own plane logic to honour a command.
- `not_applicable` — the finding's shape does not support a decision-mode diagnosis; it is a generic defect with no visible authorship trail.

Claiming `default`, `mimicry`, or `fiat` requires evidence in the violation text — a pattern that is specifically the platform default (Bootstrap navbar, iOS share sheet unchanged), specifically a copy of a competitor (Duolingo-style streak modal on a non-gamified product), or specifically a brand-override (marketing-coloured CTA that fights the product's functional hierarchy). Ungrounded authorship claims are the parser's most common hallucination; do not emit them.

## Severity scale (Nielsen 1–4)

| Severity | Name | Meaning |
|---------:|------|---------|
| 1 | Cosmetic | An architectural seam is visible but the user's path remains intact; small inconsistency between planes that does not block the goal. |
| 2 | Minor | An imperfect cascade — a skeleton that loosely mirrors structure, a scope that is unevenly prioritised — that the user can work around; no visible blockage. |
| 3 | Major | A plane is incoherent or does not match the plane below (strategy vs scope, structure vs skeleton); users notice the mismatch as "this product doesn't know what it is" or "the layout fights me"; articulable. |
| 4 | Critical | A plane is effectively absent or flagrantly contradicts its neighbours: strategy is empty and scope is featuritis, structure is implicit and skeleton is improvised, surface overrides every signal below it. The product's UX is a pile, not an architecture. |

**Severity rules specific to this skill:**
- A finding with `decision_mode` ∈ {`default`, `mimicry`, `fiat`} at severity ≥ 3 forces the enclosing dimension to at most 2. Garrett's central claim is that unconscious decisions are structural defects; treating them as local fixes is miscalibrated.
- A strategy-plane finding at severity ≥ 3 propagates: if the evidence supports it, emit *also* a finding on the next plane up (scope) showing the consequence. The skill does not require this, but a strategy-only critique with no downstream consequence is usually under-specified.
- A finding that *only* concerns surface or skeleton but names `decision_mode: conscious` with no architectural consequence is typically not an architecture finding — it may be Norman, Cooper, or a visual-design critique. If the defect does not trace back through at least one plane below, consider whether it belongs in this audit at all.

## Dimension score (1–5)

For each of the five dimensions emit an integer 1–5:

| Score | Meaning |
|------:|---------|
| 5 | No architecture defects evidenced; the plane is coherent and cascades cleanly. |
| 4 | Only cosmetic / minor defects (severity 1–2); planes align, decisions are mostly conscious. |
| 3 | Acceptable — one or more severity-2 findings; a single plane seam at severity 3 with a local recommendation. |
| 2 | Problematic — at least one severity-3 finding OR one unconscious-decision-mode defect (`default`/`mimicry`/`fiat`) at severity ≥ 3. |
| 1 | Critical — at least one severity-4 finding; the plane is effectively absent or contradicts its neighbours. |

A single `decision_mode` ∈ {`default`, `mimicry`, `fiat`} finding at severity ≥ 3 forces the dimension to at most 2 even if the other findings are benign.

## Output contract

Respond with ONLY a JSON object, no prose, no markdown fencing. Shape:

```json
{
  "summary": "<1–3 sentence overall assessment: which plane is most coherent, which is most broken, and whether the cascade is intact>",
  "dimension_scores": {
    "strategy_coherence": <int 1-5>,
    "scope_coverage": <int 1-5>,
    "structure_navigation": <int 1-5>,
    "skeleton_wireframe": <int 1-5>,
    "surface_sensory": <int 1-5>
  },
  "findings": [
    {
      "dimension": "<strategy_coherence | scope_coverage | structure_navigation | skeleton_wireframe | surface_sensory>",
      "heuristic": "<short snake_case identifier, e.g. featuritis, information_architecture_implicit, skeleton_does_not_honour_priority, surface_contradicts_skeleton_priority>",
      "product_type": "<functional | informational | hybrid | not_applicable>",
      "decision_mode": "<conscious | default | mimicry | fiat | not_applicable>",
      "violation": "<one-sentence description of the specific architectural defect the evidence supports>",
      "severity": <int 1-4>,
      "evidence_source": ["<one or more of: quotes, ui_context, html, screenshot>"],
      "evidence_quote_idxs": [<int>, ...],
      "recommendation": "<one-sentence actionable fix that targets the plane where the defect lives, not only the surface symptom>"
    }
  ]
}
```

**Constraints on the payload**

- `dimension_scores` must contain exactly those five keys, each an integer 1–5 consistent with the `findings` for that dimension.
- `findings` is a list of 0–10 items total across all dimensions; emit more than 3 per dimension only if the evidence is dense and distinct.
- `heuristic` identifiers should be stable across audits — prefer the canonical names listed under each dimension over ad-hoc coinages.
- `product_type` and `decision_mode` are each drawn from their closed sets; do not invent values.
- `decision_mode` values of `default`, `mimicry`, or `fiat` must be grounded in the violation text with a concrete indicator — the specific library default, the specific product being copied, or the specific stakeholder command. Do not speculate about internal decision processes you cannot observe.
- `evidence_source` lists the sources that support the finding, in decreasing authority for *this skill* (`ui_context` and `html` first — architecture findings live in the product's structure; `quotes` are a strong secondary anchor because users feel architectural breakage as confusion; `screenshot` anchors surface-plane and skeleton-plane findings).
- `evidence_quote_idxs` must be valid 0-based indices into the `<q>` list. Quotes are not strictly required — a structure or scope finding can rest on `html` or `ui_context` alone.
- **Evidence-source ↔ quote-idx bidirectional rule** (parser-enforced, zero-tolerance — audit falls back on any asymmetry):
  - If `"quotes"` appears in `evidence_source` → `evidence_quote_idxs` MUST be non-empty.
  - If `evidence_quote_idxs` is non-empty → `"quotes"` MUST appear in `evidence_source`.
  - Practically: the moment you cite quote `[i]` anywhere, `"quotes"` must be in `evidence_source` and `i` in `evidence_quote_idxs`. No implicit citations.
- `heuristic` plus `product_type` must not repeat another finding's pair — two findings may share a heuristic (e.g. two different featuritis observations, one functional and one informational) but not the same `(heuristic, product_type)` combination.
- If the cluster label is `"Mixed complaints"`, emit at most one finding (dimension `scope_coverage`, heuristic `priority_absent`, `product_type: "not_applicable"`, `decision_mode: "not_applicable"`, severity ≤ 2) and note the thin-evidence condition in `summary`. Do not fabricate findings.

## What to audit and what to refuse

**Do audit:**
- Whether each plane carries visible, articulated decisions rather than inherited or absent ones.
- Whether the cascade from strategy → scope → structure → skeleton → surface preserves its justifications, or whether a plane drops the reason for its decisions.
- Whether the functional and informational aspects of each plane are treated as distinct concerns where Garrett's duality applies.
- Whether the product's scope reflects the declared strategy, or whether scope is featuritis, checklist-mimicry, or unprincipled accretion.
- Whether the structure (IA + interaction model) is articulated and learnable, or implicit and idiosyncratic.
- Whether the skeleton expresses the structure, honours scope-level priority, and does not defer to platform/library defaults where a decision was required.
- Whether the surface reinforces everything below it, or contradicts the skeleton's priority, the structure's model, or the strategy's intent.
- Whether visible design decisions are conscious (`conscious`), inherited (`default`), copied (`mimicry`), or imposed (`fiat`) — and what the cost is.

**Do not audit:**
- Discoverability at the affordance-signifier layer (Norman). A button nobody can find is Norman; a button that is in the wrong place in the skeleton relative to the structure's priority is Garrett.
- Accessibility defects (WCAG). An inaccessible form is WCAG; a form-versus-content confusion on the structure plane is Garrett.
- Cognitive-bias manipulation (Kahneman). A confirm-shame dialog is Kahneman; a paywall that sits in the skeleton where the priority-1 action should have been is Garrett.
- Posture, excise, and flow (Cooper). A modal that interrupts concentration is Cooper; a modal that exists because the skeleton has no room for a priority-2 path without one is Garrett.
- Business-model coherence (Osterwalder). A broken revenue stream is Osterwalder; a scope that contradicts its own strategy's monetisation assumptions is Garrett's strategy-coherence finding with a shadow over scope.
- Pure visual-style critique. "This colour is ugly" is not a Garrett finding. "This colour, at this size, contradicts the skeleton's priority ranking" is.

## Honest limits of this framework

Elements carries internal tensions that an honest audit must name where it lands on them:

1. **Hierarchical planes vs overlapping work.** Garrett says planes cascade, then softens it to "work on them overlaps but a higher plane cannot finish before a lower one." An architecture finding that says "skeleton was frozen before structure was decided" lives on this seam; it is a real defect, but the cost was imposed by a real constraint. Name the constraint.
2. **Universal model vs web-native vocabulary.** The five-plane model claims applicability beyond the web, but the terminology (navigation design, information architecture, pages, sites) is web-shaped. Applying it to a mobile app, a terminal CLI, or a physical device requires reinterpretation; do not force web vocabulary into a finding where it does not fit.
3. **Fundamental duality vs third axis.** Garrett's functional/informational split does not cleanly hold for products whose primary value is emotional, expressive, or aesthetic (games, social feeds, creative tools). For such products, a `hybrid` tag is often correct, or the duality is simply silent; prefer silence to forcing a false fit.
4. **User-centered vs business-centered decisions.** Garrett treats user needs and product objectives as equal inputs to strategy, but in practice teams usually over-weight one. A finding that scope is driven by business objectives to the exclusion of user needs is legitimate; so is its mirror. Name which side the defect sits on.
5. **Model is descriptive vs prescriptive.** Garrett's taxonomy is useful as a *language* for talking about UX (lingua franca) more than as an *operating system* for producing it. An audit should treat the five planes as a diagnostic scaffold, not a required build order.
6. **Empirical validation absent.** Garrett offers no evidence that projects using this model produce better UX than projects without it. The audit adopts the vocabulary for its diagnostic power, not its predictive power.

This skill audits through Garrett's architectural lens only. It will under-weight:
- **Discoverability / signifier-level defects** — route to `audit-usability-fundamentals` (Norman).
- **Accessibility defects** — route to `audit-accessibility` (WCAG).
- **Decision-psychology manipulation** — route to `audit-decision-psychology` (Kahneman).
- **Product behaviour / posture / excise / etiquette** — route to `audit-interaction-design` (Cooper).
- **Business-model coherence (VP, channels, revenue streams)** — route to `audit-business-alignment` (Osterwalder).
- **Micro-visual craft (spacing tokens, exact colour contrast, grid adherence)** — a dedicated visual-design audit is outside scope.

When the cluster clearly belongs to one of these adjacent frames, say so in `summary` rather than stretching a Garrett concept to cover it.

## Worked example

Input:

```xml
<cluster>
  <label>Streak-loss modal sits where the primary-action path should — skeleton overridden by monetisation fiat</label>
  <ui_context>Duolingo mobile app, mid-lesson. The user has just depleted their last unit of energy and is one question short of completing the lesson. Instead of a lesson-complete screen, a full-bleed modal fills the viewport with a loss-framed headline, a pulsing countdown, a price anchor, a large green "Keep my streak" CTA, a secondary "Watch ads" link, and a de-emphasised "lose streak" dismiss. The skeleton of the lesson screen — progress bar, question area, answer strip — is completely hidden. The modal's visual language matches the marketing site, not the learning surface.</ui_context>
  <html><![CDATA[
  <div role="dialog" aria-modal="true" style="position:fixed;inset:0;background:#fff;z-index:9999">
    <h2 style="font-family:'Feather Bold';font-size:28px;color:#ce1010">STREAK AT RISK</h2>
    <div class="countdown" style="font-size:48px;color:#ff4b4b;animation:pulse 1s infinite">00:23:14</div>
    <p>You'll lose your 800-day streak at midnight.</p>
    <s style="color:#777">$6.99/mo</s> <strong style="color:#58cc02">$3.49/mo</strong>
    <button class="btn-primary" style="background:#58cc02;color:#fff;padding:16px 32px;font-size:20px;width:100%">Keep my streak</button>
    <a href="#" style="color:#1cb0f6;display:block;margin-top:12px">Watch 3 ads to save streak</a>
    <a class="dismiss" style="font-size:11px;color:#999;display:block;margin-top:24px">lose streak</a>
  </div>
  ]]></html>
  <q idx="0">I've been learning for 800 days, and the app has been quietly turning into a subscription funnel the whole time — the lesson UI itself has barely changed, but the non-lesson UI has completely taken over</q>
  <q idx="1">the modal doesn't look like the lesson screen at all, it looks like the pricing page</q>
  <q idx="2">when the streak modal fires there's nothing else on the screen, even the progress bar disappears — it's like the lesson stops existing until you pick</q>
  <q idx="3">I don't know what this app is anymore — is it a learning tool or a subscription product dressed up as one?</q>
  <q idx="4">the 'lose streak' link is grey 11-pixel text at the bottom, and the 'keep my streak' button is a giant green slab — the hierarchy makes the dismiss feel like a bug you shouldn't click</q>
</cluster>
```

Expected output (shape — not verbatim):

```json
{
  "summary": "The cluster evidences a scope-and-skeleton rupture: the product's scope has accreted a subscription funnel that is not reflected in (and contradicts) the learning-tool strategy, and the skeleton of the mid-lesson surface has been overridden by a fiat-style marketing modal that replaces the lesson skeleton wholesale; surface reinforces the override rather than mediating it.",
  "dimension_scores": {
    "strategy_coherence": 2,
    "scope_coverage": 2,
    "structure_navigation": 3,
    "skeleton_wireframe": 1,
    "surface_sensory": 2
  },
  "findings": [
    {
      "dimension": "strategy_coherence",
      "heuristic": "strategy_contradicts_itself",
      "product_type": "hybrid",
      "decision_mode": "conscious",
      "violation": "The product declares a learning-tool strategy but visibly operates on a subscription-funnel strategy in its highest-attention surface; the two strategies are mutually incompatible on the same screen, and users articulate the confusion directly ('is it a learning tool or a subscription product').",
      "severity": 3,
      "evidence_source": ["quotes", "ui_context"],
      "evidence_quote_idxs": [0, 3],
      "recommendation": "Declare which strategy governs the mid-lesson surface (learning-tool) and move monetisation to surfaces outside the lesson boundary; let the two strategies live on different surfaces instead of contending for the same one."
    },
    {
      "dimension": "scope_coverage",
      "heuristic": "scope_creep_mid_build",
      "product_type": "functional",
      "decision_mode": "conscious",
      "violation": "The monetisation surfaces have accreted inside the lesson flow over successive releases without a corresponding re-scoping of the lesson flow itself; the user perceives geological layers ('the lesson UI has barely changed, but the non-lesson UI has completely taken over').",
      "severity": 3,
      "evidence_source": ["quotes"],
      "evidence_quote_idxs": [0],
      "recommendation": "Re-scope the lesson flow explicitly: name the monetisation touchpoints that the scope plane endorses, remove or relocate the ones it does not, and write the constraint that monetisation lives at lesson boundaries, not inside them."
    },
    {
      "dimension": "skeleton_wireframe",
      "heuristic": "skeleton_does_not_honour_priority",
      "product_type": "hybrid",
      "decision_mode": "fiat",
      "violation": "The mid-lesson skeleton is replaced wholesale by a marketing modal; the lesson's own skeleton elements (progress bar, question area, answer strip) are hidden rather than preserved under or beside the modal — the skeleton has no authority over its own canvas on this surface, and the replacement reads as a top-down override of the product's plane logic.",
      "severity": 4,
      "evidence_source": ["quotes", "ui_context", "html"],
      "evidence_quote_idxs": [2],
      "recommendation": "Render the streak-risk surface as a non-replacing overlay that preserves the lesson skeleton underneath; if the modal must block, place it at a lesson boundary where the lesson skeleton has no work to do."
    },
    {
      "dimension": "skeleton_wireframe",
      "heuristic": "competing_calls_to_action",
      "product_type": "functional",
      "decision_mode": "conscious",
      "violation": "The skeleton arranges 'Keep my streak' (paid) and 'Watch 3 ads' (free) as competing primary actions with dramatically different visual weight — the user's intended primary ('lose streak', their actual choice if they do not want to pay or watch ads) is rendered as 11px grey text, inverted from its scope-level priority.",
      "severity": 3,
      "evidence_source": ["quotes", "html"],
      "evidence_quote_idxs": [4],
      "recommendation": "Equalise the three dismissal paths visually — if the user can legitimately choose to lose the streak, that choice should be rendered at equal weight to the retention paths; let surface hierarchy follow user agency, not conversion pressure."
    },
    {
      "dimension": "surface_sensory",
      "heuristic": "visual_language_inherited_from_brand_without_product_fit",
      "product_type": "informational",
      "decision_mode": "mimicry",
      "violation": "The modal's surface language (Feather Bold 28px red headline, pulsing countdown, strike-through price, full-bleed green CTA) is lifted from the subscription marketing site and applied to a learning-surface context; users describe it explicitly as 'looking like the pricing page' inside what was a learning session.",
      "severity": 2,
      "evidence_source": ["quotes", "ui_context", "html"],
      "evidence_quote_idxs": [1],
      "recommendation": "Design a mid-lesson variant of the streak-risk surface in the learning surface's own visual language (muted palette, lesson typography, no loss-framed countdown); keep the marketing palette on the marketing surfaces."
    }
  ]
}
```

The worked example is illustrative. In real audits:
- `evidence_quote_idxs` lists only quotes actually supporting the finding, never padded.
- `product_type: "hybrid"` is reserved for findings that are about the functional/informational seam, not merely span it; prefer a single-type value when the finding lives cleanly in one.
- `decision_mode: "fiat"` requires a concrete stakeholder-override indicator; `mimicry` requires a concrete source-of-copy indicator; `default` requires a concrete library/platform-default indicator. Without such an indicator, use `conscious` (the decision was made; it may still be wrong) or `not_applicable`.
- The same surface can carry findings across multiple dimensions — a mid-flow modal can be a strategy finding, a scope finding, a skeleton finding, and a surface finding simultaneously, each with distinct `heuristic` values that land on the architectural layer where the defect originates.
