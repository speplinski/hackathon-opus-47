// L8 — design optimisation: thin spine (iteration 0 baseline + iteration 1
// re-audit of the L7 after_snapshot, Pareto verdict) and the multi-round
// loop (design-tweak -> design-optimize re-audit -> Tchebycheff or Pareto
// verdict). Ports of:
//   src/auditable_design/layers/l8_optimize.py
//   src/auditable_design/layers/l8_optimize_loop.py
//   src/auditable_design/evaluators/pareto.py
//   src/auditable_design/evaluators/tchebycheff.py
// Messages and artifact markdown are byte-identical to the Python output.

import { InsightCluster, ReconciledVerdict, DesignDecision, OptimizationIteration } from "./types";
import { escapeXml, extractJsonObjectText } from "./common";

// ---------------------------------------------------------------------------
// Constants (same values as the Python modules)
// ---------------------------------------------------------------------------

export const L8_BASELINE_SKILL_ID = "reconciled";
export const L8_VALID_ANCHORED_SEVERITIES: number[] = [0, 3, 5, 7, 9];
export const L8_DEFAULT_MAX_REGRESSION = 1;
export const L8_DEFAULT_MIN_IMPROVEMENT_PCT = 10.0;
export const L8_DEFAULT_MAX_ITERATIONS = 5;
export const L8_DEFAULT_STALL_LIMIT = 2;
export const L8_CONVERGENCE_SEVERITY_THRESHOLD = 5;
const L8_MIN_SNAPSHOT_WORDS = 50;
const L8_MAX_SNAPSHOT_WORDS = 400;

export type L8Scores = { [heuristic: string]: number };

// ---------------------------------------------------------------------------
// Python formatting helpers
// ---------------------------------------------------------------------------

// Python repr() of a str.
export function l8PyRepr(s: string): string {
  const quote = s.indexOf("'") >= 0 && s.indexOf('"') < 0 ? '"' : "'";
  let out = quote;
  for (const ch of Array.from(s)) {
    const cp = ch.codePointAt(0) as number;
    if (ch === "\\") out += "\\\\";
    else if (ch === quote) out += "\\" + ch;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (cp < 0x20 || cp === 0x7f) out += "\\x" + (cp < 16 ? "0" : "") + cp.toString(16);
    else out += ch;
  }
  return out + quote;
}

// Python repr() of a list of str (e.g. sorted(missing)).
export function l8PyListRepr(xs: string[]): string {
  return "[" + xs.map((x: string): string => l8PyRepr(x)).join(", ") + "]";
}

export function l8PyTypeName(v: unknown): string {
  if (v === null || v === undefined) return "NoneType";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  if (typeof v === "string") return "str";
  if (Array.isArray(v)) return "list";
  return "dict";
}

// Python format(x, ".Nf") / "+.Nf": round-half-even on the exact binary value.
export function l8PyFixed(x: number, digits: number, plus = false): string {
  const neg = x < 0 || (x === 0 && 1 / x < 0);
  const ax = Math.abs(x);
  let body: string;
  const exact = ax.toPrecision(100);
  if (/e/.test(exact)) {
    body = ax.toFixed(digits);
  } else {
    const parts = exact.split(".");
    const intPart = parts[0];
    const frac = (parts[1] || "").padEnd(digits + 1, "0");
    const kept = intPart + frac.slice(0, digits);
    const next = frac.charAt(digits);
    const rest = frac.slice(digits + 1);
    let roundUp = false;
    if (next > "5") roundUp = true;
    else if (next === "5") {
      if (/[1-9]/.test(rest)) roundUp = true;
      else roundUp = parseInt(kept.charAt(kept.length - 1), 10) % 2 === 1;
    }
    let digitsArr = kept.split("");
    if (roundUp) {
      let i = digitsArr.length - 1;
      while (i >= 0) {
        if (digitsArr[i] === "9") {
          digitsArr[i] = "0";
          i--;
        } else {
          digitsArr[i] = String(parseInt(digitsArr[i], 10) + 1);
          break;
        }
      }
      if (i < 0) digitsArr = ["1"].concat(digitsArr);
    }
    const all = digitsArr.join("");
    const ip = all.slice(0, all.length - digits).replace(/^0+(?=\d)/, "");
    body = digits > 0 ? (ip || "0") + "." + all.slice(all.length - digits) : ip || "0";
  }
  if (neg) return "-" + body;
  return (plus ? "+" : "") + body;
}

function l8SignedInt(n: number): string {
  return (n >= 0 ? "+" : "") + String(n);
}

function l8Pad2(n: number): string {
  return n < 10 && n >= 0 ? "0" + String(n) : String(n);
}

function l8Sorted(xs: string[]): string[] {
  return xs.slice().sort();
}

function l8SameKeySet(a: L8Scores, b: L8Scores): boolean {
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) if (!Object.prototype.hasOwnProperty.call(b, k)) return false;
  return true;
}

// ---------------------------------------------------------------------------
// Verifiers
// ---------------------------------------------------------------------------

export interface L8ParetoVerdict {
  accepted: boolean;
  reason: string;
  regression_count: number;
  dominance: boolean;
  delta_per_heuristic: L8Scores;
}

