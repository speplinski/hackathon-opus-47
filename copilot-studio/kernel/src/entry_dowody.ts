// AD_Dowody — evidence lookup for the conversational agent's tool flow.

import type { InsightCluster, KernelResult, ReconciledVerdict } from "./types";
import { dwEvidence, dwSummary } from "./dowody";

function main(workbook: ExcelScript.Workbook, operation: string, payload: string): string {
  const out: KernelResult = { ok: true, operation, result: null, errors: [] };
  try {
    const p = JSON.parse(payload || "{}") as { [k: string]: unknown };
    if (operation !== "dowody") throw new Error(`unknown operation: ${operation}`);
    const entries = dwEvidence(
      p.cluster as InsightCluster,
      p.reconciled as ReconciledVerdict,
      typeof p.heuristic === "string" && p.heuristic ? (p.heuristic as string) : null,
      (p.utterance_texts as { [id: string]: string }) || null
    );
    out.result = { entries, summary: dwSummary(entries) };
  } catch (e) {
    out.ok = false;
    out.errors.push(String((e as Error).message || e));
  }
  return JSON.stringify(out);
}
