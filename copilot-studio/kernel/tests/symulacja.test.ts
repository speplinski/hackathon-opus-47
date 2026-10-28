// End-to-end simulation of the AD_Przebieg flow for cluster_11 with the
// study's recorded Opus 4.6 outputs standing in for the model. Calls the
// bundled scripts from dist/ in flow order and checks each step against
// the study's (backfilled) results.
import { expect, test } from "bun:test";
import { readFileSync } from "fs";
import { gunzipSync } from "zlib";
import { call } from "./dist_loader";

const fx = JSON.parse(gunzipSync(readFileSync(`${import.meta.dir}/fixtures/symulacja.json.gz`)).toString("utf8"));
const state: { [k: string]: unknown } = {};

test("L4: six lenses through AD_Audyt", async () => {
  const verdicts: unknown[] = [];
  for (const l of fx.l4) {
    const msg = await call("AD_Audyt", "l4_message", { lens: l.lens, cluster: fx.l4_cluster });
    expect(typeof msg.message).toBe("string");
    const r = await call("AD_Audyt", "l4_verdict", {
      lens: l.lens, cluster: fx.l4_cluster, response_text: l.response_text,
      model: l.claude_model, produced_at: l.produced_at, skill_hash: l.skill_hash, native_payload_ref: l.native_payload_ref,
    });
    expect(r.status).toBe(l.status);
    expect((r.verdict as { relevant_heuristics: unknown }).relevant_heuristics).toEqual(l.expected_heuristics);
    verdicts.push(r.verdict);
  }
  state.verdicts = verdicts;
});

test("L5: reconcile through AD_Uzgodnienie, review ids reach the ranking", async () => {
  const m = await call("AD_Uzgodnienie", "l5_message", { cluster: fx.l5_cluster, verdicts: state.verdicts });
  expect(m.skip).toBe(false);
  const r = await call("AD_Uzgodnienie", "l5_reconcile", { cluster: fx.l5_cluster, verdicts: state.verdicts, response_text: fx.l5.response_text });
  expect(r.status).toBe("audited");
  expect(r.verdict).toEqual(fx.l5.expected);
  state.reconciled = r.verdict;
});

test("L6: two passes → priority", async () => {
  await call("AD_Uzgodnienie", "l6_message", { cluster: fx.l5_cluster, reconciled: state.reconciled });
  const r = await call("AD_Uzgodnienie", "l6_score", { cluster_id: fx.l5_cluster.cluster_id, response_texts: fx.l6.response_texts });
  expect(r.status).toBe("scored");
  const p = r.priority as { dimensions: unknown; weighted_total: number; validation_delta: number };
  expect(p.dimensions).toEqual(fx.l6.expected.dimensions);
  expect(Math.abs(p.weighted_total - fx.l6.expected.weighted_total)).toBeLessThan(1e-9);
  expect(p.validation_delta).toBe(fx.l6.expected.validation_delta);
  state.priority = r.priority;
});

test("L7: decision", async () => {
  await call("AD_Uzgodnienie", "l7_message", { cluster: fx.l5_cluster, reconciled: state.reconciled, priority: state.priority });
  const r = await call("AD_Uzgodnienie", "l7_decide", { cluster: fx.l5_cluster, reconciled: state.reconciled, response_text: fx.l7.response_text });
  expect(r.status).toBe("decided");
  const expDecision = fx.l7.expected_decisions_file.find((d: { decision_id: string }) => d.decision_id === (r.decision as { decision_id: string }).decision_id);
  expect(r.decision).toEqual(expDecision);
  const expPrinciple = fx.l7.expected_principles_file.find((d: { cluster_id: string }) => d.cluster_id === fx.l5_cluster.cluster_id);
  expect(r.principle).toEqual(expPrinciple);
  state.decision = r.decision;
});

test("Agent tool: AD_Dowody on the simulated run", async () => {
  const r = await call("AD_Dowody", "dowody", { cluster: fx.l5_cluster, reconciled: state.reconciled, heuristic: null });
  const entries = r.entries as { basis: string }[];
  expect(entries.length).toBe((state.reconciled as { ranked_violations: unknown[] }).ranked_violations.length);
  expect(String(r.summary).length).toBeGreaterThan(0);
});

