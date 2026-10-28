// L8, verify-on-product and brief: TypeScript kernel vs the Python pipeline.
// Fixtures: tests/fixtures/l8_brief.json.gz, built by fixtures_l8_brief.py
// from the study data (expected values come from the Python code itself).

import { describe, expect, test } from "bun:test";
import { readFileSync } from "fs";
import { gunzipSync } from "zlib";
import {
  l8ParseReaudit,
  l8ParseTweak,
  l8ParetoVerdict,
  l8TchebycheffVerdict,
} from "../src/l8";
import { main as petla } from "../src/entry_petla";
import { main as briefMain } from "../src/entry_brief";
import { OptimizationIteration } from "../src/types";

type J = { [k: string]: any };

const F: J = JSON.parse(
  gunzipSync(readFileSync(new URL("./fixtures/l8_brief.json.gz", import.meta.url))).toString("utf8")
);
const WB = {} as ExcelScript.Workbook;

function call(op: string, payload: J): J {
  const out = JSON.parse(petla(WB, op, JSON.stringify(payload)));
  if (!out.ok) throw new Error(`${op}: ${out.errors.join("; ")}`);
  return out.result;
}

function noTime(it: J): J {
  const { recorded_at, ...rest } = it;
  return rest;
}

const ART = "ART";

describe("thin spine (iteration 0 + 1, Pareto)", () => {
  for (const c of F.thin as J[]) {
    test(c.name, () => {
      const base = call("l8_baseline", {
        cluster: c.cluster, reconciled: c.reconciled, decision: c.decision, run_id: c.run_id, artifacts_dir: ART,
      });
      expect(base.reaudit_message).toBe(c.expected.reaudit_message);
      expect(base.baseline_heuristics).toEqual(c.expected.baseline_heuristics);
      expect(noTime(base.iteration)).toEqual(c.expected.baseline);
      expect(base.artifact_md).toBe(c.expected.artifacts[base.artifact_ref]);

      const prop = call("l8_proposed", {
        cluster: c.cluster, reconciled: c.reconciled, decision: c.decision, baseline_iteration: base.iteration,
        response_text: c.response_text, run_id: c.run_id, artifacts_dir: ART,
      });
      expect(noTime(prop.iteration)).toEqual(c.expected.proposed);
      expect(prop.status).toBe(c.expected.status);
      expect(prop.verdict).toEqual(c.expected.verdict);
      expect(prop.artifact_md).toBe(c.expected.artifacts[prop.artifact_ref]);
      if (c.expected.status === "optimized") expect(prop.native_payload).toEqual(c.expected.native_payload);
    });
  }
});

describe("multi-round loop", () => {
  for (const c of F.loop as J[]) {
    test(c.name, () => {
      const calls: J[] = c.expected.calls;
      let k = 0;
      const steps: J[] = [];
      const iterations: J[] = [];
      const verdicts: J[] = [];
      const natives: J[] = [];
      const artifacts: { [ref: string]: string } = {};
      let halt: string | null = null;
      let plan: J;
      for (;;) {
        plan = call("l8_loop_plan", {
          cluster: c.cluster, reconciled: c.reconciled, decision: c.decision,
          existing_iterations: c.existing_iterations, steps, halt_reason: halt,
        });
        if (plan.action === "stop") break;
        expect(calls[k].skill_id).toBe("design-tweak");
        expect(plan.tweak_message).toBe(calls[k].user);
        const tweakText = calls[k].response;
        k++;
        const tp = call("l8_tweak_parse", {
          response_text: tweakText, current_scores: plan.parent_scores,
          cluster: c.cluster, reconciled: c.reconciled, parent_snapshot: plan.parent_snapshot,
        });
        if (tp.status !== "ok") {
          halt = tp.termination_reason;
          continue;
        }
        expect(calls[k].skill_id).toBe("design-optimize");
        expect(tp.reaudit_message).toBe(calls[k].user);
        const reauditText = calls[k].response;
        k++;
        const allIts: J[] = c.existing_iterations.concat(iterations);
        const parent = allIts.find((it: J) => it.iteration_id === plan.parent_iteration_id);
        // The flow passes plan.parent_iteration as-is; it must be that iteration.
        expect(plan.parent_iteration).toEqual(parent);
        const step = call("l8_loop_step", {
          cluster: c.cluster, reconciled: c.reconciled, parent_iteration: plan.parent_iteration,
          parent_snapshot: plan.parent_snapshot, next_index: plan.next_index,
          tweak_response_text: tweakText, reaudit_response_text: reauditText,
          verifier: c.verifier, run_id: c.run_id, artifacts_dir: ART,
        });
        if (step.status !== "ok") {
          halt = step.termination_reason;
          continue;
        }
        iterations.push(step.iteration);
        verdicts.push(step.verdict);
        natives.push(step.native_payload);
        artifacts[step.artifact_ref] = step.artifact_md;
        steps.push({ iteration: step.iteration, new_snapshot: step.new_snapshot, converged: step.verdict.converged });
      }
      expect(k).toBe(calls.length);
      expect(plan.termination_reason).toBe(c.expected.termination_reason);
      expect(plan.final_parent_id).toBe(c.expected.final_parent_id);
      expect(iterations.map(noTime)).toEqual(c.expected.new_iterations);
      expect(verdicts).toEqual(c.expected.verdicts);
      expect(natives).toEqual(c.expected.native_payloads);
      expect(artifacts).toEqual(c.expected.artifacts);
    });
  }
});

