# Kernel ops: L8, verify-on-product, design brief

Two Office Scripts, built by `bun build.ts` from `src/`:

| Script | Built from | Python it replaces |
|---|---|---|
| `AD_Petla` | `l8.ts`, `verify.ts`, `entry_petla.ts` | `layers/l8_optimize.py`, `layers/l8_optimize_loop.py`, `evaluators/pareto.py`, `evaluators/tchebycheff.py`, `scripts/verify_on_product.py` |
| `AD_Brief` | `brief.ts`, `entry_brief.ts` | `scripts/export_design_brief.py` |

**Call shape.** Every call is `Run script` with:
- `operation`: the op name;
- `payload`: a JSON string.

The script returns `KernelResult` as a JSON string.

**`ok` vs `status`.**
- `ok: false` means the input is wrong: unknown op, a missing key, or invalid JSON in the payload. Stop the run.
- A model response that cannot be used is `ok: true` with `result.status: "fallback"`, the reason in `result.error`, and the raw text where useful. Write it to the run folder like any other result.

**Common object shapes.**
- `cluster`, `reconciled`, `decision`, `iteration` objects use the field names of `schemas.py`; see `types.ts`.
- `scores` maps heuristic slug → severity, e.g. `{"slug": 7}`. Iterations nest it under the synthetic skill `"reconciled"`, as in Python.

## Thin spine (iterations 0 and 1)

| Op | Payload | Result | Flow step |
|---|---|---|---|
| `l8_baseline` | `cluster, reconciled, decision, run_id, artifacts_dir?, recorded_at?` | `iteration` (iteration 0), `artifact_ref`, `artifact_md`, `baseline_heuristics`, `reaudit_message` | After L7. Write `artifact_md` to `artifact_ref`. Send `reaudit_message` to the **design-optimize** prompt. |
| `l8_proposed` | `cluster, reconciled, decision, baseline_iteration, response_text, run_id, artifacts_dir?, max_regression? (1), recorded_at?` | `iteration` (iteration 1), `artifact_ref`, `artifact_md`, `status` (`optimized` \| `fallback`), `reason`, `native_payload`, `verdict` (Pareto) | Right after the design-optimize prompt. A fallback copies the baseline scores, which the Pareto verdict rejects. Same as Python. |

`artifact_ref` is `artifacts_dir + "/" + <cluster_id>_iterNN.md`. Python's equivalent is `str(artifacts_dir / name)`.

## Loop (iterations 2+)

The flow repeats this cycle until `l8_loop_plan` returns `action: "stop"`:

1. Call `l8_loop_plan`.
2. Run the **design-tweak** prompt with `tweak_message`.
3. Call `l8_tweak_parse`.
4. Run the **design-optimize** prompt with `reaudit_message`.
5. Call `l8_loop_step`.
6. Append `{iteration, new_snapshot, converged: verdict.converged}` to `steps`.

| Op | Payload | Result |
|---|---|---|
| `l8_loop_plan` | `cluster, reconciled, decision, existing_iterations` (iterations 0 and 1), `steps` (`[{iteration, new_snapshot, converged}]`, this run so far), `halt_reason?`, `max_iterations?` (5), `stall_limit?` (2), `severity_threshold?` (5), `snapshots?` (`{iteration_id: snapshot}`), `artifact_bodies?` (`{design_artifact_ref: markdown}`, only when resuming from iterations ≥ 2) | `action` (`tweak` \| `stop`), `termination_reason` (`max_iterations` \| `stall` \| `converged` \| `severity_threshold` \| `tweak_parse_fail` \| `reaudit_parse_fail`), `next_index`, `parent_iteration_id`, `parent_iteration` (the object to pass to `l8_loop_step`), `parent_snapshot`, `parent_scores`, `tweak_message`, `final_parent_id` |
| `l8_tweak_parse` | `response_text, current_scores` (= `parent_scores`), and `cluster, reconciled, parent_snapshot` to also get the re-audit message | `status`, `payload`, `error`, `termination_reason` (`tweak_parse_fail` on fallback), `new_snapshot`, `reaudit_message` |
| `l8_loop_step` | `cluster, reconciled, parent_iteration, parent_snapshot, next_index, tweak_response_text, reaudit_response_text, verifier` (`tchebycheff` \| `pareto`), `run_id, artifacts_dir?, max_regression?` (1), `min_improvement_pct?` (10.0), `recorded_at?` | `status`, `ok`, `termination_reason` (on fallback), `error`, `iteration`, `new_snapshot`, `verdict` (LoopVerdict), `verdict_raw` (Pareto or Tchebycheff fields), `artifact_ref`, `artifact_md`, `native_payload` |
| `l8_verdict` | `parent, child, verifier, max_regression?, min_improvement_pct?` | `verdict`, `raw`. Stand-alone gate, e.g. to re-check a stored pair. |
| `l8_reaudit_message` | `cluster, reconciled` + (`before_snapshot, after_snapshot` or `decision`) | `message` |
| `l8_reaudit_parse` | `response_text` + (`baseline_heuristics` or `reconciled`) | `status`, `payload`, `error` |

**When a parse fails.** If `l8_tweak_parse` or `l8_loop_step` returns `status: "fallback"`:
1. Pass its `termination_reason` as `halt_reason` in one more `l8_loop_plan` call.
2. That call returns `stop` with the reason and the `final_parent_id`.