export interface L8TchebycheffVerdict {
  accepted: boolean;
  reason: string;
  parent_cost: number;
  child_cost: number;
  binding_heuristic: string | null;
  improvement_pct: number;
  converged: boolean;
  regression_count: number;
  delta_per_heuristic: L8Scores;
}

export interface L8LoopVerdict {
  verifier: string;
  accepted: boolean;
  reason: string;
  regression_count: number;
  converged: boolean;
  delta_per_heuristic: L8Scores;
}

function l8KeyMismatch(where: string, parent: L8Scores, child: L8Scores): Error {
  return new Error(
    `${where}: parent keys ${l8PyListRepr(l8Sorted(Object.keys(parent)))} != child keys ${l8PyListRepr(
      l8Sorted(Object.keys(child))
    )}`
  );
}

function l8Delta(parent: L8Scores, child: L8Scores): L8Scores {
  const d: L8Scores = {};
  for (const h of Object.keys(parent)) d[h] = child[h] - parent[h];
  return d;
}

// evaluators/pareto.py: verdict
export function l8ParetoVerdict(parent: L8Scores, child: L8Scores, maxRegression = L8_DEFAULT_MAX_REGRESSION): L8ParetoVerdict {
  if (!l8SameKeySet(parent, child)) throw l8KeyMismatch("verdict", parent, child);
  const delta = l8Delta(parent, child);
  const regressions = Object.keys(delta).filter((h: string): boolean => delta[h] > 0);
  const improvements = Object.keys(delta).filter((h: string): boolean => delta[h] < 0);
  let dominates = true;
  let strictBetter = false;
  for (const h of Object.keys(parent)) {
    if (child[h] > parent[h]) {
      dominates = false;
      break;
    }
    if (child[h] < parent[h]) strictBetter = true;
  }
  dominates = dominates && strictBetter;
  if (dominates) {
    return {
      accepted: true,
      reason:
        `Pareto dominance: child improves on ${improvements.length} heuristic(s) ` +
        `(${l8Sorted(improvements).join(", ")}) without regressing on any.`,
      regression_count: 0,
      dominance: true,
      delta_per_heuristic: delta,
    };
  }
  const sum = (s: L8Scores): number => Object.keys(s).reduce((a: number, k: string): number => a + s[k], 0);
  const parentSum = sum(parent);
  const childSum = sum(child);
  const regressionCount = regressions.length;
  if (regressionCount === 0 && improvements.length === 0) {
    return {
      accepted: false,
      reason:
        "No improvement: every heuristic's severity is identical to the parent iteration; the child is a no-op.",
      regression_count: 0,
      dominance: false,
      delta_per_heuristic: delta,
    };
  }
  if (regressionCount <= maxRegression && childSum < parentSum) {
    return {
      accepted: true,
      reason:
        `Weighted-sum fallback: child sum ${childSum} < parent sum ${parentSum} ` +
        `(${parentSum - childSum} severity units reduced); ${regressionCount} regression ` +
        `(${l8Sorted(regressions).join(", ")}) tolerated at max_regression=${maxRegression}. ` +
        `Improvements: ${l8Sorted(improvements).join(", ")}.`,
      regression_count: regressionCount,
      dominance: false,
      delta_per_heuristic: delta,
    };
  }
  const regressed = l8Sorted(regressions).join(", ");
  const reason =
    regressionCount > maxRegression
      ? `Rejected: ${regressionCount} heuristic regressions (${regressed}) exceed max_regression=${maxRegression}.`
      : `Rejected: child sum ${childSum} >= parent sum ${parentSum} — no net severity improvement despite ` +
        `${regressionCount} regression (${regressed}).`;
  return { accepted: false, reason, regression_count: regressionCount, dominance: false, delta_per_heuristic: delta };
}

// evaluators/tchebycheff.py: tchebycheff_cost
export function l8TchebycheffCost(scores: L8Scores, weights: L8Scores): { cost: number; binding: string | null } {
  if (!l8SameKeySet(scores, weights)) {
    throw new Error(
      `tchebycheff_cost: scores keys ${l8PyListRepr(l8Sorted(Object.keys(scores)))} != weights keys ${l8PyListRepr(
        l8Sorted(Object.keys(weights))
      )}`
    );
  }
  const keys = l8Sorted(Object.keys(scores));
  if (keys.length === 0) return { cost: 0, binding: null };
  let best: string | null = null;
  let bestVal = -1;
  for (const h of keys) {
    const v = scores[h] * weights[h];
    if (v > bestVal) {
      bestVal = v;
      best = h;
    }
  }
  return { cost: bestVal, binding: best };
}

