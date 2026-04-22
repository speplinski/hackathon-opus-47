# structure-of-complaint — quality rubric

This rubric supports humans and automated graders evaluating the output of the
`structure-of-complaint` skill. It complements `SKILL.md`, which instructs the
model; this file describes what "good" looks like when checking the result.

## Scoring axes

Each produced graph is evaluated on four axes.

1. **Schema validity** — binary, enforced programmatically. The graph
   parses against `schemas.ComplaintGraph`: 3–7 nodes with unique `node_id`,
   edges reference existing nodes, no self-loops, offsets consistent with
   quote length. Failure rejects the graph at ingest. **Under-minimum
   graphs (fewer than 3 nodes) that match SKILL.md's thin-review guidance
   — the review genuinely lacks three cleanly anchorable spans — are the
   intended routing to the quarantine path, not a rubric failure. Record
   them as `thin_review`, not as an axis-1 quality miss.**

2. **Closed-vocabulary compliance** — binary, enforced programmatically.
   Every `node_type` is one of `{pain, expectation, triggered_element,
   workaround, lost_value}`; every `relation` is one of `{triggers,
   violates_expectation, compensates_for, correlates_with}`. Failure
   rejects the graph (Pydantic `Literal` mismatch).

3. **Faithful anchoring** — requires human judgment. Verbatim quotes are
   taken in context. A span that is technically a substring of the review
   but wrenches meaning from its surrounding clause fails this axis.
   Example: quoting `"love"` from `"I don't love this"` as evidence of
   positive sentiment is a faithfulness failure even though the substring
   check passes. Flagged during pilot spot-checks; not an automatic
   production gate — a graph that fails axis 3 but passes 1 and 2 still
   ingests, but the review_id is added to the pilot quarantine set for
   re-examination.

4. **Typing accuracy** — quality-weighted, requires human judgment. Each
   node is labelled with the best-fitting type; edges point in the correct
   direction and match the relation's typical usage (see below). This is
   where most quality disagreements will land. A single-node typing
   disagreement does not reject the graph but contributes to the pilot's
   overall accuracy metric.

## Per-type guidance

### `pain`

- **Best fit**: an explicit negative emotion or suffering clause —
  `"annoying"`, `"frustrated"`, `"hate this"`, `"so bad"`. Also literal
  statements of a bad experience: `"I can't do my lessons"`,
  `"it takes forever"`.
- **Near miss — prefer `triggered_element`**: if the span names the product
  mechanic that *causes* the pain rather than the pain itself. In
  `"The energy system is broken"`, the span `"energy system"` is
  `triggered_element`; the pain sits in `"broken"`.
- **Near miss — prefer `lost_value`**: if the span names what the user
  *lost* rather than what they feel. `"Lost my motivation"` is
  `lost_value`, not `pain`.

### `expectation`

- **Best fit**: a statement of what the user assumed, wanted, or previously
  had. `"I used to be able to"`, `"should be free"`, `"I expected"`.
- Temporal framings (`"used to"`, `"before the update"`) count as
  `expectation` — the user is grounding the expectation in past experience.
- **Near miss — prefer `pain`**: in `"I wanted to study but can't"`, the
  span `"wanted to study"` is `expectation`; the span `"can't"` is `pain`.
- **Near miss — prefer omission**: when the expectation is only *implicit*
  in the complaint and the review offers no clean anchor for it (e.g. `"I
  am speaking"` where the implied expectation is correct recognition),
  omit the `expectation` node rather than stretching a weak span. A graph
  without `expectation` is preferable to one with a padded anchor.

### `triggered_element`

- **Best fit**: the specific product feature, mechanic, UI surface, copy,
  or ad behaviour that the user points to as the cause. Example anchors
  from product reviews: `"energy system"`, `"checkout button"`,
  `"new feed algorithm"`, `"premium subscription"`, `"pop-up ads"`.
- A generic reference (`"this app"`, `"the new update"`) is a weaker but
  acceptable anchor when the review does not name a specific feature.

### `workaround`

- **Best fit**: what the user does — or has started doing — to cope.
  `"I just use Memrise"`, `"I pay for premium"`, `"I skip these lessons"`.
- **Near miss — prefer `expectation`**: `"I wish there was a way around it"`
  is `expectation` (unrealised wish), not `workaround`.

### `lost_value`

- **Best fit**: the benefit, outcome, or affordance the user lost.
  `"motivation"`, `"learning progress"`, `"my streak"`, `"the fun"`,
  `"trust"`.
- **Also fits**: consequence clauses that name what the user no longer
  gets — `"changes the meaning of the translated message"` (lost accurate
  meaning), `"prevent you from doing more lessons"` (lost ability to
  progress). These are `lost_value`, not `pain`, because they name the
  *thing lost* rather than the feeling.
- This is the node_type most commonly skipped; emit it whenever the review
  names a concrete thing the user no longer has.

## Relation selection

Edges are optional. Emit an edge only when the text clearly supports it. If
in doubt, omit — a missing edge is a smaller quality hit than a wrong one.

- `triggers` — the canonical causal edge, typically
  `triggered_element → pain` or sometimes `triggered_element → lost_value`.
  The most common edge.
- `violates_expectation` — the element frustrates a stated expectation.
  Source is typically `triggered_element`, destination `expectation`.
- `compensates_for` — the workaround addresses a pain. Source `workaround`,
  destination `pain`.