test("L8: thin spine and loop through AD_Petla", async () => {
  const L = fx.l8;
  const base = await call("AD_Petla", "l8_baseline", {
    cluster: fx.l5_cluster, reconciled: state.reconciled, decision: state.decision,
    run_id: L.run_id, artifacts_dir: L.artifacts_dir, recorded_at: L.expected_thin[0].recorded_at,
  });
  const prop = await call("AD_Petla", "l8_proposed", {
    cluster: fx.l5_cluster, reconciled: state.reconciled, decision: state.decision,
    baseline_iteration: base.iteration, response_text: L.thin_response_text,
    run_id: L.run_id, artifacts_dir: L.artifacts_dir, recorded_at: L.expected_thin[1].recorded_at,
  });
  expect(base.iteration).toEqual(L.expected_thin[0]);
  expect(prop.iteration).toEqual(L.expected_thin[1]);

  const steps: { iteration: unknown; new_snapshot: string; converged: boolean }[] = [];
  const loopIters: unknown[] = [];
  for (let guard = 0; guard < 10; guard++) {
    const plan = await call("AD_Petla", "l8_loop_plan", {
      cluster: fx.l5_cluster, reconciled: state.reconciled, decision: state.decision,
      existing_iterations: [base.iteration, prop.iteration], steps,
    });
    if (plan.action === "stop") {
      state.l8_stop = plan.termination_reason;
      break;
    }
    const k = steps.length;
    const tw = await call("AD_Petla", "l8_tweak_parse", {
      response_text: L.loop_tweaks[k], current_scores: plan.parent_scores,
      cluster: fx.l5_cluster, reconciled: state.reconciled, parent_snapshot: plan.parent_snapshot,
    });
    const step = await call("AD_Petla", "l8_loop_step", {
      cluster: fx.l5_cluster, reconciled: state.reconciled, parent_snapshot: plan.parent_snapshot,
      parent_iteration: plan.parent_iteration, next_index: plan.next_index,
      tweak_response_text: L.loop_tweaks[k], reaudit_response_text: L.loop_reaudits[k],
      verifier: "tchebycheff", run_id: L.loop_run_id, artifacts_dir: L.artifacts_dir,
      recorded_at: (L.expected_loop[k] || {}).recorded_at,
    });
    expect(tw.status).not.toBe("fallback");
    steps.push({ iteration: step.iteration, new_snapshot: String(step.new_snapshot), converged: Boolean((step.verdict as { converged?: boolean }).converged) });
    loopIters.push(step.iteration);
  }
  expect(loopIters).toEqual(L.expected_loop);
  expect(state.l8_stop).toBeDefined();
  state.iterations = [base.iteration, prop.iteration, ...loopIters];
});

test("Verify on product through AD_Petla", async () => {
  const m = await call("AD_Petla", "vp_message", { reconciled: state.reconciled, screenshots: fx.verify.screenshots });
  expect(String(m.prompt_text)).toContain("[Screenshot: ");
  const r = await call("AD_Petla", "vp_parse", { response_text: fx.verify.response_text, reconciled: state.reconciled });
  expect(r.status).not.toBe("fallback");
  expect(r.payload).toEqual(fx.verify.expected_payload);
  state.verify = r.payload;
});

test("Brief through AD_Brief equals the study brief (except timestamp and input table)", async () => {
  const r = await call("AD_Brief", "brief_render", {
    cluster: fx.l5_cluster, reconciled: state.reconciled, priority: state.priority, decision: state.decision,
    iterations: state.iterations, verify_payload: state.verify, model_short: "opus46", loop_verifier: "tchebycheff",
  });
  const norm = (md: string) =>
    md.split("\n").filter((l) => !l.startsWith("**Generated:**") && !/^\| .*`[^`]*\.(jsonl?|md)`/.test(l)).join("\n");
  expect(norm(String(r.markdown))).toBe(norm(fx.brief.expected_markdown));
});
