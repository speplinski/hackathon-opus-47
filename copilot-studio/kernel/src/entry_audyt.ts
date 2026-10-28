// AD_Audyt — Office Script entry for layer L4 (six audit lenses).
// Called from the flow with the "Run script" action of Excel Online (Business):
//   operation = "l4_message" | "l4_verdict" | "l4_lenses"
//   payload   = JSON string (see OPS_L4.md)
// Returns JSON.stringify(KernelResult).

import { KernelResult, InsightCluster } from "./types";
import { l4BuildMessage, l4MakeVerdict, l4LensInfo, L4VerdictInput } from "./l4";

function main(workbook: ExcelScript.Workbook, operation: string, payload: string): string {
  const res: KernelResult = { ok: true, operation: operation, result: null, errors: [] };
  try {
    const p = (payload ? JSON.parse(payload) : {}) as { [k: string]: unknown };
    if (operation === "l4_message") {
      res.result = { message: l4BuildMessage(p["lens"] as string, p["cluster"] as InsightCluster) };
    } else if (operation === "l4_verdict") {
      const out = l4MakeVerdict(p as unknown as L4VerdictInput);
      res.result = out;
      // A model response that cannot be parsed is a recorded result
      // (status "fallback"), not an error: ok stays true, as in every
      // other kernel script. ok:false means the input itself was wrong.
    } else if (operation === "l4_lenses") {
      res.result = l4LensInfo();
    } else {
      res.ok = false;
      res.errors.push(`unknown operation ${JSON.stringify(operation)}; expected l4_message, l4_verdict or l4_lenses`);
    }
  } catch (e) {
    res.ok = false;
    res.errors.push(String((e as Error).message || e));
  }
  return JSON.stringify(res);
}