// evaluators/tchebycheff.py: verdict
export function l8TchebycheffVerdict(
  parent: L8Scores,
  child: L8Scores,
  minImprovementPct = L8_DEFAULT_MIN_IMPROVEMENT_PCT
): L8TchebycheffVerdict {
  if (!l8SameKeySet(parent, child)) throw l8KeyMismatch("verdict", parent, child);
  const delta = l8Delta(parent, child);
  const regressionCount = Object.keys(delta).filter((h: string): boolean => delta[h] > 0).length;
  const parentCost = l8TchebycheffCost(parent, parent).cost;
  const childRes = l8TchebycheffCost(child, parent);
  const childCost = childRes.cost;
  const binding = childRes.binding;
  if (parentCost === 0) {
    return {
      accepted: false,
      reason:
        "Converged: parent weighted-max residual is 0 (every heuristic already at severity 0 — no " +
        "further improvement measurable under weighted Tchebycheff cost).",
      parent_cost: 0,
      child_cost: childCost,
      binding_heuristic: binding,
      improvement_pct: 0.0,
      converged: true,
      regression_count: regressionCount,
      delta_per_heuristic: delta,
    };
  }
  const improvementPct = (100.0 * (parentCost - childCost)) / parentCost;
  const thresholdCost = parentCost * (1.0 - minImprovementPct / 100.0);
  const regressions = l8Sorted(Object.keys(delta).filter((h: string): boolean => delta[h] > 0));
  if (childCost < thresholdCost) {
    const improvements = l8Sorted(Object.keys(delta).filter((h: string): boolean => delta[h] < 0));
    const parts: string[] = [
      `Weighted Tchebycheff cost ${childCost} < threshold ${l8PyFixed(thresholdCost, 1)} ` +
        `(parent ${parentCost}, -${l8PyFixed(improvementPct, 1)}% vs min ${l8PyFixed(minImprovementPct, 1)}%). ` +
        `Binding child heuristic: ${binding}.`,
    ];
    if (improvements.length) parts.push(`Improvements: ${improvements.join(", ")}.`);
    if (regressions.length) parts.push(`Tolerated regressions: ${regressions.join(", ")}.`);
    return {
      accepted: true,
      reason: parts.join(" "),
      parent_cost: parentCost,
      child_cost: childCost,
      binding_heuristic: binding,
      improvement_pct: improvementPct,
      converged: false,
      regression_count: regressionCount,
      delta_per_heuristic: delta,
    };
  }
  let reason: string;
  if (childCost >= parentCost) {
    reason =
      `Rejected: weighted Tchebycheff cost ${childCost} >= parent cost ${parentCost} — no net improvement ` +
      `(${l8PyFixed(improvementPct, 1, true)}%). Binding heuristic: ${binding}.`;
    if (regressions.length) reason += ` Regressions: ${regressions.join(", ")}.`;
  } else {
    reason =
      `Rejected: weighted Tchebycheff cost drop ${l8PyFixed(improvementPct, 1)}% below required ` +
      `${l8PyFixed(minImprovementPct, 1)}% threshold (parent ${parentCost} -> child ${childCost}, ` +
      `threshold ${l8PyFixed(thresholdCost, 1)}). Binding heuristic: ${binding}.`;
  }
  return {
    accepted: false,
    reason,
    parent_cost: parentCost,
    child_cost: childCost,
    binding_heuristic: binding,
    improvement_pct: improvementPct,
    converged: false,
    regression_count: regressionCount,
    delta_per_heuristic: delta,
  };
}

// l8_optimize_loop.apply_verifier
export function l8ApplyVerifier(
  parent: L8Scores,
  child: L8Scores,
  verifier: string,
  maxRegression = L8_DEFAULT_MAX_REGRESSION,
  minImprovementPct = L8_DEFAULT_MIN_IMPROVEMENT_PCT
): { verdict: L8LoopVerdict; raw: L8ParetoVerdict | L8TchebycheffVerdict } {
  if (verifier === "pareto") {
    const raw = l8ParetoVerdict(parent, child, maxRegression);
    const converged = Object.keys(parent).every((h: string): boolean => parent[h] === 0);
    return {
      verdict: {
        verifier: "pareto",
        accepted: raw.accepted,
        reason: raw.reason,
        regression_count: raw.regression_count,
        converged,
        delta_per_heuristic: raw.delta_per_heuristic,
      },
      raw,
    };
  }
  if (verifier === "tchebycheff") {
    const raw = l8TchebycheffVerdict(parent, child, minImprovementPct);
    return {
      verdict: {
        verifier: "tchebycheff",
        accepted: raw.accepted,
        reason: raw.reason,
        regression_count: raw.regression_count,
        converged: raw.converged,
        delta_per_heuristic: raw.delta_per_heuristic,
      },
      raw,
    };
  }
  throw new Error(`unknown verifier ${l8PyRepr(verifier)} — expected 'pareto' or 'tchebycheff'`);
}

// ---------------------------------------------------------------------------
// Scores helpers
// ---------------------------------------------------------------------------

// l8_optimize.reconciled_heuristic_list
export function l8HeuristicList(reconciled: ReconciledVerdict): string[] {
  const seen: string[] = [];
  for (const v of reconciled.ranked_violations) if (seen.indexOf(v.heuristic) < 0) seen.push(v.heuristic);
  return seen;
}

// l8_optimize._baseline_scores (flat)
export function l8BaselineScores(reconciled: ReconciledVerdict): L8Scores {
  const out: L8Scores = {};
  for (const v of reconciled.ranked_violations) {
    const prev = Object.prototype.hasOwnProperty.call(out, v.heuristic) ? out[v.heuristic] : 0;
    out[v.heuristic] = Math.max(prev, Math.trunc(v.severity));
  }
  return out;
}

