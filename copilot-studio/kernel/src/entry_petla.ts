// Office Script AD_Petla — L8 optimisation (thin spine + loop) and
// verify-on-product. Called from the flow with "Run script":
//   operation: one of the op names below
//   payload:   JSON string (shape per op, see OPS_L8_BRIEF.md)
// Returns JSON.stringify(KernelResult). ok:false only for bad input; a
// model response that cannot be used is ok:true with result.status
// "fallback" and the reason.

import {
  InsightCluster,
  ReconciledVerdict,
  DesignDecision,
  OptimizationIteration,
  KernelResult,
} from "./types";
import {
  L8Scores,
  L8LoopStepRecord,
  l8Baseline,
  l8Proposed,
  l8ReauditMessage,
  l8ParseReaudit,
  l8ParseTweak,
  l8LoopPlan,
  l8LoopStep,
  l8ApplyVerifier,
  l8HeuristicList,
  L8_DEFAULT_MAX_REGRESSION,
  L8_DEFAULT_MIN_IMPROVEMENT_PCT,
} from "./l8";
import { VpScreenshot, vpMessage, vpParse, vpRenderMarkdown, vpSummary } from "./verify";

type PetlaPayload = { [k: string]: unknown };


function petlaNeed(p: PetlaPayload, keys: string[]): void {
  const missing = keys.filter((k: string): boolean => p[k] === undefined || p[k] === null);
  if (missing.length) throw new Error(`payload is missing: ${missing.join(", ")}`);
}

function petlaNum(p: PetlaPayload, key: string, dflt: number): number {
  const v = p[key];
  if (v === undefined || v === null) return dflt;
  if (typeof v !== "number") throw new Error(`${key} must be a number`);
  return v;
}

function petlaStr(p: PetlaPayload, key: string, dflt: string): string {
  const v = p[key];
  if (v === undefined || v === null) return dflt;
  return String(v);
}

function petlaOptStr(p: PetlaPayload, key: string): string | undefined {
  const v = p[key];
  return v === undefined || v === null ? undefined : String(v);
}

