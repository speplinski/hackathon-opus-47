// Office Script entry point for L5–L7 (script name: AD_Uzgodnienie).
//
// Called from the flow with the Excel Online (Business) "Run script"
// action: operation = op name, payload = JSON string. Returns a JSON
// string of KernelResult. See OPS_L5_L7.md for every op.

import { KernelResult, AuditVerdict, InsightCluster, PriorityScore, ReconciledVerdict } from "./types";
import { l5BuildBundle, l5BuildUserMessage, l5NativeRow, l5Reconcile } from "./l5";
import { l6BuildUserMessage, l6Score, l7BuildUserMessage, l7Decide } from "./l6_l7";

type UzgPayload = { [k: string]: unknown };

function uzgRequire(p: UzgPayload, key: string): unknown {
  if (!(key in p) || p[key] === undefined) throw new Error(`payload is missing '${key}'`);
  return p[key];
}

function uzgDispatch(operation: string, p: UzgPayload, warnings: string[]): unknown {
  if (operation === "l5_message") {
    const cluster = uzgRequire(p, "cluster") as InsightCluster;
    const verdicts = uzgRequire(p, "verdicts") as AuditVerdict[];
    const bundle = l5BuildBundle(cluster.cluster_id, verdicts);
    if (Object.keys(bundle.verdicts_by_skill).length === 0) {
      // Python skips the model call for an empty bundle.
      return { skip: true, reason: "no L4 verdicts in input bundle", message: null };
    }
    return { skip: false, reason: null, message: l5BuildUserMessage(cluster, bundle) };
  }
  if (operation === "l5_reconcile") {
    const cluster = uzgRequire(p, "cluster") as InsightCluster;
    const verdicts = uzgRequire(p, "verdicts") as AuditVerdict[];
    const text = p["response_text"] === undefined || p["response_text"] === null ? null : String(p["response_text"]);
    const outcome = l5Reconcile(cluster, verdicts, text, warnings);
    return {
      status: outcome.status,
      reason: outcome.reason,
      verdict: outcome.verdict,
      native_row: l5NativeRow(outcome),
    };
  }
  if (operation === "l6_message") {
    return {
      message: l6BuildUserMessage(uzgRequire(p, "cluster") as InsightCluster, uzgRequire(p, "reconciled") as ReconciledVerdict),
    };
  }
  if (operation === "l6_score") {
    const clusterId = String(uzgRequire(p, "cluster_id"));
    const texts = (uzgRequire(p, "response_texts") as unknown[]).map((t: unknown): string => String(t));
    const weights = p["meta_weights"] === undefined || p["meta_weights"] === null ? null : (p["meta_weights"] as { [k: string]: number });
    const outcome = l6Score(clusterId, texts, weights);
    return {
      status: outcome.status,
      reason: outcome.reason,
      priority: outcome.priority,
      native_row: { cluster_id: outcome.cluster_id, passes: outcome.passes, reason: outcome.reason, status: outcome.status },
    };
  }
  if (operation === "l7_message") {
    return {
      message: l7BuildUserMessage(
        uzgRequire(p, "cluster") as InsightCluster,
        uzgRequire(p, "reconciled") as ReconciledVerdict,
        uzgRequire(p, "priority") as PriorityScore
      ),
    };
  }
  if (operation === "l7_decide") {
    const outcome = l7Decide(
      uzgRequire(p, "cluster") as InsightCluster,
      uzgRequire(p, "reconciled") as ReconciledVerdict,
      String(uzgRequire(p, "response_text"))
    );
    return {
      status: outcome.status,
      reason: outcome.reason,
      principle: outcome.principle,
      decision: outcome.decision,
      native_row: { cluster_id: outcome.cluster_id, payload: outcome.native_payload, reason: outcome.reason, status: outcome.status },
    };
  }
  throw new Error(`unknown operation '${operation}'`);
}

export function uzgodnienieRun(operation: string, payload: string): KernelResult {
  const warnings: string[] = [];
  try {
    const p = JSON.parse(payload) as UzgPayload;
    const result = uzgDispatch(operation, p, warnings);
    return { ok: true, operation, result, errors: warnings };
  } catch (e) {
    return { ok: false, operation, result: null, errors: warnings.concat([String((e as Error).message || e)]) };
  }
}

function main(workbook: ExcelScript.Workbook, operation: string, payload: string): string {
  return JSON.stringify(uzgodnienieRun(operation, payload));
}