// l8_optimize_loop._flat_scores
export function l8FlatScores(scores: { [skill: string]: L8Scores }): L8Scores {
  if (Object.prototype.hasOwnProperty.call(scores, L8_BASELINE_SKILL_ID)) {
    const b = scores[L8_BASELINE_SKILL_ID];
    const out: L8Scores = {};
    for (const k of Object.keys(b)) out[k] = b[k];
    return out;
  }
  const flat: L8Scores = {};
  for (const bucket of Object.keys(scores)) {
    for (const h of Object.keys(scores[bucket])) {
      flat[h] = Math.max(Object.prototype.hasOwnProperty.call(flat, h) ? flat[h] : 0, scores[bucket][h]);
    }
  }
  return flat;
}

// l8_optimize_loop._severity_sum
export function l8SeveritySum(scores: { [skill: string]: L8Scores }): number {
  let total = 0;
  for (const bucket of Object.keys(scores)) for (const h of Object.keys(scores[bucket])) total += scores[bucket][h];
  return total;
}

function l8IterationId(clusterId: string, index: number): string {
  return `iteration__${clusterId}__${l8Pad2(index)}`;
}

function l8ArtifactRef(artifactsDir: string, clusterId: string, index: number): string {
  const name = `${clusterId}_iter${l8Pad2(index)}.md`;
  return artifactsDir ? `${artifactsDir.replace(/\/+$/, "")}/${name}` : name;
}

function l8Now(recordedAt: string | undefined): string {
  return recordedAt || new Date().toISOString();
}

// ---------------------------------------------------------------------------
// Messages
// ---------------------------------------------------------------------------

function l8ClusterBlock(cluster: InsightCluster): string[] {
  const parts: string[] = [
    "<cluster>",
    `  <cluster_id>${escapeXml(cluster.cluster_id)}</cluster_id>`,
    `  <label>${escapeXml(cluster.label)}</label>`,
  ];
  if (cluster.ui_context !== null && cluster.ui_context !== undefined) {
    parts.push(`  <ui_context>${escapeXml(cluster.ui_context)}</ui_context>`);
  }
  cluster.representative_quotes.forEach((q: string, i: number): void => {
    parts.push(`  <q idx="${i}">${escapeXml(q)}</q>`);
  });
  parts.push("</cluster>");
  return parts;
}

// l8_optimize.build_user_message (before/after from the L7 decision) and
// l8_optimize_loop.build_reaudit_user_message (before/after from the loop)
// render the same envelope.
export function l8ReauditMessage(
  cluster: InsightCluster,
  reconciled: ReconciledVerdict,
  beforeSnapshot: string,
  afterSnapshot: string
): string {
  const parts = l8ClusterBlock(cluster);
  parts.push(`<before_snapshot>${escapeXml(beforeSnapshot)}</before_snapshot>`);
  parts.push(`<after_snapshot>${escapeXml(afterSnapshot)}</after_snapshot>`);
  parts.push("<baseline_heuristics>");
  for (const v of reconciled.ranked_violations) {
    parts.push(`  <h slug="${escapeXml(v.heuristic)}" severity="${v.severity}">${escapeXml(v.violation)}</h>`);
  }
  parts.push("</baseline_heuristics>");
  return parts.join("\n");
}

// l8_optimize_loop.build_tweak_user_message
export function l8TweakMessage(
  cluster: InsightCluster,
  reconciled: ReconciledVerdict,
  currentSnapshot: string,
  currentScores: L8Scores,
  verdictReason: string
): string {
  const parts = l8ClusterBlock(cluster);
  parts.push(`<current_snapshot>${escapeXml(currentSnapshot)}</current_snapshot>`);
  parts.push("<current_scores>");
  for (const h of l8Sorted(Object.keys(currentScores))) parts.push(`  ${escapeXml(h)}: ${currentScores[h]}`);
  parts.push("</current_scores>");
  parts.push("<baseline_heuristics>");
  for (const v of reconciled.ranked_violations) {
    parts.push(`  <h slug="${escapeXml(v.heuristic)}">${escapeXml(v.violation)}</h>`);
  }
  parts.push("</baseline_heuristics>");
  parts.push(`<verdict_reason>${escapeXml(verdictReason)}</verdict_reason>`);
  return parts.join("\n");
}

// ---------------------------------------------------------------------------
// Parsers
// ---------------------------------------------------------------------------

export interface L8ParseResult {
  ok: boolean;
  payload: { [k: string]: unknown } | null;
  error: string | null;
}

function l8LoadObject(text: string): { data: { [k: string]: unknown } | null; error: string | null } {
  const raw = extractJsonObjectText(text);
  if (raw === null) return { data: null, error: `no JSON object found in response: ${l8PyRepr(text)}` };
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    return { data: null, error: `malformed JSON: ${String((e as Error).message)}; text=${l8PyRepr(text)}` };
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    return { data: null, error: `expected JSON object, got ${l8PyTypeName(data)}` };
  }
  return { data: data as { [k: string]: unknown }, error: null };
}

function l8KeyCheck(data: { [k: string]: unknown }, required: string[]): string | null {
  const actual = Object.keys(data);
  const missing = required.filter((k: string): boolean => actual.indexOf(k) < 0);
  if (missing.length) return `missing required top-level keys: ${l8PyListRepr(l8Sorted(missing))}`;
  const extra = actual.filter((k: string): boolean => required.indexOf(k) < 0);
  if (extra.length) return `unexpected top-level keys: ${l8PyListRepr(l8Sorted(extra))}`;
  return null;
}

