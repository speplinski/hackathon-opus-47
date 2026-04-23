# label-cluster — quality rubric

This rubric supports humans and automated graders evaluating the output of
the `label-cluster` skill. It complements `SKILL.md`, which instructs the
model; this file describes what "good" looks like when checking the result.

## Scoring axes

Each produced label is evaluated on three axes.

1. **Schema validity** — binary, enforced programmatically. The response
   is a JSON object with a single key `label` whose value is a string of
   length 1–60, stripped of leading/trailing whitespace. Failure rejects
   the label at ingest; the layer falls back to the `UNLABELED:cluster_NN`
   placeholder and logs the failure.

2. **Anchoring** — requires human judgment. The label points at a theme
   the quotes actually carry. A label that invents context absent from
   the quotes (e.g. `"Expensive subscription"` on a cluster about voice
   recognition) fails this axis. A label may legitimately compress a
   theme in wording that doesn't appear verbatim in any single quote —
   that's the point of naming — but it must not contradict or supplement
   them.

3. **Specificity** — quality-weighted, requires human judgment. A label
   that would fit every cluster in the pipeline (`"UX issues"`,
   `"Problems"`, `"User complaints"`) conveys no signal and fails this
   axis. A label should tell a product owner which part of the app to
   investigate.

## Specificity ladder

Three tiers, roughly ordered from best to worst:

- **Tier 1 — element + behaviour.** Names both what's broken and how.
  Examples: `"Voice recognition marks correct answers wrong"`,
  `"App freezes mid-lesson"`, `"Paywall interrupts flow"`. This is the
  target for most clusters.
- **Tier 2 — element only.** Names the surface but not the symptom.
  Examples: `"Voice recognition"`, `"Paywall"`, `"Energy system"`. Use
  when the cluster is narrow enough that the surface alone is
  actionable, but the symptoms vary too much to summarise.
- **Tier 3 — symptom only.** Names the symptom without the element.
  Examples: `"Frustration"`, `"App too hard"`, `"Too slow"`. Use only
  when the cluster is about a feeling that spans elements. Emit
  sparingly — product owners can't act on a symptom without an element.

Avoid dropping below tier 3 into generic slots like `"UX problems"`,
`"Annoyances"`, `"Bugs"`. Those are placeholder-shaped.

## Common pitfalls

- **Mimicking one quote.** A label that is just the wording of the most
  prominent quote fails specificity when the quote is short and the
  cluster holds different phrasings of the same complaint. Synthesise
  across quotes, don't copy.
- **Meta framing.** Labels that start with `"Cluster of..."`,
  `"Reviews about..."`, `"Users complaining about..."` push the meta
  layer into the label itself. The label IS the theme; no framing.
- **Evaluative wording that isn't in the quotes.** A label calling
  something `"broken"`, `"unacceptable"`, or `"terrible"` when no quote
  uses that wording imports editorial voice into the audit trail.
- **Multi-theme disjunctions.** `"Voice recognition or lessons"` is
  two labels pretending to be one. If the cluster genuinely spans two
  themes, emit `"Mixed complaints"` and let the cluster-coherence audit
  flag it.

## Mixed-complaints path

When the quotes do not share a theme, the correct output is
`{"label": "Mixed complaints"}`. This is a known-unknown signal used by
downstream L4 audits. Treat it as a first-class option, not a failure —
emitting a plausible-sounding but synthetic umbrella is worse than
emitting `"Mixed complaints"` honestly.

## Worked gradings

### Pass

Quotes:

- `"I am speaking but it says wrong"`
- `"I keep getting it wrong"`
- `"always wrong and give you the wrong words"`

Label: `"Voice recognition marks correct answers wrong"`

Grade: axis 1 passes (valid JSON, 47 chars), axis 2 passes (every
quote is about recognition-as-wrong), axis 3 passes (tier 1 —
element + behaviour).

### Fail — axis 2

Quotes:

- `"freezing all the time"`
- `"keeps freezing"`
- `"app freezes"`

Label: `"Expensive subscription"`

Grade: axis 1 passes, axis 2 fails (no quote mentions subscription or
price), axis 3 passes on form but irrelevant given axis 2 failure.

### Fail — axis 3

Quotes:

- `"can't complete lesson"`
- `"lesson stuck"`
- `"lesson doesn't progress"`

Label: `"UX issues"`

Grade: axis 1 passes, axis 2 arguably passes (lessons do have UX
issues), axis 3 fails — the label would fit every cluster in the
pipeline and gives no actionable signal.
