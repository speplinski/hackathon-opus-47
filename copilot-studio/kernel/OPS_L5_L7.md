# AD_Uzgodnienie — operations for L5, L6, L7

Office Script `AD_Uzgodnienie` (built from `src/l5.ts`, `src/l6_l7.ts`, `src/entry_uzgodnienie.ts`).

The flow calls it with the Excel Online (Business) **Run script** action:
- `operation`: the op name;
- `payload`: a JSON string.

It returns a JSON string:

```json
{ "ok": true, "operation": "...", "result": { ... }, "errors": [] }
```

- When `ok` is `true`, `errors` holds only warnings, e.g. an L5 tension axis outside the closed set.
- When `ok` is `false`, the input itself was wrong (missing key, unknown skill id, unknown op). This is not a model failure: a model failure comes back as `ok: true` with `status: "fallback"`.

Types (`InsightCluster`, `AuditVerdict`, `ReconciledVerdict`, `PriorityScore`, `DesignDecision`) are the JSON shapes of `src/auditable_design/schemas.py` (see `src/types.ts`).

## Flow sequence per cluster

1. **L5**
   1. `l5_message`. If `skip`, write the fallback from `l5_reconcile` with `response_text: null` and go to L6.
   2. Run the prompt **sot-reconcile** with `message`.
   3. `l5_reconcile` with the raw response text.
   4. Write `verdict` to `L5_uzgodnienie.json` and `native_row` to the native file.
2. **L6**
   1. `l6_message`.
   2. Run the prompt **priority-weight** twice with the same message.
   3. `l6_score` with both responses.
   4. If `status = "needs_third_pass"`, run the prompt a third time and call `l6_score` again with all three responses.
   5. Write `priority` and `native_row`.
3. **L7**
   1. `l7_message` with the L6 `priority`.
   2. Run the prompt **design-decide** once.
   3. `l7_decide`.
   4. Write `principle`, `decision` and `native_row`. On a fallback, write only `native_row`.

## Operations

| op | payload | result |
|---|---|---|
| `l5_message` | `{cluster, verdicts: AuditVerdict[]}` (verdicts of this cluster; other clusters are ignored) | `{skip, reason, message}` — `skip: true` when the bundle is empty (Python makes no model call then) |
| `l5_reconcile` | `{cluster, verdicts, response_text}` (`response_text` may be `null` for the empty-bundle case) | `{status: "audited"\|"fallback", reason, verdict: ReconciledVerdict, native_row: {cluster_id, status, payload}}` |
| `l6_message` | `{cluster, reconciled: ReconciledVerdict}` | `{message}` |
| `l6_score` | `{cluster_id, response_texts: string[2 or 3], meta_weights?}` | `{status: "scored"\|"fallback"\|"needs_third_pass", reason, priority: PriorityScore\|null, native_row: {cluster_id, passes, reason, status}}` |
| `l7_message` | `{cluster, reconciled, priority: PriorityScore}` | `{message}` |
| `l7_decide` | `{cluster, reconciled, response_text}` | `{status: "decided"\|"fallback", reason, principle, decision, native_row: {cluster_id, payload, reason, status}}` |

## What is ported exactly

**Prompts.** `build_user_message` for L5, L6 and L7 is byte-identical to Python on the study data (golden tests).

**L5.** These are ported exactly:
- `_derive_flat_lists_from_graph`: corroboration collapse, heuristic-slug rule, solitary entries, `corroboration_count`, `unique_frames`, `rank_score`, stable sort, tensions, gaps;
- `source_findings`;
- `_build_reconciled_verdict`: reasoning string;
- `evidence_review_ids_for_entry`: union of L4 review ids in first-seen order;
- the empty-bundle and parse-failure fallbacks with the same native shapes.