function petlaDispatch(operation: string, p: PetlaPayload): unknown {
  switch (operation) {
    case "l8_baseline": {
      petlaNeed(p, ["cluster", "reconciled", "decision", "run_id"]);
      const cluster = p.cluster as InsightCluster;
      const reconciled = p.reconciled as ReconciledVerdict;
      const decision = p.decision as DesignDecision;
      const out = l8Baseline(cluster, reconciled, decision, String(p.run_id), petlaStr(p, "artifacts_dir", ""), petlaOptStr(p, "recorded_at"));
      return {
        ...out,
        baseline_heuristics: l8HeuristicList(reconciled),
        reaudit_message: l8ReauditMessage(cluster, reconciled, decision.before_snapshot, decision.after_snapshot),
      };
    }
    case "l8_reaudit_message": {
      petlaNeed(p, ["cluster", "reconciled"]);
      let before = petlaOptStr(p, "before_snapshot");
      let after = petlaOptStr(p, "after_snapshot");
      if ((before === undefined || after === undefined) && p.decision) {
        const d = p.decision as DesignDecision;
        before = d.before_snapshot;
        after = d.after_snapshot;
      }
      if (before === undefined || after === undefined) {
        throw new Error("payload needs before_snapshot + after_snapshot, or decision");
      }
      return { message: l8ReauditMessage(p.cluster as InsightCluster, p.reconciled as ReconciledVerdict, before, after) };
    }
    case "l8_reaudit_parse": {
      petlaNeed(p, ["response_text"]);
      const hs = p.baseline_heuristics
        ? (p.baseline_heuristics as string[])
        : p.reconciled
        ? l8HeuristicList(p.reconciled as ReconciledVerdict)
        : null;
      if (hs === null) throw new Error("payload needs baseline_heuristics or reconciled");
      const r = l8ParseReaudit(String(p.response_text), hs);
      return { status: r.ok ? "ok" : "fallback", payload: r.payload, error: r.error };
    }
    case "l8_proposed": {
      petlaNeed(p, ["cluster", "reconciled", "decision", "baseline_iteration", "response_text", "run_id"]);
      return l8Proposed(
        p.cluster as InsightCluster,
        p.reconciled as ReconciledVerdict,
        p.decision as DesignDecision,
        p.baseline_iteration as OptimizationIteration,
        String(p.response_text),
        String(p.run_id),
        petlaStr(p, "artifacts_dir", ""),
        petlaNum(p, "max_regression", L8_DEFAULT_MAX_REGRESSION),
        petlaOptStr(p, "recorded_at")
      );
    }
    case "l8_loop_plan": {
      petlaNeed(p, ["cluster", "reconciled", "decision", "existing_iterations"]);
      return l8LoopPlan(
        p.cluster as InsightCluster,
        p.reconciled as ReconciledVerdict,
        p.decision as DesignDecision,
        p.existing_iterations as OptimizationIteration[],
        (p.steps as L8LoopStepRecord[]) || [],
        {
          max_iterations: p.max_iterations === undefined ? undefined : petlaNum(p, "max_iterations", 5),
          stall_limit: p.stall_limit === undefined ? undefined : petlaNum(p, "stall_limit", 2),
          severity_threshold: p.severity_threshold === undefined ? undefined : petlaNum(p, "severity_threshold", 5),
          halt_reason: petlaOptStr(p, "halt_reason") || null,
          snapshots: (p.snapshots as { [k: string]: string }) || {},
          artifact_bodies: (p.artifact_bodies as { [k: string]: string }) || {},
        }
      );
    }
    case "l8_tweak_parse": {
      petlaNeed(p, ["response_text", "current_scores"]);
      const r = l8ParseTweak(String(p.response_text), p.current_scores as L8Scores);
      const newSnapshot = r.ok && r.payload ? String(r.payload["new_snapshot"]) : null;
      let reaudit: string | null = null;
      if (newSnapshot !== null && p.cluster && p.reconciled && typeof p.parent_snapshot === "string") {
        reaudit = l8ReauditMessage(p.cluster as InsightCluster, p.reconciled as ReconciledVerdict, p.parent_snapshot, newSnapshot);
      }
      return {
        status: r.ok ? "ok" : "fallback",
        payload: r.payload,
        error: r.error,
        termination_reason: r.ok ? null : "tweak_parse_fail",
        new_snapshot: newSnapshot,
        reaudit_message: reaudit,
      };
    }
    case "l8_loop_step": {
      petlaNeed(p, [
        "cluster", "reconciled", "parent_iteration", "parent_snapshot", "next_index",
        "tweak_response_text", "reaudit_response_text", "verifier", "run_id",
      ]);
      const out = l8LoopStep(
        p.cluster as InsightCluster,
        p.reconciled as ReconciledVerdict,
        p.parent_iteration as OptimizationIteration,
        String(p.parent_snapshot),
        petlaNum(p, "next_index", 2),
        String(p.tweak_response_text),
        String(p.reaudit_response_text),
        String(p.verifier),
        String(p.run_id),
        petlaStr(p, "artifacts_dir", ""),
        petlaNum(p, "max_regression", L8_DEFAULT_MAX_REGRESSION),
        petlaNum(p, "min_improvement_pct", L8_DEFAULT_MIN_IMPROVEMENT_PCT),
        petlaOptStr(p, "recorded_at")
      );
      return { status: out.ok ? "ok" : "fallback", ...out };
    }
    case "l8_verdict": {
      petlaNeed(p, ["parent", "child", "verifier"]);
      return l8ApplyVerifier(
        p.parent as L8Scores,
        p.child as L8Scores,
        String(p.verifier),
        petlaNum(p, "max_regression", L8_DEFAULT_MAX_REGRESSION),
        petlaNum(p, "min_improvement_pct", L8_DEFAULT_MIN_IMPROVEMENT_PCT)
      );
    }
    case "vp_message": {
      petlaNeed(p, ["reconciled", "screenshots"]);
      return vpMessage(p.reconciled as ReconciledVerdict, p.screenshots as VpScreenshot[]);
    }
    case "vp_parse": {
      petlaNeed(p, ["response_text"]);
      const slugs = p.baseline_slugs
        ? (p.baseline_slugs as string[])
        : p.reconciled
        ? l8HeuristicList(p.reconciled as ReconciledVerdict)
        : null;
      if (slugs === null) throw new Error("payload needs baseline_slugs or reconciled");
      const r = vpParse(String(p.response_text), slugs);
      return {
        status: r.ok ? "ok" : "fallback",
        payload: r.payload,
        error: r.error,
        raw_response: r.ok ? null : String(p.response_text),
      };
    }
    case "vp_render": {
      petlaNeed(p, ["reconciled", "payload", "model", "screenshots"]);
      const reconciled = p.reconciled as ReconciledVerdict;
      const vp = p.payload as { [k: string]: unknown };
      return {
        markdown: vpRenderMarkdown(reconciled, vp, String(p.model), p.screenshots as VpScreenshot[]),
        summary: vpSummary(reconciled, vp),
      };
    }
    default:
      throw new Error(`unknown operation: ${operation}`);
  }
}

export function main(workbook: ExcelScript.Workbook, operation: string, payload: string): string {
  const res: KernelResult = { ok: true, operation, result: null, errors: [] };
  let p: PetlaPayload;
  try {
    const parsed: unknown = JSON.parse(payload);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("payload must be a JSON object");
    p = parsed as PetlaPayload;
  } catch (e) {
    res.ok = false;
    res.errors.push(`invalid payload JSON: ${String((e as Error).message)}`);
    return JSON.stringify(res);
  }
  try {
    res.result = petlaDispatch(operation, p);
  } catch (e) {
    res.ok = false;
    res.errors.push(String((e as Error).message));
  }
  return JSON.stringify(res);
}