// l8_optimize.parse_optimize_response
export function l8ParseReaudit(text: string, baselineHeuristics: string[]): L8ParseResult {
  const fail = (error: string): L8ParseResult => ({ ok: false, payload: null, error });
  const loaded = l8LoadObject(text);
  if (loaded.data === null) return fail(loaded.error as string);
  const data = loaded.data;
  const keyErr = l8KeyCheck(data, ["scored_heuristics", "reasoning"]);
  if (keyErr) return fail(keyErr);
  const scored = data["scored_heuristics"];
  if (scored === null || typeof scored !== "object" || Array.isArray(scored)) {
    return fail(`'scored_heuristics' must be dict, got ${l8PyTypeName(scored)}`);
  }
  const sc = scored as { [k: string]: unknown };
  const baselineSet = baselineHeuristics;
  const scoredKeys = Object.keys(sc);
  const missingH = baselineSet.filter((h: string): boolean => scoredKeys.indexOf(h) < 0);
  const extraH = scoredKeys.filter((h: string): boolean => baselineSet.indexOf(h) < 0);
  if (missingH.length || extraH.length) {
    const parts: string[] = [];
    if (missingH.length) parts.push(`missing heuristics: ${l8PyListRepr(l8Sorted(uniq(missingH)))}`);
    if (extraH.length) parts.push(`extra heuristics: ${l8PyListRepr(l8Sorted(extraH))}`);
    return fail(`scored_heuristics keys must exactly match baseline list — ${parts.join("; ")}`);
  }
  for (const h of scoredKeys) {
    const sev = sc[h];
    if (typeof sev !== "number" || !Number.isInteger(sev)) {
      return fail(`scored_heuristics[${l8PyRepr(h)}] must be int, got ${l8PyTypeName(sev)}`);
    }
    if (L8_VALID_ANCHORED_SEVERITIES.indexOf(sev) < 0) {
      return fail(`scored_heuristics[${l8PyRepr(h)}]=${sev} not in [0, 3, 5, 7, 9]`);
    }
  }
  const reasoning = data["reasoning"];
  if (typeof reasoning !== "string" || reasoning.trim().length === 0) return fail("'reasoning' must be non-empty str");
  return { ok: true, payload: data, error: null };
}

function uniq(xs: string[]): string[] {
  const out: string[] = [];
  for (const x of xs) if (out.indexOf(x) < 0) out.push(x);
  return out;
}

// l8_optimize_loop.parse_tweak_response
export function l8ParseTweak(text: string, currentScores: L8Scores): L8ParseResult {
  const fail = (error: string): L8ParseResult => ({ ok: false, payload: null, error });
  const loaded = l8LoadObject(text);
  if (loaded.data === null) return fail(loaded.error as string);
  const data = loaded.data;
  const keyErr = l8KeyCheck(data, ["new_snapshot", "addresses_heuristics", "preserves_heuristics", "reasoning"]);
  if (keyErr) return fail(keyErr);
  const snapshot = data["new_snapshot"];
  if (typeof snapshot !== "string" || snapshot.trim().length === 0) return fail("'new_snapshot' must be non-empty str");
  const trimmed = snapshot.trim();
  const nWords = trimmed.length === 0 ? 0 : trimmed.split(/\s+/).length;
  if (nWords < L8_MIN_SNAPSHOT_WORDS || nWords > L8_MAX_SNAPSHOT_WORDS) {
    return fail(`new_snapshot length ${nWords} words outside [${L8_MIN_SNAPSHOT_WORDS}, ${L8_MAX_SNAPSHOT_WORDS}]`);
  }
  const addresses = data["addresses_heuristics"];
  if (!Array.isArray(addresses) || addresses.length === 0) return fail("'addresses_heuristics' must be non-empty list");
  const preserves = data["preserves_heuristics"];
  if (!Array.isArray(preserves)) return fail("'preserves_heuristics' must be list");
  const known = Object.keys(currentScores);
  const addr = uniq((addresses as unknown[]).map((x: unknown): string => String(x)));
  const pres = uniq((preserves as unknown[]).map((x: unknown): string => String(x)));
  const extraAddr = addr.filter((h: string): boolean => known.indexOf(h) < 0);
  if (extraAddr.length) return fail(`addresses_heuristics contains unknown slugs: ${l8PyListRepr(l8Sorted(extraAddr))}`);
  const extraPres = pres.filter((h: string): boolean => known.indexOf(h) < 0);
  if (extraPres.length) return fail(`preserves_heuristics contains unknown slugs: ${l8PyListRepr(l8Sorted(extraPres))}`);
  const nonResidual = addr.filter((h: string): boolean => currentScores[h] === 0);
  if (nonResidual.length) {
    return fail(
      "addresses_heuristics contains slugs already at severity 0 " +
        `(must address residuals only): ${l8PyListRepr(l8Sorted(nonResidual))}`
    );
  }
  const nonZero = pres.filter((h: string): boolean => currentScores[h] !== 0);
  if (nonZero.length) {
    return fail(
      "preserves_heuristics contains slugs NOT at severity 0 " +
        `(must preserve resolved only): ${l8PyListRepr(l8Sorted(nonZero))}`
    );
  }
  const reasoning = data["reasoning"];
  if (typeof reasoning !== "string" || reasoning.trim().length === 0) return fail("'reasoning' must be non-empty str");
  return { ok: true, payload: data, error: null };
}