This matches Python, which stops the loop on a parse failure and does not write an iteration.

## Verify on product

The Python script sends a single message. It alternates `[Screenshot: <label>]` text blocks with base64 PNG images, then appends the XML envelope.

Copilot Studio prompts take images as separate **Image or document** inputs. So:
- give the prompt one image input per screenshot, in the same order as `screenshots`;
- give it one text input containing `prompt_text`, which is the caption lines (one per line), a blank line, then the envelope;
- the system prompt is the `SYSTEM_PROMPT` constant in `scripts/verify_on_product.py`. It is not part of the kernel.

The envelope is byte-identical to Python. Only the interleaving changes: captions come first, images are attached as inputs.

| Op | Payload | Result | Flow step |
|---|---|---|---|
| `vp_message` | `reconciled, screenshots` (`[{label, path?}]`) | `screenshot_captions`, `envelope`, `prompt_text` | Before the verify prompt |
| `vp_parse` | `response_text` + (`reconciled` or `baseline_slugs`) | `status`, `payload`, `error`, `raw_response` (on fallback) | After the verify prompt. Write `payload` as `verify_on_product_<cluster>_<model>.json`. |
| `vp_render` | `reconciled, payload, model, screenshots` (`[{label, path}]`) | `markdown` (the `.md` artifact), `summary` (`baseline_severity_sum`, `adjusted_severity_sum`, `verdict_counts`, `baseline_heuristic_count`) | Optional human-readable artifact |

## Design brief

| Op | Payload | Result |
|---|---|---|
| `brief_render` | `cluster, reconciled, priority, decision, iterations` (thin spine + loop, filtered to the cluster by id prefix), `verify_payload?` (null → no grounding sections), `model_short, loop_verifier, generated_at?` (Python format `2026-04-24T18:11:48+00:00`; default: now), `inputs?` (`[{label, path, sha256?}]` for the provenance table), `artifact_bodies?` (`{design_artifact_ref: markdown}`, needed when the final accepted iteration is ≥ 2; without it the brief falls back to `decision.after_snapshot`, as Python does when the file is missing) | `markdown` |

**Inputs to the brief.**
- Use the backfilled `cluster` (with `representative_review_ids`) and `reconciled` (with `evidence_review_ids`). Then the "Evidence per finding" section lists reviews and quotes.
- Without them, the brief says per-finding ids are unavailable.

## Tests

Regenerate the fixture from the repo root:
```
uv run python copilot-studio/kernel/tests/fixtures_l8_brief.py
```
The output, `tests/fixtures/l8_brief.json.gz`, is 117 KB gzipped (~900 KB JSON).

Run the tests:
```
cd copilot-studio/kernel && bun test tests/l8_brief.test.ts
```

Every expected value is produced by the Python code on study inputs:
- **Thin spine:** 4 cases, opus47/opus46/sonnet46 on `cluster_11` plus opus47 on `cluster_00`. Stored model responses are replayed through `optimize_cluster`. A sanity check confirms the replay reproduces the stored study iterations.
- **Loop:** 9 runs, `cluster_02` × 3 models × {tchebycheff, pareto} plus `cluster_11` × 3 models. They are replayed through `run_loop` and reproduce the study's termination reasons: `converged`, `stall` (3 iterations), `severity_threshold`, and immediate `converged`.
- **Verifiers:** all real parent/child pairs plus 10 synthetic edge cases. Checks cover Pareto at max_regression 1 and 2, and Tchebycheff at 10 % and 25 %, including the reason strings.
- **Parsers:** design-tweak and re-audit rejection messages (Python wording), plus the verify payload validation.
- **Verify:** the envelope and caption texts, and the `.md` artifacts byte-equal to the study files for 3 models.
- **Brief:** 6 briefs byte-equal to `_build_brief`:
  - backfilled `cluster_11` × 3 models;
  - original `cluster_11` without evidence ids and without verify;
  - `cluster_02` with the final snapshot read from an iteration-2 artifact;
  - `cluster_02` sonnet46/pareto, with rejected iterations and a missing artifact.

## Python behaviour not ported

| What | Why / what to do instead |
|---|---|
| File I/O: writing artifacts, `.jsonl`, provenance sidecars, reading artifact files back | The flow writes `artifact_md` to `artifact_ref` and passes artifact bodies back via `artifact_bodies` when needed. |
| sha256 of input files (brief provenance, verify provenance) | No crypto in Office Scripts. Pass `sha256` in `inputs` if the flow has it; otherwise the table shows `missing`. |
| `recorded_at` microseconds | Default is `new Date().toISOString()` (millisecond precision). Pass `recorded_at` to override. |
| Provenance aggregates of `l8_optimize.build_provenance` / `l8_optimize_loop.build_provenance` | Not ported. The flow can count accepted/rejected iterations from the results. |
| JSON decoder error messages | `malformed JSON: …` uses the JavaScript parser's message, not Python's. Other rejection messages match Python's wording. |
| `isinstance(x, int)` on `7.0` | JSON `7.0` parses to the integer 7 in JavaScript, so it is accepted where Python rejects a float. |
| `str.split()` whitespace set (snapshot word count) | Uses the JavaScript `\s` set. The two differ only on exotic control characters. |
| Sorting of slugs | JavaScript sorts by UTF-16 code unit, Python by code point. Identical for ASCII slugs. |
| Verify interleaving of images and captions | See above. Captions are listed in order instead of interleaved. |
