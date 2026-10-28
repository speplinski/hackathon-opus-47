// Office Script AD_Brief — renders the design brief (markdown). Called
// from the flow with "Run script":
//   operation: "brief_render"
//   payload:   JSON string, shape BrPayload (see OPS_L8_BRIEF.md)
// Returns JSON.stringify(KernelResult) with result = { markdown }.

import { KernelResult } from "./types";
import { BrPayload, brRender } from "./brief";

export function main(workbook: ExcelScript.Workbook, operation: string, payload: string): string {
  const res: KernelResult = { ok: true, operation, result: null, errors: [] };
  try {
    if (operation !== "brief_render") throw new Error(`unknown operation: ${operation}`);
    const parsed: unknown = JSON.parse(payload);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("payload must be a JSON object");
    const p = parsed as { [k: string]: unknown };
    const required = ["cluster", "reconciled", "priority", "decision", "iterations", "model_short", "loop_verifier"];
    const missing = required.filter((k: string): boolean => p[k] === undefined || p[k] === null);
    if (missing.length) throw new Error(`payload is missing: ${missing.join(", ")}`);
    res.result = { markdown: brRender(parsed as BrPayload) };
  } catch (e) {
    res.ok = false;
    res.errors.push(String((e as Error).message));
  }
  return JSON.stringify(res);
}
