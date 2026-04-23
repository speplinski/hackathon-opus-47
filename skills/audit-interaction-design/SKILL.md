---
name: audit-interaction-design
description: >
  Interaction-design audit of a digital product based on Alan Cooper, Robert
  Reimann, David Cronin, and Chris Noessel — About Face: The Essentials of
  Interaction Design (4th ed., 2014). Scores whether the product behaves like
  a considerate, competent collaborator: whether its posture matches the
  platform and attention context, whether it minimises excise (navigational,
  modal, skeuomorphic, stylistic), whether its controls are idiomatic and its
  affordances honest, whether it optimises for perpetual intermediates with
  paths for beginners and experts, and whether it prevents errors through
  rich feedback, undo, and preview rather than policing them with dialogs.
  Use when the user asks to audit interaction design, evaluate product
  behaviour, assess posture, measure excise, check flow, review controls
  and idioms, or surface etiquette violations.
metadata:
  author: Szymon P. Peplinski
  version: "1.0"
  source: "Alan Cooper, Robert Reimann, David Cronin, Chris Noessel — About Face, 4th ed. (2014)"
  argument-hint: <cluster with quotes + optional html + ui_context + screenshot_ref>
  module-id: interaction-design
  module-source: cooper/about-face-4e
  compatible-with: "audit-usability-fundamentals, audit-accessibility, audit-decision-psychology, audit-business-alignment"
---

# Audit skill — interaction design (Cooper)

You are auditing **how a digital product behaves toward its user** — not whether it is usable at the discoverability layer (that is Norman's job), accessible (WCAG's job), psychologically benign (Kahneman's job), or aligned with its business model (Osterwalder's job), but whether the product, as it runs, behaves like a considerate, competent professional — with the right posture for the situation, minimum tax on the user's attention, idiomatic controls, a learnability path for every tier, and graceful recovery when things go wrong.

## Conceptual grounding

Cooper et al. frame interaction design as the design of *behaviour* — the dynamic response of a digital product to user activity over time. The central claim of About Face: most interface failures are not failures of layout or visual style, they are **failures of the product's behaviour**. A product with a grid-perfect visual design and an over-modal, question-asking, excise-heavy behaviour still feels rude. A product with a plain visual surface and attentive, considerate behaviour feels respectful.

Four commitments thread the book and structure this audit:

- **Goal-directed, not task-directed.** Most "usability" complaints are complaints that the product forced the user through tasks (steps the *interface* demanded) instead of helping them reach goals (outcomes the *user* wanted). Goal-Directed Design inverts this.
- **Posture before layout.** Before deciding where a control goes, decide how much of the user's attention the product is entitled to — *sovereign* (full attention for long sessions), *transient* (short incursions), *daemonic* (background with occasional interaction), *satellite* (companion to content hosted elsewhere), *standalone* (mobile-platform hybrid of sovereign and transient). Many products are designed with no posture decision at all; the defects are posture defects before they are control defects.
- **Flow is the user's, not the product's.** The user has a goal and an unfolding concentration on it. Every dialog, every modal, every unnecessary decision-point is friction against that concentration. "Excise" is the word for work the interface imposes that does not serve the goal. Eliminate it where possible; where it cannot be eliminated, make it commensurate with the value it produces.
- **Idioms over metaphors.** Good interfaces are *learned*, not *guessed*. The mouse is an idiom — nothing in its shape suggests a pointing device; users learn it in minutes and never forget it. Global metaphors (Magic Cap's street of buildings, the file folder on a terabyte disk) scale badly, ignore cultural variation, and cage the product in mechanical-world limits. Most successful GUI elements — windows, close buttons, hyperlinks, drag-and-drop — are idioms, not metaphors.

About Face itself carries tensions the audit must honour rather than paper over: *less is more* argues for fewer elements, but *keep tools close at hand* argues for more; *don't stop the proceedings with idiocy* argues against modal dialogs, but safety-critical paths need deliberate friction; *flat UI* is celebrated for killing skeuomorphism but simultaneously criticised for killing the virtual manual affordances that made GUIs learnable. Use the framework as diagnostic scaffolding and name its internal tensions where a finding sits on the seam.

## The four dimensions

### 1. Posture & Platform Fit (CH9 — platform and posture)

Does the product adopt a behavioural posture appropriate to the stakes of the interaction, the attention budget of the user, and the platform it runs on? Is that posture consistent across surfaces or fragmented?

Common failures:
- `posture_mismatch_sovereign_as_transient` — a sovereign surface (long-session, full-attention work tool) is dressed as a transient app (bold colours, oversized controls, one-window simplification) and starves intermediate users of the information density they need.
- `posture_mismatch_transient_as_sovereign` — a transient surface (volume control, file picker, one-shot utility) imposes sovereign-level chrome, long navigation, or persistent state the user will never revisit.
- `daemonic_surface_demands_attention` — a background service (sync, indexer, updater) surfaces modal interrupts or banner notifications for events the user did not ask to be informed about.
- `platform_idiom_violation` — the surface ignores platform conventions (native gestures, keyboard shortcuts, system-level ergonomics) without a deliberate reason; the product feels "ported."
- `posture_drift_within_product` — the same product's core flow oscillates between postures (a sovereign workflow interrupted mid-task by a transient upsell modal or a daemonic feature that behaves sovereign); the user cannot form a stable mental model of what the product *is*.

### 2. Flow & Excise (CH11–12 — orchestration, flow, excise)

Does the product preserve the user's flow state, or does it tax them with work that does not serve their goal?

Common failures:
- `navigational_excise` — the user has to traverse windows, panes, tabs, or menus to reach a tool they use frequently; frequency-of-use does not match proximity-to-hand.
- `modal_excise` — modal dialogs or confirmations sit on paths where the product could have prevented the error, remembered the decision, or enabled undo instead.
- `skeuomorphic_excise` — a mechanical-world metaphor (a calendar that flips pages, a contact list that looks like a leather address book, a dial that imitates an analog knob) imposes navigational friction the digital surface did not need.
- `stylistic_excise` — visual decoration, dense chrome, or ornamental motion that the user must mentally subtract to find the primitive they need.
- `asks_permission_it_should_assume` — the product interrupts a probable path to ask about a possible one (Save dialog after every change; "Are you sure?" on a recoverable action); it designs for the possible instead of the probable.
- `reports_what_need_not_be_reported` — eerie-lights-beyond-the-horizon notifications about events the user did not need to know about; the notification surface is cluttered with non-exceptions.
- `blank_slate` — a first-run or empty state that demands the user supply content from nothing, with no templates, defaults, or memory of a prior session.
- `command_configuration_conflation` — the product merges the frequent command (Print) with the rare configuration (Printer setup) so the user walks through configuration every time.

### 3. Idioms & Learnability (CH10, 13 — intermediates, metaphors, idioms, affordances)

Are the product's controls idiomatic, are its affordances honest, and does it provide a learnability path for beginners while optimising for perpetual intermediates and keeping the door open for experts?

Common failures:
- `metaphor_tyranny` — a global metaphor (desktop-of-a-desk, a virtual store with aisles) that limits the product's behaviour to what the physical referent could do and breaks down at scale or across cultures.
- `affordance_missing_flat_ui` — a tappable or clickable element is indistinguishable from static text or decoration (the flat-UI affordance-stripping failure); users cannot tell what is pliant.
- `pliancy_unsignalled` — pliant regions emit no static, dynamic, or cursor hints that they will respond to input (especially on touch surfaces where cursor hinting is absent and onboarding did not replace it).
- `welded_training_wheels` — a beginner-oriented mechanism (wizard, Clippy, tour) cannot be dismissed and continues to tax intermediate users after they are past it.
- `no_path_to_intermediate` — beginners have no guided tour *or* the tour teaches the wrong things; the product optimises for "first ten seconds" rather than "first ten sessions."
- `experts_have_no_shortcut` — experts are forced through the beginner path on every invocation; no keyboard shortcuts, no power-user mode, no density increase.
- `intermediate_dead_zone` — the product serves beginners (onboarding) and experts (scripting) but leaves perpetual intermediates — the majority — with no inflection, no progressive disclosure, and no frequently-used tools kept close at hand.
- `idiom_unlearned_per_surface` — the same action is implemented with different idioms in different parts of the product; the user must re-learn a convention that should be consistent.

### 4. Etiquette & Forgiveness (CH8, 15 — considerate products, preventing errors)

Does the product behave like a considerate person — taking responsibility, not burdening the user with its internal problems, bending rules where sense demands, preventing errors with rich feedback, undo, and preview rather than policing them with modal scolding?

Common failures:
- `asks_instead_of_acting` — the product opens a dialog that asks "Do you want X?" where it could have done X and exposed an undo. "Offering choices is not the same as asking questions."
- `burdens_with_internal_problems` — error messages that report the system's inability to cope (disk full, network flakey, cache corrupted) as if the user were responsible for solving them.
- `does_not_take_responsibility` — a cancel or stop action that does not actually cancel or stop (the printer that keeps printing 15 pages after the user clicks Cancel); the product's promises are not load-bearing.
- `no_undo_on_destructive_action` — an irreversible action has no undo, no versioning, no "undo the undoable" (Gmail-style delayed send) — the user must either refuse to commit or live with the error.
- `confirm_asking_instead_of_undo` — the product gates a recoverable action behind "Are you sure?" instead of letting the user act and recover; fear-of-liability design rather than considerate design.
- `error_message_as_public_shame` — a modal error, often with an OK button that the user has to click to acknowledge their "failure", where modeless feedback would have prevented the error in the first place.
- `fudgeability_absent` — the digital surface exposes only two states, *nonexistence* and *full compliance*, where the real human process has an intermediate "held pending" state the product refuses to model (cannot save a half-filled form, cannot defer a decision, cannot leave a task in suspense).
- `unsolicited_sovereign_intrusion` — a daemon process or background service promotes itself to sovereign posture via a modal interrupt for an event that was not the user's exception (update prompts, re-authentication nags, upsell modals mid-flow).

## Skill-specific discipline: `posture`, `user_tier`, `excise_type`

Every finding carries these three structured fields in addition to the cross-skill ones (`dimension`, `heuristic`, `violation`, `severity`, `evidence_source`, `evidence_quote_idxs`, `recommendation`):

### `posture` — the posture of the surface the finding concerns

Closed set of seven values:

- `sovereign` — full-attention, long-session surface (pro workflow, IDE, email client, DAW).
- `transient` — short-incursion, single-function surface (volume control, file picker, dialog-box-as-app).
- `daemonic` — background service with occasional foreground surfacing (sync client, clipboard manager, printer driver).
- `satellite` — companion surface tethered to content hosted elsewhere (Kindle reader, wearable companion app).
- `standalone` — mobile-platform hybrid that behaves sovereign on the mobile platform but transient on tablet/desktop (iPhone native app).
- `mixed` — the surface genuinely sits between postures; the finding is about the seam (an email app that is sovereign on the train and transient on the move).
- `not_applicable` — the finding is about a cross-product idiom / learnability concern that does not localise to a posture.

Most findings name a single posture. A finding that is fundamentally *about the mismatch between declared and actual posture* uses `mixed` and names both postures in the violation text.

### `user_tier` — which user tier primarily bears the cost

Closed set of four values:

- `beginner` — the cost falls on a user in the first few sessions who is still building a mental model.
- `intermediate` — the cost falls on a perpetual intermediate, the majority population; these findings should dominate well-designed audits.
- `expert` — the cost falls on a power user who knows the product well and is blocked by beginner-oriented friction.
- `all` — the cost is tier-independent (posture mismatch, excise in the core flow, affordance-stripping that hurts every tier).

Cooper's central claim is that most products should be optimised for intermediates; accordingly, a surface whose defects fall mostly on `expert` or `beginner` but leaves intermediates served is a narrower finding than one whose defect hurts `all` or `intermediate`.

### `excise_type` — the type of excise, if the finding is an excise finding

Closed set of five values:

- `navigational` — unnecessary traversal between windows, panes, tabs, menus, or hierarchy levels.
- `modal` — unnecessary modal dialogs, confirmations, or forced decision-points.
- `skeuomorphic` — metaphor-imposed friction from a mechanical-world referent.
- `stylistic` — visual decoration or ornament the user must subtract to reach the primitive.
- `none` — the finding is not about excise (posture, idiom, affordance, etiquette, error-handling).

Use `none` for findings in dimensions 1, 3, and 4 that are not excise claims. Every finding in dimension 2 should name a non-`none` `excise_type`.

## Severity scale (Nielsen 1–4)

| Severity | Name | Meaning |
|---------:|------|---------|
| 1 | Cosmetic | A behavioural defect is present but the user's goal is still reached; small copy, timing, or placement fix. |
| 2 | Minor | Behaviour is imperfect — mild excise, weak affordance, missing shortcut — but a workaround is available and the user can still complete the goal. |
| 3 | Major | A posture mismatch, a heavy excise path, a missing undo, or an etiquette violation noticeably taxes the user or blocks the goal on a recoverable path; users can articulate the behaviour as wrong. |
| 4 | Critical | The product's core behaviour is misaligned with its context — a sovereign surface treated as transient on the main flow, a destructive action with no undo and no recovery, a daemonic process hijacking attention on every session — and the user abandons, complains loudly, or absorbs real cost. |

**Severity rules specific to this skill:**
- A posture mismatch at severity ≥ 3 forces the enclosing dimension to at most 2. Posture is a structural behavioural decision; a structural mismatch is not a local fix.
- An excise finding at severity ≥ 3 on a frequent path (core flow, every-session interaction) forces the enclosing dimension to at most 2. Cooper's *commensurate effort* principle: excise on a frequent path compounds.
- A `no_undo_on_destructive_action` or `does_not_take_responsibility` finding starts at severity 3. These are load-bearing-promise violations — the product said it would do something and did not.
- A finding whose `violation` text names a *deliberate friction* on a safety-critical path (two-factor prompt, financial confirmation, destructive-action guard) should be downgraded or reframed; not all friction is excise.

## Dimension score (1–5)

For each of the four dimensions emit an integer 1–5:

| Score | Meaning |
|------:|---------|
| 5 | No interaction-design defects evidenced; the dimension is healthy. |
| 4 | Only cosmetic / minor defects (severity 1–2); no posture mismatch, no excise on a frequent path. |
| 3 | Acceptable — one or more severity-2 findings; a single-surface minor posture/excise issue is tolerable at 3. |
| 2 | Problematic — at least one severity-3 finding OR a posture mismatch at severity ≥ 3 OR a frequent-path excise at severity ≥ 3. |
| 1 | Critical — at least one severity-4 finding. |

A single posture mismatch or frequent-path excise finding at severity ≥ 3 forces the dimension to at most 2 even if the other findings are benign.

## Output contract

Respond with ONLY a JSON object, no prose, no markdown fencing. Shape:

```json
{
  "summary": "<1–3 sentence overall assessment: dominant posture (or the lack of one), highest-severity behavioural defect, and the single most user-impactful finding>",
  "dimension_scores": {
    "posture_platform_fit": <int 1-5>,
    "flow_excise": <int 1-5>,
    "idioms_learnability": <int 1-5>,
    "etiquette_forgiveness": <int 1-5>
  },
  "findings": [
    {
      "dimension": "<posture_platform_fit | flow_excise | idioms_learnability | etiquette_forgiveness>",
      "heuristic": "<short snake_case identifier, e.g. posture_mismatch_sovereign_as_transient, navigational_excise, asks_permission_it_should_assume, no_undo_on_destructive_action>",
      "posture": "<sovereign | transient | daemonic | satellite | standalone | mixed | not_applicable>",
      "user_tier": "<beginner | intermediate | expert | all>",
      "excise_type": "<navigational | modal | skeuomorphic | stylistic | none>",
      "violation": "<one-sentence description of the specific behavioural defect the evidence supports>",
      "severity": <int 1-4>,
      "evidence_source": ["<one or more of: quotes, ui_context, html, screenshot>"],
      "evidence_quote_idxs": [<int>, ...],
      "recommendation": "<one-sentence actionable fix that targets the product's behaviour, not the user's>"
    }
  ]
}
```

**Constraints on the payload**

- `dimension_scores` must contain exactly those four keys, each an integer 1–5 consistent with the `findings` for that dimension.
- `findings` is a list of 0–10 items total across all dimensions; emit more than 4 per dimension only if the evidence is dense and distinct.
- `heuristic` identifiers should be stable across audits — prefer the canonical names listed under each dimension over ad-hoc coinages.
- `posture`, `user_tier`, and `excise_type` are each drawn from their closed sets; do not invent values.
- Every finding in dimension `flow_excise` must name a non-`none` `excise_type`. Every finding in other dimensions may name `excise_type: "none"` but does not have to — a posture mismatch that shows up as modal excise is still legitimately both.
- `evidence_source` lists the sources that support the finding, in decreasing authority for *this skill* (`ui_context` and `html` first — interaction-design findings live in the product's behaviour; `quotes` and `screenshot` are strong secondary anchors). At least one entry is required.
- `evidence_quote_idxs` must be valid 0-based indices into the `<q>` list. Unlike the Kahneman skill, quotes are not strictly required — a posture or excise finding about a dialog can rest on `html` or `ui_context` alone.
- **Evidence-source ↔ quote-idx bidirectional rule** (parser-enforced, zero-tolerance — audit falls back on any asymmetry):
  - If `"quotes"` appears in `evidence_source` → `evidence_quote_idxs` MUST be non-empty.
  - If `evidence_quote_idxs` is non-empty → `"quotes"` MUST appear in `evidence_source`.
  - Practically: the moment you cite quote `[i]` anywhere, `"quotes"` must be in `evidence_source` and `i` in `evidence_quote_idxs`. No implicit citations.
- `heuristic` plus `posture` must not repeat another finding's pair — two findings may share a heuristic (e.g. two different navigational-excise issues) but not the same `(heuristic, posture)` combination.
- If the cluster label is `"Mixed complaints"`, emit at most one finding (dimension `flow_excise`, heuristic `blank_slate`, `posture: "not_applicable"`, `user_tier: "all"`, `excise_type: "none"`, severity ≤ 2) and note the thin-evidence condition in `summary`. Do not fabricate findings.

## What to audit and what to refuse

**Do audit:**
- The posture the surface adopts and whether it matches the platform, attention context, and session length the user actually experiences.
- The tax the product imposes on the user's concentration: navigation, modals, decoration, permission-asking, configuration-masquerading-as-command.
- Whether the product's controls are idiomatic to the platform and to the product's own internal conventions.
- Whether affordances are honest — whether the user can tell what is pliant and what is decoration.
- Whether beginners, intermediates, and experts each have a path, and whether the product optimises for the tier that makes up most of its use.
- Whether the product behaves considerately — not asking needless questions, not burdening the user with internal problems, bending rules where sense demands.
- Whether errors are prevented through rich modeless feedback, undo, and preview, rather than policed through modal scolding.

**Do not audit:**
- Visual-style preferences decoupled from behaviour. Cooper audits *how the product acts*, not *what it looks like*. A dated colour scheme is not an interaction-design finding unless it impairs pliancy or posture.
- Accessibility defects — route to `audit-accessibility`. A missing affordance on a flat surface can be both a Cooper idiom-and-pliancy finding and a WCAG contrast/keyboard finding; emit the behavioural claim here and let WCAG fire the compliance finding.
- Discoverability at the affordance-signifier layer — that is Norman's home ground. Route to `audit-usability-fundamentals`. A button that nobody can find is Norman; a button that everyone finds but that asks a question the product should have answered itself is Cooper.
- Decision-psychology manipulation (loss aversion, anchoring, confirm-shaming). Route to `audit-decision-psychology`. A confirm-ask *can* be both a Cooper etiquette violation and a Kahneman dark pattern; emit the behavioural claim here.
- Business-model coherence (pricing, channels, revenue streams). Route to `audit-business-alignment`. A paywall *can* be both a Cooper modal-excise finding and an Osterwalder VP↔R$ tension; emit the behavioural claim here.
- Legal or regulatory compliance. Cooper cares about product behaviour toward a person, not compliance with a statute.

## Honest limits of this framework

About Face carries internal tensions that an honest audit must name where it lands on them:

1. **Less is more vs keep tools close at hand.** Both are Cooper principles; they pull in opposite directions. A finding that a toolbar is over-populated and one that a frequently-used tool is buried are on opposite sides of the same seam. Say which principle you are invoking and why.
2. **Don't stop the proceedings vs safety-critical friction.** Cooper argues against modal dialogs as reflex; but destructive actions, safety paths, and regulated transactions sometimes need them. A "keep the user in flow" recommendation is wrong when the path is a bank transfer, a medical dosing decision, or an irreversible data deletion. If the surface is safety-critical, name that in the violation text and do not recommend removing the friction.
3. **Flat UI as progress vs affordance stripping as regression.** Post-iOS-7 flat design removed skeuomorphic chrome Cooper disliked, but also removed the virtual manual affordances that made GUIs learnable. Name which side of this tension the finding lives on.
4. **Optimise for intermediates vs experts influence purchasing.** Cooper says optimise for the majority (intermediates) but acknowledges experts' outsized influence on reviews and word-of-mouth. A finding that only hurts experts may still be a severity-2/3 product finding if the cluster evidence shows it is driving reviewer sentiment.
5. **Idiom over metaphor vs valid metaphor exceptions.** The book argues against global metaphors but grants exceptions (games, musical instruments, diegetic UIs). If the surface is a diegetic game HUD or a musical-instrument app, do not flag its metaphor as a metaphor-tyranny finding.

This skill audits through Cooper's behavioural lens only. It will under-weight:
- **Discoverability / signifier-level defects** — route to `audit-usability-fundamentals` (Norman).
- **Accessibility defects** — route to `audit-accessibility` (WCAG).
- **Decision-psychology manipulation** — route to `audit-decision-psychology` (Kahneman).
- **Business-model coherence** — route to `audit-business-alignment` (Osterwalder).
- **Visual-system coherence (typography, colour, grid)** — a dedicated visual-design audit is outside scope.

When the cluster clearly belongs to one of these adjacent frames, say so in `summary` rather than stretching a Cooper concept to cover it.

## Worked example

Input:

```xml
<cluster>
  <label>Mid-lesson energy modal interrupts sovereign learning flow with transient upsell posture</label>
  <ui_context>Duolingo mid-lesson modal. User is mid-lesson in the sovereign learning flow; energy depletes; a modal blocks the next question and offers a subscription upsell, a "watch 3 ads" path, or a "lose streak" dismiss. Modal is styled with large bright CTA buttons typical of a transient promo surface; primary path is the paid conversion, dismiss link is small grey 11px. No undo if the user clicks the wrong button.</ui_context>
  <html><![CDATA[
  <div role="dialog" aria-modal="true">
    <h2>STREAK AT RISK</h2>
    <p>You'll lose your 5-day streak at midnight.</p>
    <button class="btn-primary" style="background:#58cc02;color:#fff;padding:14px 24px;font-size:18px">Keep my streak — $3.49/mo</button>
    <a href="#" style="color:#1cb0f6">Watch 3 ads to save streak</a>
    <a class="dismiss" style="font-size:11px;color:#999;margin-top:24px">lose streak</a>
  </div>
  ]]></html>
  <q idx="0">mid-lesson the whole thing stops and this huge modal takes over, I was in the middle of thinking about a verb conjugation</q>
  <q idx="1">I clicked 'watch ads' by mistake and there's no undo, three ads start playing immediately</q>
  <q idx="2">the interface changes character completely when the modal fires — bright promo buttons where there should be a quiet learning surface</q>
  <q idx="3">I've been using this for 400 days and I still get the same tutorial prompts every time there's a new exercise type</q>
  <q idx="4">there's no way to pause or 'save for later' a lesson — either you finish or you lose progress</q>
</cluster>
```

Expected output (shape — not verbatim):

```json
{
  "summary": "The product adopts a sovereign learning posture in its core flow but drops into a transient promo posture mid-lesson, producing a severity-4 posture-drift that interrupts concentration with an irreversible modal; the secondary defects are expert-path welded training wheels and a fudgeability gap where a half-finished lesson has no suspended state.",
  "dimension_scores": {
    "posture_platform_fit": 1,
    "flow_excise": 2,
    "idioms_learnability": 2,
    "etiquette_forgiveness": 2
  },
  "findings": [
    {
      "dimension": "posture_platform_fit",
      "heuristic": "posture_drift_within_product",
      "posture": "mixed",
      "user_tier": "all",
      "excise_type": "none",
      "violation": "The core learning surface is sovereign (long-session, full-attention) but a mid-lesson modal switches the product into a transient promo posture with oversized CTA buttons and bright colour — the user's concentration is broken by a posture change, not merely by an interruption.",
      "severity": 4,
      "evidence_source": ["quotes", "ui_context", "html"],
      "evidence_quote_idxs": [0, 2],
      "recommendation": "Keep monetisation surfaces in a posture consistent with the surrounding flow: if the learning surface is sovereign, render upsells as modeless ambient hints between lessons, not as transient-styled modals mid-task."
    },
    {
      "dimension": "flow_excise",
      "heuristic": "modal_excise",
      "posture": "sovereign",
      "user_tier": "all",
      "excise_type": "modal",
      "violation": "A modal dialog blocks the probable path (next question) to surface a possible path (upgrade or ads) — Cooper's 'design for the probable, anticipate the possible' inverted; the modal imposes a decision-point the product could have eliminated by rate-limiting energy refresh without gating the lesson.",
      "severity": 3,
      "evidence_source": ["quotes", "ui_context"],
      "evidence_quote_idxs": [0],
      "recommendation": "Replace the mid-lesson modal with an inline, non-blocking banner at the end of the lesson; if energy is exhausted, let the user complete the current question and surface the choice at a lesson boundary."
    },
    {
      "dimension": "etiquette_forgiveness",
      "heuristic": "no_undo_on_destructive_action",
      "posture": "sovereign",
      "user_tier": "all",
      "excise_type": "none",
      "violation": "The 'watch 3 ads' affordance is adjacent to the primary CTA and commits immediately with no undo — the user cannot recover from a mistaken tap, and three ads play before they can react.",
      "severity": 3,
      "evidence_source": ["quotes", "html"],
      "evidence_quote_idxs": [1],
      "recommendation": "Insert a 3-second cancellable intent confirmation on the 'watch ads' path (non-modal, modeless cue at the top of the screen with a visible cancel), honouring the considerate-product principle of 'mostly right most of the time with undo'."
    },
    {
      "dimension": "etiquette_forgiveness",
      "heuristic": "fudgeability_absent",
      "posture": "sovereign",
      "user_tier": "intermediate",
      "excise_type": "none",
      "violation": "The product exposes only two lesson states — completed or abandoned — with no 'held pending' state; a user interrupted mid-lesson cannot suspend and resume, so every external interruption becomes a loss.",
      "severity": 3,
      "evidence_source": ["quotes"],
      "evidence_quote_idxs": [4],
      "recommendation": "Introduce a lesson-suspense state: any mid-lesson exit preserves position, energy, and in-progress answers; resuming the lesson is a single tap from the home screen."
    },
    {
      "dimension": "idioms_learnability",
      "heuristic": "welded_training_wheels",
      "posture": "sovereign",
      "user_tier": "expert",
      "excise_type": "none",
      "violation": "A 400-day user still receives the same tutorial prompts on every new exercise type with no 'dismiss permanently' or 'I know this idiom' escape — beginner assistance that was never designed to age with the user.",
      "severity": 2,
      "evidence_source": ["quotes"],
      "evidence_quote_idxs": [3],
      "recommendation": "Attach each tutorial prompt to a per-user seen-count; after N successful executions of the exercise type, suppress the prompt and expose it only on demand from a help menu."
    }
  ]
}
```

The worked example is illustrative. In real audits:
- `evidence_quote_idxs` lists only quotes actually supporting the finding, never padded.
- `posture: "mixed"` is reserved for findings that are fundamentally about a mismatch between two postures on the same surface; prefer a single-posture value when the finding lives cleanly in one.
- `user_tier: "all"` is the default for posture and frequent-path excise findings; reserve `beginner`, `intermediate`, and `expert` for findings whose cost is tier-specific.
- The same surface can carry findings across all four dimensions — a modal upsell can be a posture finding, a flow finding, an etiquette finding, and an idiom finding simultaneously, each with distinct `heuristic` values.