**L5 validation.** All rules of `parse_reconcile_response` are ported:
- top-level keys (legacy `ranked_violations`/`tensions`/`gaps` ignored);
- `summary`;
- graph keys;
- all 17 node keys;
- node types;
- `confidence` in [0, 1];
- violation nodes cross-checked against the L4 bundle (skill, finding index, heuristic, severity);
- corroboration with at least 2 members that are violation ids;
- contradiction and tension skill pairs present in the bundle;
- unknown tension axis: a warning, not a rejection (as in Python);
- the gap evidence rules (closed sources, no duplicates, the bidirectional quotes rule, idx range, `why_missed`);
- edge keys, endpoints and relation types;
- duplicate node ids.

**L6.**
- `parse_priority_response`: exact key sets, ints in [0, 10], non-empty rationales and `overall_note`.
- Two-pass logic with a third pass when any dimension drifts by more than 1.
- Aggregation: mean with Python's banker's rounding for 2 passes, median for 3.
- `validation_delta` and `weighted_total`.
- Fallbacks: one parsed pass, or none.

**L7.**
- `parse_decide_response`: exact key sets.
- `derived_from_review_ids` must be a subset of `member_review_ids`; otherwise the reason is "hallucinated citation".
- `resolves_heuristics` must be a subset of the ranked heuristics; otherwise the reason is "invented slug".
- Ids: `principle__<cluster>` and `decision__<cluster>__1`.

## Differences from Python and what is not ported

**Integers.**
- JSON `3.0` parses as an integer in JavaScript, so `source_finding_idx`, severities and L6 dimensions written as `3.0` are accepted. Python rejects them.
- JSON `true` is still rejected where a number is required.

**L5 JSON repair.**
- L5 uses `parseModelJson` from `common.ts`, which repairs stray quotes only when the runtime reports an error position.
- V8 reports it; Bun/JavaScriptCore does not, so the repair path is untested here.
- L6 and L7 have no repair pass, as in Python.

**Error messages.** Same meaning, but not byte-identical: Python's `{text!r}` repr of the whole response is not reproduced. The raw response is kept in `native_row.payload.raw_response` instead.

**`weighted_total`.** Summed in a fixed dimension order. Python iterates a `frozenset`, whose order depends on the hash seed, so the last bit of the float can differ. Tests compare to 1e-9.

**L7 `:.2f` of `weighted_total`.**
- `toFixed(2)` is used. It differs from Python only on exact binary ties such as x.125, which sums of 0.2 × int do not produce.
- The fixtures match byte-for-byte.

**Key order.** Dimension and meta-weight keys are emitted sorted alphabetically, as Python persists `PriorityScore` (`sort_keys=True`). The brief's priority table reads them in that order; the end-to-end simulation (`tests/symulacja.test.ts`) caught the difference.

**Not ported (flow or provenance concerns):**
- `load_*` file readers;
- the duplicate-verdict log warning (the later row still wins);
- the L6 "no reconciled verdict in input" fallback (the flow decides whether to call L6);
- `_validate_ranked` / `_auto_repair_ranked`, which are dead code in Python because the parser derives ranked entries itself;
- provenance sidecars, skill hashes and timestamps.

## Tests

```bash
uv run python copilot-studio/kernel/tests/fixtures_l5_l7.py   # from repo root; needs study data
cd copilot-studio/kernel && bun test tests/l5_l7.test.ts && bun build.ts
```

**Fixtures** (`tests/fixtures/l5_l7.json`, about 380 KB): `cluster_11` for Opus 4.7, Opus 4.6 and Sonnet 4.6, from:
- backfilled L5 inputs in `data/derived_evchain`;
- the L6 and L7 native sidecars.

The fixture script asserts that Python's own recomputation equals the stored study files before writing.

**41 tests**:
- byte-identical messages;
- deep-equal derived lists, `ReconciledVerdict`, `PriorityScore`, principle and decision;
- synthetic L6 aggregation (banker's rounding, 3-pass median);
- third-pass handling;
- 9 L5 rejection or fallback cases, 3 L6 malformed-pass cases and 2 L7 cross-reference cases;
- entry-point dispatch.