// ---------------------------------------------------------------------------
// Thin spine: iteration 0 and iteration 1
// ---------------------------------------------------------------------------

export interface L8IterationOut {
  iteration: OptimizationIteration;
  artifact_ref: string;
  artifact_md: string;
}

// l8_optimize.build_baseline_iteration
export function l8Baseline(
  cluster: InsightCluster,
  reconciled: ReconciledVerdict,
  decision: DesignDecision,
  runId: string,
  artifactsDir: string,
  recordedAt?: string
): L8IterationOut {
  const scores = l8BaselineScores(reconciled);
  let md =
    `# ${cluster.cluster_id} — iteration 0 (baseline)\n\n` +
    `## Cluster label\n${cluster.label}\n\n` +
    `## before_snapshot (from L7 decision ${decision.decision_id})\n` +
    `${decision.before_snapshot}\n\n` +
    `## Baseline heuristic severities (from L5 reconciled verdict)\n`;
  for (const h of Object.keys(scores)) md += `- \`${h}\` — severity ${scores[h]}\n`;
  const ref = l8ArtifactRef(artifactsDir, cluster.cluster_id, 0);
  return {
    iteration: {
      iteration_id: l8IterationId(cluster.cluster_id, 0),
      run_id: runId,
      iteration_index: 0,
      parent_iteration_id: null,
      design_artifact_ref: ref,
      scores: { [L8_BASELINE_SKILL_ID]: scores },
      reasoning:
        "Baseline — heuristic severities imported verbatim from L5 reconciled verdict. No Claude call; " +
        "no regression possible (iteration 0 is its own reference).",
      accepted: true,
      regression_reason: null,
      delta_per_heuristic: {},
      informing_review_ids: cluster.member_review_ids.slice(),
      recorded_at: l8Now(recordedAt),
    },
    artifact_ref: ref,
    artifact_md: md,
  };
}

export interface L8ProposedOut extends L8IterationOut {
  status: string; // "optimized" | "fallback"
  reason: string | null;
  native_payload: { [k: string]: unknown };
  verdict: L8ParetoVerdict;
}

// l8_optimize.optimize_cluster (after the model call)
export function l8Proposed(
  cluster: InsightCluster,
  reconciled: ReconciledVerdict,
  decision: DesignDecision,
  baseline: OptimizationIteration,
  responseText: string,
  runId: string,
  artifactsDir: string,
  maxRegression = L8_DEFAULT_MAX_REGRESSION,
  recordedAt?: string
): L8ProposedOut {
  const baselineHeuristics = l8HeuristicList(reconciled);
  const baselineScores = baseline.scores[L8_BASELINE_SKILL_ID];
  const parsed = l8ParseReaudit(responseText, baselineHeuristics);
  let proposed: L8Scores;
  let reasoningText: string;
  if (parsed.ok && parsed.payload) {
    const sc = parsed.payload["scored_heuristics"] as L8Scores;
    proposed = {};
    for (const k of Object.keys(sc)) proposed[k] = sc[k];
    reasoningText = parsed.payload["reasoning"] as string;
  } else {
    proposed = {};
    for (const k of Object.keys(baselineScores)) proposed[k] = baselineScores[k];
    reasoningText =
      `Fallback — parse failure on re-audit; proposed iteration copies baseline scores (no improvement). ` +
      `Parse error: ${parsed.error}`;
  }
  const v = l8ParetoVerdict(baselineScores, proposed, maxRegression);
  let md =
    `# ${cluster.cluster_id} — iteration 1 (proposed)\n\n` +
    `## Parent iteration\n${baseline.iteration_id}\n\n` +
    `## after_snapshot (from L7 decision ${decision.decision_id})\n` +
    `${decision.after_snapshot}\n\n` +
    `## Re-audit severities\n`;
  for (const h of baselineHeuristics) {
    const d = Object.prototype.hasOwnProperty.call(v.delta_per_heuristic, h) ? v.delta_per_heuristic[h] : 0;
    md += `- \`${h}\` — baseline ${baselineScores[h]} → proposed ${proposed[h]} (delta ${l8SignedInt(d)})\n`;
  }
  md += `\n## Model reasoning\n${reasoningText}\n\n`;
  md += `## Pareto verdict\n${v.reason}\n`;
  const ref = l8ArtifactRef(artifactsDir, cluster.cluster_id, 1);
  return {
    iteration: {
      iteration_id: l8IterationId(cluster.cluster_id, 1),
      run_id: runId,
      iteration_index: 1,
      parent_iteration_id: baseline.iteration_id,
      design_artifact_ref: ref,
      scores: { [L8_BASELINE_SKILL_ID]: proposed },
      reasoning: reasoningText,
      accepted: v.accepted,
      regression_reason: v.accepted ? null : v.reason,
      delta_per_heuristic: v.delta_per_heuristic,
      informing_review_ids: cluster.member_review_ids.slice(),
      recorded_at: l8Now(recordedAt),
    },
    artifact_ref: ref,
    artifact_md: md,
    status: parsed.ok ? "optimized" : "fallback",
    reason: parsed.ok ? null : parsed.error,
    native_payload: parsed.ok && parsed.payload
      ? parsed.payload
      : { fallback: true, reason: parsed.error, raw_response: responseText },
    verdict: v,
  };
}