- `correlates_with` — a loose association, used when no stronger relation
  fits. Prefer omitting the edge over using `correlates_with` as a
  fallback.

Directionality matters: edges are `src → dst`. A confused direction is a
rubric failure even if both endpoint nodes are typed correctly.

## Common pitfalls

These are failure modes to watch for when reviewing outputs.

**Padding to reach three nodes.** If the review is thin, a model may
paraphrase or repeat spans to satisfy the `min_length=3` constraint. The
correct behaviour is to emit fewer nodes and let the pipeline reject the
graph. A graph that contains repeated or near-repeated quotes across
different `node_id`s is suspect. **Substring containment counts as
padding too**: if one node's quote is fully contained within another
node's quote (e.g. `"my phone"` ⊂ `"not on my phone"`), the two nodes
are almost certainly splitting one concept — collapse to one, or reach
for a different anchor.

**Compound emotion + element spans.** A span like
`"disappointed with updates"` carries two roles — the feeling and the
thing the feeling is about. Splitting into `pain` = `"disappointed"` +
`triggered_element` = `"updates"` is preferable to forcing both under
one `pain` node, which hides the element from downstream L3 clustering.

**Over-long quotes.** Multi-sentence quotes dilute the node's semantic
role and increase the chance of overlap with another node's quote. Prefer
short, focused spans. A node whose quote exceeds roughly 60 characters
should be scrutinised.

**Conflating `pain` and `lost_value`.** These are adjacent — pain is the
feeling, lost_value is the loss. A review can carry both. When in doubt,
pick the one whose wording is more literal in the span.

**Inventing relations.** Models sometimes generate edges that are
plausible but not grounded in the text. An edge that is not directly
supported by a plain reading of the review should be omitted.

## Worked examples

### Example A — rich review, 5 node_types

Review:
> The new practice mode ruins the streak thing. Before this update I'd log
> in every day and feel good about my progress. Now I just skip practice
> altogether. No streak, no point.

Output:

```json
{
  "nodes": [
    {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "new practice mode"},
    {"node_id": "n2", "node_type": "pain", "verbatim_quote": "ruins the streak thing"},
    {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "log in every day and feel good about my progress"},
    {"node_id": "n4", "node_type": "workaround", "verbatim_quote": "skip practice altogether"},
    {"node_id": "n5", "node_type": "lost_value", "verbatim_quote": "No streak, no point"}
  ],
  "edges": [
    {"src": "n1", "dst": "n2", "relation": "triggers"},
    {"src": "n1", "dst": "n3", "relation": "violates_expectation"},
    {"src": "n4", "dst": "n2", "relation": "compensates_for"}
  ]
}
```

Why this passes: 5 distinct node_types, each anchored by a short faithful
quote. Edges are grounded in the text — the reviewer literally says
_"Before this update I'd log in every day..."_ (violated expectation) and
_"Now I just skip"_ (workaround). `correlates_with` is not used because
stronger relations fit.

### Example B — short review, 3 nodes (no padding)

Review:
> Paywall is annoying. Used to be free.

Output:

```json
{
  "nodes": [
    {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "Paywall"},
    {"node_id": "n2", "node_type": "pain", "verbatim_quote": "annoying"},
    {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "Used to be free"}
  ],
  "edges": [
    {"src": "n1", "dst": "n2", "relation": "triggers"},
    {"src": "n1", "dst": "n3", "relation": "violates_expectation"}
  ]
}
```

Why this passes: exactly three nodes, each faithful. No attempt to invent
a `workaround` (there isn't one) or a `lost_value` (the loss is implicit
but not spelled out in recoverable text). Both edges are directly
supported.

### Example C — failure mode (DO NOT emit graphs like this)

Review (same as Example B):
> Paywall is annoying. Used to be free.

Broken output:

```json
{
  "nodes": [
    {"node_id": "n1", "node_type": "triggered_element", "verbatim_quote": "Paywall"},
    {"node_id": "n2", "node_type": "pain", "verbatim_quote": "annoying"},
    {"node_id": "n3", "node_type": "expectation", "verbatim_quote": "Used to be free"},
    {"node_id": "n4", "node_type": "workaround", "verbatim_quote": "annoying"},
    {"node_id": "n5", "node_type": "lost_value", "verbatim_quote": "free"}
  ],
  "edges": []
}
```

Why this fails — three separate rubric violations:

- `n4` reuses the `"annoying"` span (already `n2`'s quote) and mistypes it
  as a workaround. The reviewer did not describe a coping strategy; the
  model is padding to reach five nodes.
- `n5` quotes `"free"` as `lost_value`, but `"free"` is part of
  `"used to be free"` — a description of past state, not a lost benefit
  the review articulates. The faithful-anchoring axis fails.
- The graph has zero edges despite two clear causal relations (paywall
  triggering annoyance; paywall violating the free expectation).

The correct response when only three nodes can be faithfully anchored is
Example B — emit three, stop.

---

To grade a new graph with this rubric, walk axes 1–4 in order: schema
validity, closed-vocabulary compliance, faithful anchoring, typing accuracy.
Record any failed axis and the node(s) or edge(s) responsible. A grader
comment of the form _"axis 4: n2 should be lost_value, not pain"_ is
preferred over a free-form review — it keeps the pilot's rejection reasons
aggregable.