describe("verifiers (Pareto, Tchebycheff)", () => {
  test(`${F.verifiers.length} parent/child pairs`, () => {
    for (const v of F.verifiers as J[]) {
      expect(l8ParetoVerdict(v.parent, v.child)).toEqual(v.pareto);
      const p2 = l8ParetoVerdict(v.parent, v.child, 2);
      expect({ accepted: p2.accepted, reason: p2.reason }).toEqual(v.pareto_max2);
      expect(l8TchebycheffVerdict(v.parent, v.child)).toEqual(v.tchebycheff);
      const t2 = l8TchebycheffVerdict(v.parent, v.child, 25.0);
      expect({ accepted: t2.accepted, reason: t2.reason }).toEqual(v.tchebycheff_25);
    }
  });
  test("mismatched keys are a caller error", () => {
    expect(() => l8ParetoVerdict({ a: 1 }, { b: 1 })).toThrow();
    const out = JSON.parse(petla(WB, "l8_verdict", JSON.stringify({ parent: { a: 1 }, child: { b: 1 }, verifier: "pareto" })));
    expect(out.ok).toBe(false);
    const bad = JSON.parse(petla(WB, "l8_verdict", JSON.stringify({ parent: { a: 1 }, child: { a: 1 }, verifier: "x" })));
    expect(bad.ok).toBe(false);
  });
});

// Python's json decoder messages cannot be reproduced; for those cases only
// the "malformed JSON" prefix is compared.
function sameError(mine: string | null, py: string | null): void {
  if (py === null) {
    expect(mine).toBeNull();
    return;
  }
  if (py.startsWith("malformed JSON")) expect((mine || "").startsWith("malformed JSON")).toBe(true);
  else expect(mine).toBe(py);
}

describe("parsers", () => {
  test("design-tweak", () => {
    for (const c of F.parsers.tweak as J[]) {
      const r = l8ParseTweak(c.text, { a: 7, b: 0, c: 3 });
      expect(r.ok).toBe(c.ok);
      sameError(r.error, c.error);
    }
  });
  test("design-optimize re-audit", () => {
    for (const c of F.parsers.reaudit as J[]) {
      const r = l8ParseReaudit(c.text, ["a", "b", "c"]);
      expect(r.ok).toBe(c.ok);
      sameError(r.error, c.error);
    }
  });
  test("unusable responses are recorded as fallback, not as errors", () => {
    const r = call("l8_reaudit_parse", { response_text: "no json", baseline_heuristics: ["a"] });
    expect(r.status).toBe("fallback");
    const t = call("l8_tweak_parse", { response_text: "{}", current_scores: { a: 3 } });
    expect(t.status).toBe("fallback");
    expect(t.termination_reason).toBe("tweak_parse_fail");
  });
  test("bad input is ok:false", () => {
    expect(JSON.parse(petla(WB, "nope", "{}")).ok).toBe(false);
    expect(JSON.parse(petla(WB, "l8_baseline", "{}")).ok).toBe(false);
    expect(JSON.parse(petla(WB, "l8_baseline", "not json")).ok).toBe(false);
  });
});

describe("verify on product", () => {
  for (const c of F.verify.cases as J[]) {
    test(c.name, () => {
      const m = call("vp_message", { reconciled: c.reconciled, screenshots: c.screenshots });
      expect(m.screenshot_captions.concat([m.envelope])).toEqual(c.expected.content_texts);
      const parsed = call("vp_parse", { response_text: JSON.stringify(c.payload), reconciled: c.reconciled });
      expect(parsed.status).toBe("ok");
      const r = call("vp_render", { reconciled: c.reconciled, payload: c.payload, model: c.model, screenshots: c.screenshots });
      expect(r.markdown).toBe(c.expected.md);
    });
  }
  test("payload validation", () => {
    for (const c of F.verify.parse as J[]) {
      const r = call("vp_parse", { response_text: c.text, baseline_slugs: F.verify.slugs });
      expect(r.status).toBe(c.ok ? "ok" : "fallback");
      if (!c.ok) sameError(r.error, c.error);
    }
  });
});

describe("design brief", () => {
  for (const c of F.brief as J[]) {
    test(c.name, () => {
      const out = JSON.parse(briefMain(WB, "brief_render", JSON.stringify(c.payload)));
      expect(out.ok).toBe(true);
      expect(out.result.markdown).toBe(c.expected);
    });
  }
  test("missing input is ok:false", () => {
    expect(JSON.parse(briefMain(WB, "brief_render", "{}")).ok).toBe(false);
  });
});

describe("formatting helpers", () => {
  test("Python round-half-even on exact binary values", async () => {
    const { l8PyFixed } = await import("../src/l8");
    expect(l8PyFixed(62.5, 0)).toBe("62");
    expect(l8PyFixed(63.5, 0)).toBe("64");
    expect(l8PyFixed(0.25, 1)).toBe("0.2");
    expect(l8PyFixed(0.35, 1)).toBe("0.3"); // 0.35 is 0.34999… in binary
    expect(l8PyFixed(72.9, 1)).toBe("72.9");
    expect(l8PyFixed(-12.25, 1, true)).toBe("-12.2");
    expect(l8PyFixed(10, 1, true)).toBe("+10.0");
    expect(l8PyFixed(99.96, 1)).toBe("100.0");
  });
});

void (null as unknown as OptimizationIteration);