// ---------------------------------------------------------------------------
// Multi-round loop
// ---------------------------------------------------------------------------

export interface L8LoopStepRecord {
  iteration: OptimizationIteration;
  new_snapshot: string;
  converged?: boolean;
}

export interface L8LoopPlan {
  action: string; // "tweak" | "stop"
  termination_reason: string | null;
  next_index: number;
  parent_iteration_id: string;
  parent_iteration: OptimizationIteration; // pass as-is to l8_loop_step
  parent_snapshot: string;
  parent_scores: L8Scores;
  tweak_message: string | null;
  final_parent_id: string;
}

function l8LatestAccepted(its: OptimizationIteration[]): OptimizationIteration | null {
  for (let i = its.length - 1; i >= 0; i--) if (its[i].accepted) return its[i];
  return null;
}

// l8_optimize_loop._snapshot_of: iteration 0 -> before_snapshot, 1 ->
// after_snapshot, 2+ -> the "## new_snapshot" section of its artifact
// (passed in `artifactBodies`, keyed by design_artifact_ref) or an explicit
// snapshot (keyed by iteration_id).
export function l8SnapshotOf(
  it: OptimizationIteration,
  decision: DesignDecision,
  snapshots: { [iterationId: string]: string },
  artifactBodies: { [ref: string]: string }
): string {
  if (it.iteration_index === 0) return decision.before_snapshot;
  if (it.iteration_index === 1) return decision.after_snapshot;
  if (Object.prototype.hasOwnProperty.call(snapshots, it.iteration_id)) return snapshots[it.iteration_id];
  const body = artifactBodies[it.design_artifact_ref];
  if (body === undefined) {
    throw new Error(`artifact ${it.design_artifact_ref} missing for iteration ${it.iteration_id}; cannot resume loop`);
  }
  const s = l8ExtractSection(body, "new_snapshot");
  if (s === null) throw new Error(`artifact ${it.design_artifact_ref} has no '## new_snapshot' section`);
  return s;
}

export function l8ExtractSection(body: string, header: string): string | null {
  const marker = `## ${header}\n`;
  const idx = body.indexOf(marker);
  if (idx < 0) return null;
  const after = body.slice(idx + marker.length);
  const end = after.indexOf("\n## ");
  return (end < 0 ? after : after.slice(0, end)).trim();
}

// Replays l8_optimize_loop.run_loop's control flow over the steps taken so
// far and says what to do next. The flow calls this before every round.
export function l8LoopPlan(
  cluster: InsightCluster,
  reconciled: ReconciledVerdict,
  decision: DesignDecision,
  existing: OptimizationIteration[],
  steps: L8LoopStepRecord[],
  opts: {
    max_iterations?: number;
    stall_limit?: number;
    severity_threshold?: number;
    halt_reason?: string | null;
    snapshots?: { [iterationId: string]: string };
    artifact_bodies?: { [ref: string]: string };
  }
): L8LoopPlan {
  if (existing.length < 1) throw new Error("need at least the baseline iteration");
  const sortedExisting = existing.slice().sort((a, b) => a.iteration_index - b.iteration_index);
  const maxIterations = opts.max_iterations === undefined ? L8_DEFAULT_MAX_ITERATIONS : opts.max_iterations;
  const stallLimit = opts.stall_limit === undefined ? L8_DEFAULT_STALL_LIMIT : opts.stall_limit;
  const threshold = opts.severity_threshold === undefined ? L8_CONVERGENCE_SEVERITY_THRESHOLD : opts.severity_threshold;
  const snapshots = opts.snapshots || {};
  const bodies = opts.artifact_bodies || {};

  let parent = l8LatestAccepted(sortedExisting) || sortedExisting[0];
  let parentSnapshot = l8SnapshotOf(parent, decision, snapshots, bodies);
  let consecutive = 0;
  let nextIndex = Math.max(...sortedExisting.map((it) => it.iteration_index)) + 1;
  let termination: string | null = null;

  for (const step of steps) {
    if (step.iteration.accepted) {
      consecutive = 0;
      parent = step.iteration;
      parentSnapshot = step.new_snapshot;
    } else {
      consecutive += 1;
    }
    nextIndex += 1;
    if (step.converged) {
      termination = "converged";
      break;
    }
    if (consecutive >= stallLimit) {
      termination = "stall";
      break;
    }
  }
  if (termination === null && opts.halt_reason) termination = opts.halt_reason;

  const parentScores = l8FlatScores(parent.scores);
  let tweak: string | null = null;
  if (termination === null) {
    if (!(nextIndex < maxIterations)) termination = "max_iterations";
    else if (Object.keys(parentScores).length && Object.keys(parentScores).every((h) => parentScores[h] === 0)) {
      termination = "converged";
    } else if (l8SeveritySum(parent.scores) <= threshold) {
      termination = "severity_threshold";
    } else {
      const reason = parent.regression_reason || parent.reasoning || "Continuing refinement.";
      tweak = l8TweakMessage(cluster, reconciled, parentSnapshot, parentScores, reason);
    }
  }
  const all = sortedExisting.concat(steps.map((s) => s.iteration));
  const finalParent = l8LatestAccepted(all) || sortedExisting[0];
  return {
    action: tweak === null ? "stop" : "tweak",
    termination_reason: termination,
    next_index: nextIndex,
    parent_iteration_id: parent.iteration_id,
    parent_iteration: parent,
    parent_snapshot: parentSnapshot,
    parent_scores: parentScores,
    tweak_message: tweak,
    final_parent_id: finalParent.iteration_id,
  };
}

export interface L8LoopStepOut {
  ok: boolean;
  termination_reason: string | null; // "tweak_parse_fail" | "reaudit_parse_fail" on failure
  error: string | null;
  iteration: OptimizationIteration | null;
  new_snapshot: string | null;
  verdict: L8LoopVerdict | null;
  verdict_raw: L8ParetoVerdict | L8TchebycheffVerdict | null;
  artifact_ref: string | null;
  artifact_md: string | null;
  native_payload: { [k: string]: unknown } | null;
}

// One round of run_loop after both model calls: parse tweak + re-audit,
// apply the verifier, assemble the iteration and its artifact.
export function l8LoopStep(
  cluster: InsightCluster,
  reconciled: ReconciledVerdict,
  parentIteration: OptimizationIteration,
  parentSnapshot: string,
  nextIndex: number,
  tweakResponseText: string,
  reauditResponseText: string,
  verifier: string,
  runId: string,
  artifactsDir: string,
  maxRegression = L8_DEFAULT_MAX_REGRESSION,
  minImprovementPct = L8_DEFAULT_MIN_IMPROVEMENT_PCT,
  recordedAt?: string
): L8LoopStepOut {
  const empty: L8LoopStepOut = {
    ok: false, termination_reason: null, error: null, iteration: null, new_snapshot: null,
    verdict: null, verdict_raw: null, artifact_ref: null, artifact_md: null, native_payload: null,
  };
  const parentScores = l8FlatScores(parentIteration.scores);
  const tweak = l8ParseTweak(tweakResponseText, parentScores);
  if (!tweak.ok || !tweak.payload) return { ...empty, termination_reason: "tweak_parse_fail", error: tweak.error };
  const newSnapshot = String(tweak.payload["new_snapshot"]);
  const baselineHeuristics = l8HeuristicList(reconciled);
  const reaudit = l8ParseReaudit(reauditResponseText, baselineHeuristics);
  if (!reaudit.ok || !reaudit.payload) {
    return { ...empty, termination_reason: "reaudit_parse_fail", error: reaudit.error, new_snapshot: newSnapshot };
  }
  const sc = reaudit.payload["scored_heuristics"] as L8Scores;
  const proposed: L8Scores = {};
  for (const k of Object.keys(sc)) proposed[k] = sc[k];
  const reasoningText = String(reaudit.payload["reasoning"]);
  const applied = l8ApplyVerifier(parentScores, proposed, verifier, maxRegression, minImprovementPct);
  const v = applied.verdict;

  const addresses = (tweak.payload["addresses_heuristics"] as unknown[]).map((x) => String(x));
  const preserves = (tweak.payload["preserves_heuristics"] as unknown[]).map((x) => String(x));
  let md =
    `# ${cluster.cluster_id} — iteration ${nextIndex} (${v.accepted ? "accepted" : "rejected"})\n\n` +
    `## Parent iteration\n${parentIteration.iteration_id}\n\n` +
    `## Verifier\n${v.verifier}\n\n` +
    `## parent_snapshot\n${parentSnapshot}\n\n` +
    `## new_snapshot\n${newSnapshot}\n\n` +
    `## design-tweak addresses\n`;
  for (const h of addresses) md += `- \`${h}\`\n`;
  md += "\n## design-tweak preserves\n";
  for (const h of preserves) md += `- \`${h}\`\n`;
  md += `\n## design-tweak reasoning\n${String(tweak.payload["reasoning"])}\n\n`;
  md += "## Re-audit severities\n";
  for (const h of baselineHeuristics) {
    const d = Object.prototype.hasOwnProperty.call(v.delta_per_heuristic, h) ? v.delta_per_heuristic[h] : 0;
    md += `- \`${h}\` — parent ${parentScores[h]} → child ${proposed[h]} (delta ${l8SignedInt(d)})\n`;
  }
  md += `\n## Re-audit reasoning\n${reasoningText}\n\n`;
  md += `## Verifier verdict\n${v.reason}\n`;
  const ref = l8ArtifactRef(artifactsDir, cluster.cluster_id, nextIndex);
  return {
    ok: true,
    termination_reason: null,
    error: null,
    iteration: {
      iteration_id: l8IterationId(cluster.cluster_id, nextIndex),
      run_id: runId,
      iteration_index: nextIndex,
      parent_iteration_id: parentIteration.iteration_id,
      design_artifact_ref: ref,
      scores: { [L8_BASELINE_SKILL_ID]: proposed },
      reasoning: reasoningText,
      accepted: v.accepted,
      regression_reason: v.accepted ? null : v.reason,
      delta_per_heuristic: v.delta_per_heuristic,
      informing_review_ids: cluster.member_review_ids.slice(),
      recorded_at: l8Now(recordedAt),
    },
    new_snapshot: newSnapshot,
    verdict: v,
    verdict_raw: applied.raw,
    artifact_ref: ref,
    artifact_md: md,
    native_payload: {
      iteration_index: nextIndex,
      tweak_payload: tweak.payload,
      reaudit_payload: reaudit.payload,
      verifier,
      verdict_reason: v.reason,
    },
  };
}
