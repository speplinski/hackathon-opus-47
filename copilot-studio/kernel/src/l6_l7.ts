// L6 priority-weight and L7 design-decide — ports of
// src/auditable_design/layers/l6_weight.py and l7_decide.py.
//
// L6 calls the model 2 or 3 times per cluster (third pass only when the
// first two parsed and drift by more than 1 on some dimension); the
// kernel parses each pass and aggregates. L7 calls the model once; the
// kernel cross-checks citations and builds the principle and decision.

import { escapeXml, extractJsonObjectText, fmt2 } from "./common";
import { DesignDecision, InsightCluster, PriorityScore, ReconciledVerdict } from "./types";

type L67Obj = { [k: string]: unknown };

function l67IsObj(v: unknown): v is L67Obj {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function l67IsNonEmptyStr(v: unknown): v is string {
  return typeof v === "string" && v.trim().length > 0;
}

function l67Sorted(xs: string[]): string[] {
  return xs.slice().sort();
}

function l67Repr(v: unknown): string {
  return typeof v === "string" ? `'${v}'` : JSON.stringify(v);
}

class L67ParseError extends Error {}

function l67Fail(msg: string): never {
  throw new L67ParseError(msg);
}

// Plain json.loads of the greedy {...} span (L6/L7 have no repair pass).
function l67ParseObject(text: string): L67Obj {
  const raw = extractJsonObjectText(text);
  if (raw === null) l67Fail("no JSON object found in response");
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    l67Fail(`malformed JSON: ${String((e as Error).message)}`);
  }
  if (!l67IsObj(data)) l67Fail("expected JSON object");
  return data;
}

function l67CheckKeys(obj: L67Obj, expected: string[], where: string): void {
  const keys = Object.keys(obj);
  const missing = expected.filter((k: string): boolean => keys.indexOf(k) < 0);
  if (missing.length) l67Fail(`${where} missing keys: ${JSON.stringify(l67Sorted(missing))}`);
  const extra = keys.filter((k: string): boolean => expected.indexOf(k) < 0);
  if (extra.length) l67Fail(`${where} unexpected keys: ${JSON.stringify(l67Sorted(extra))}`);
}

// Shared <cluster> preamble pieces.
function l67OptionalClusterParts(cluster: InsightCluster, parts: string[]): void {
  if (cluster.ui_context !== undefined && cluster.ui_context !== null) {
    parts.push(`  <ui_context>${escapeXml(cluster.ui_context)}</ui_context>`);
  }
  if (cluster.html !== undefined && cluster.html !== null) {
    parts.push(`  <html><![CDATA[\n${cluster.html}\n]]></html>`);
  }
  if (cluster.screenshot_ref !== undefined && cluster.screenshot_ref !== null) {
    parts.push(`  <screenshot_ref>${escapeXml(cluster.screenshot_ref)}</screenshot_ref>`);
  }
  cluster.representative_quotes.forEach((q: string, i: number): void => {
    parts.push(`  <q idx="${i}">${escapeXml(q)}</q>`);
  });
  parts.push("</cluster>");
}

function l67ReconciledParts(reconciled: ReconciledVerdict, parts: string[]): void {
  parts.push("<reconciled_verdict>");
  parts.push(`  <cluster_id>${escapeXml(reconciled.cluster_id)}</cluster_id>`);
  parts.push("  <ranked_violations>");
  reconciled.ranked_violations.forEach((v, i: number): void => {
    parts.push(
      `    <entry idx="${i}" heuristic="${escapeXml(v.heuristic)}" ` +
        `severity="${v.severity}">` +
        `${escapeXml(v.violation)} ` +
        `[reasoning: ${escapeXml(v.reasoning)}]` +
        `</entry>`
    );
  });
  parts.push("  </ranked_violations>");
  parts.push("  <tensions>");
  reconciled.tensions.forEach((t, i: number): void => {
    parts.push(
      `    <tension idx="${i}" skill_a="${t.skill_a}" skill_b="${t.skill_b}" ` +
        `axis="${escapeXml(t.axis)}">` +
        `${escapeXml(t.resolution)}` +
        `</tension>`
    );
  });
  parts.push("  </tensions>");
  parts.push("</reconciled_verdict>");
}

// ===========================================================================
// L6 priority-weight
// ===========================================================================

export const L6_SKILL_ID = "priority-weight";
// Canonical order; Python iterates a frozenset (hash order), so key
// order of its dicts is not stable and only values are compared.
export const L6_DIMENSION_KEYS: string[] = ["severity", "reach", "persistence", "business_impact", "cognitive_cost"];
export const L6_DEFAULT_META_WEIGHTS: { [k: string]: number } = {
  severity: 0.2,
  reach: 0.2,
  persistence: 0.2,
  business_impact: 0.2,
  cognitive_cost: 0.2,
};
export const L6_MAX_DIMENSION_DELTA = 1;

export interface L6Pass {
  pass: number;
  status: "parsed" | "fallback";
  payload?: L67Obj;
  reason?: string;
}

export interface L6Outcome {
  cluster_id: string;
  status: "scored" | "fallback" | "needs_third_pass";
  reason: string | null;
  priority: PriorityScore | null;
  passes: L6Pass[];
}

export function l6BuildUserMessage(cluster: InsightCluster, reconciled: ReconciledVerdict): string {
  const parts: string[] = [
    "<cluster>",
    `  <cluster_id>${escapeXml(cluster.cluster_id)}</cluster_id>`,
    `  <label>${escapeXml(cluster.label)}</label>`,
    `  <member_review_ids_count>${cluster.member_review_ids.length}</member_review_ids_count>`,
  ];
  l67OptionalClusterParts(cluster, parts);
  l67ReconciledParts(reconciled, parts);
  return parts.join("\n");
}

// parse_priority_response
export function l6ParsePriorityResponse(text: string): L67Obj {
  const data = l67ParseObject(text);
  l67CheckKeys(data, ["dimensions", "rationale", "overall_note"], "top-level");
  const dims = data["dimensions"];
  if (!l67IsObj(dims)) l67Fail("'dimensions' must be dict");
  l67CheckKeys(dims, L6_DIMENSION_KEYS, "dimensions");
  for (const k of Object.keys(dims)) {
    const v = dims[k];
    if (typeof v !== "number" || !Number.isInteger(v)) l67Fail(`dimensions['${k}'] must be int`);
    if (!(v >= 0 && v <= 10)) l67Fail(`dimensions['${k}']=${v} out of [0, 10]`);
  }
  const rationale = data["rationale"];
  if (!l67IsObj(rationale)) l67Fail("'rationale' must be dict");
  l67CheckKeys(rationale, L6_DIMENSION_KEYS, "rationale");
  for (const k of Object.keys(rationale)) {
    if (!l67IsNonEmptyStr(rationale[k])) l67Fail(`rationale['${k}'] must be non-empty str`);
  }
  if (!l67IsNonEmptyStr(data["overall_note"])) l67Fail("'overall_note' must be non-empty str");
  return data;
}

// Python round(): half to even.
function l6RoundHalfEven(x: number): number {
  const f = Math.floor(x);
  const diff = x - f;
  if (diff > 0.5) return f + 1;
  if (diff < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

export function l6NeedsThirdPass(a: { [k: string]: number }, b: { [k: string]: number }): boolean {
  return L6_DIMENSION_KEYS.some((d: string): boolean => Math.abs(a[d] - b[d]) > L6_MAX_DIMENSION_DELTA);
}

// _aggregate_passes
export function l6AggregatePasses(passes: { [k: string]: number }[]): { dims: { [k: string]: number }; delta: number } {
  const dims: { [k: string]: number } = {};
  if (passes.length === 2) {
    let maxDelta = 0;
    for (const d of L6_DIMENSION_KEYS) {
      maxDelta = Math.max(maxDelta, Math.abs(passes[0][d] - passes[1][d]));
      dims[d] = l6RoundHalfEven((passes[0][d] + passes[1][d]) / 2);
    }
    return { dims, delta: maxDelta };
  }
  if (passes.length === 3) {
    let maxDelta = 0;
    for (const d of L6_DIMENSION_KEYS) {
      const vals = [passes[0][d], passes[1][d], passes[2][d]].sort((x: number, y: number): number => x - y);
      maxDelta = Math.max(maxDelta, vals[2] - vals[0]);
      dims[d] = vals[1];
    }
    return { dims, delta: maxDelta };
  }
  throw new Error(`unexpected pass count ${passes.length} (must be 2 or 3)`);
}

export function l6WeightedTotal(dims: { [k: string]: number }, weights: { [k: string]: number }): number {
  let s = 0;
  for (const d of L6_DIMENSION_KEYS) s += dims[d] * weights[d];
  return s;
}

function l6Priority(
  clusterId: string,
  dims: { [k: string]: number },
  weights: { [k: string]: number },
  total: number,
  passes: number,
  delta: number
): PriorityScore {
  // Keys in sorted order: Python persists PriorityScore with
  // sort_keys=True and every later layer (e.g. the brief's priority
  // table) reads it back in that order.
  const orderedDims: { [k: string]: number } = {};
  const orderedWeights: { [k: string]: number } = {};
  for (const d of L6_DIMENSION_KEYS.slice().sort()) {
    orderedDims[d] = dims[d];
    orderedWeights[d] = weights[d];
  }
  return {
    cluster_id: clusterId,
    dimensions: orderedDims,
    meta_weights: orderedWeights,
    weighted_total: total,
    validation_passes: passes,
    validation_delta: delta,
  };
}

function l6ParsePass(text: string, n: number): L6Pass {
  try {
    return { pass: n, status: "parsed", payload: l6ParsePriorityResponse(text) };
  } catch (e) {
    if (!(e instanceof L67ParseError)) throw e;
    return { pass: n, status: "fallback", reason: e.message };
  }
}

// score_cluster minus the model calls. `responseTexts` holds the raw
// responses of passes 1, 2 (and 3 if the flow already ran it). When a
// third pass is needed but not supplied, status is "needs_third_pass"
// and the flow must call the prompt once more and re-run this op.
export function l6Score(
  clusterId: string,
  responseTexts: string[],
  metaWeights: { [k: string]: number } | null
): L6Outcome {
  const weights = metaWeights !== null ? metaWeights : L6_DEFAULT_META_WEIGHTS;
  if (responseTexts.length < 2) throw new Error("l6_score needs the responses of passes 1 and 2");
  const passes: L6Pass[] = [l6ParsePass(responseTexts[0], 1), l6ParsePass(responseTexts[1], 2)];
  const parsed = passes.filter((p: L6Pass): boolean => p.status === "parsed").map((p: L6Pass): L67Obj => p.payload as L67Obj);
  const zero: { [k: string]: number } = {};
  for (const d of L6_DIMENSION_KEYS) zero[d] = 0;

  if (parsed.length === 0) {
    return {
      cluster_id: clusterId,
      status: "fallback",
      reason: "both passes failed to parse",
      priority: l6Priority(clusterId, zero, weights, 0.0, 2, 0.0),
      passes,
    };
  }
  if (parsed.length === 1) {
    const dims = parsed[0]["dimensions"] as { [k: string]: number };
    return {
      cluster_id: clusterId,
      status: "fallback",
      reason: "only one of two passes parsed — no validation comparison",
      priority: l6Priority(clusterId, dims, weights, l6WeightedTotal(dims, weights), 2, 0.0),
      passes,
    };
  }
  const dimLists = parsed.map((p: L67Obj): { [k: string]: number } => p["dimensions"] as { [k: string]: number });
  if (l6NeedsThirdPass(dimLists[0], dimLists[1])) {
    if (responseTexts.length < 3) {
      return { cluster_id: clusterId, status: "needs_third_pass", reason: "passes 1 and 2 drift > 1", priority: null, passes };
    }
    const p3 = l6ParsePass(responseTexts[2], 3);
    passes.push(p3);
    if (p3.status === "parsed") dimLists.push((p3.payload as L67Obj)["dimensions"] as { [k: string]: number });
  }
  const agg = l6AggregatePasses(dimLists);
  return {
    cluster_id: clusterId,
    status: "scored",
    reason: null,
    priority: l6Priority(clusterId, agg.dims, weights, l6WeightedTotal(agg.dims, weights), dimLists.length, agg.delta),
    passes,
  };
}

// ===========================================================================
// L7 design-decide
// ===========================================================================

export const L7_SKILL_ID = "design-decide";

export interface DesignPrinciple {
  principle_id: string;
  cluster_id: string;
  name: string;
  statement: string;
  derived_from_review_ids: string[];
}

export interface L7Outcome {
  cluster_id: string;
  status: "decided" | "fallback";
  reason: string | null;
  principle: DesignPrinciple | null;
  decision: DesignDecision | null;
  native_payload: L67Obj;
}

export function l7BuildUserMessage(cluster: InsightCluster, reconciled: ReconciledVerdict, priority: PriorityScore): string {
  const parts: string[] = [
    "<cluster>",
    `  <cluster_id>${escapeXml(cluster.cluster_id)}</cluster_id>`,
    `  <label>${escapeXml(cluster.label)}</label>`,
    `  <member_review_ids>${escapeXml(cluster.member_review_ids.join(" "))}</member_review_ids>`,
  ];
  l67OptionalClusterParts(cluster, parts);
  l67ReconciledParts(reconciled, parts);
  const d = priority.dimensions;
  parts.push("<priority_score>");
  parts.push(
    `  <dimensions>` +
      `severity=${d["severity"]} ` +
      `reach=${d["reach"]} ` +
      `persistence=${d["persistence"]} ` +
      `business_impact=${d["business_impact"]} ` +
      `cognitive_cost=${d["cognitive_cost"]}` +
      `</dimensions>`
  );
  parts.push(`  <weighted_total>${fmt2(priority.weighted_total)}</weighted_total>`);
  parts.push("</priority_score>");
  return parts.join("\n");
}

// parse_decide_response (+ _validate_principle, _validate_decision)
export function l7ParseDecideResponse(text: string, cluster: InsightCluster, reconciled: ReconciledVerdict): L67Obj {
  const data = l67ParseObject(text);
  l67CheckKeys(data, ["principle", "decision"], "top-level");

  const p = data["principle"];
  if (!l67IsObj(p)) l67Fail("'principle' must be dict");
  l67CheckKeys(p, ["name", "statement", "derived_from_review_ids"], "principle");
  for (const k of ["name", "statement"]) {
    if (!l67IsNonEmptyStr(p[k])) l67Fail(`principle.${k} must be non-empty str`);
  }
  const rids = p["derived_from_review_ids"];
  if (!Array.isArray(rids) || rids.length === 0) l67Fail("principle.derived_from_review_ids must be non-empty list");
  const members = cluster.member_review_ids;
  rids.forEach((rid: unknown, i: number): void => {
    if (!l67IsNonEmptyStr(rid)) l67Fail(`principle.derived_from_review_ids[${i}] must be non-empty str`);
    if (members.indexOf(rid) < 0) {
      l67Fail(
        `principle.derived_from_review_ids[${i}]=${l67Repr(rid)} not in cluster.member_review_ids (size=${
          members.filter((x: string, k: number): boolean => members.indexOf(x) === k).length
        }) — hallucinated citation`
      );
    }
  });

  const dec = data["decision"];
  if (!l67IsObj(dec)) l67Fail("'decision' must be dict");
  l67CheckKeys(dec, ["description", "before_snapshot", "after_snapshot", "resolves_heuristics"], "decision");
  for (const k of ["description", "before_snapshot", "after_snapshot"]) {
    if (!l67IsNonEmptyStr(dec[k])) l67Fail(`decision.${k} must be non-empty str`);
  }
  const hs = dec["resolves_heuristics"];
  if (!Array.isArray(hs) || hs.length === 0) l67Fail("decision.resolves_heuristics must be non-empty list");
  const allowed = reconciled.ranked_violations.map((v): string => v.heuristic);
  hs.forEach((h: unknown, i: number): void => {
    if (!l67IsNonEmptyStr(h)) l67Fail(`decision.resolves_heuristics[${i}] must be non-empty str`);
    if (allowed.indexOf(h) < 0) {
      l67Fail(
        `decision.resolves_heuristics[${i}]=${l67Repr(h)} not in reconciled.ranked_violations heuristics — invented slug`
      );
    }
  });
  return data;
}

export function l7PrincipleId(clusterId: string): string {
  return `principle__${clusterId}`;
}

export function l7DecisionId(clusterId: string, idx = 1): string {
  return `decision__${clusterId}__${idx}`;
}

// decide_cluster minus the model call.
export function l7Decide(
  cluster: InsightCluster,
  reconciled: ReconciledVerdict,
  responseText: string
): L7Outcome {
  try {
    const payload = l7ParseDecideResponse(responseText, cluster, reconciled);
    const p = payload["principle"] as L67Obj;
    const d = payload["decision"] as L67Obj;
    return {
      cluster_id: cluster.cluster_id,
      status: "decided",
      reason: null,
      principle: {
        principle_id: l7PrincipleId(cluster.cluster_id),
        cluster_id: cluster.cluster_id,
        name: p["name"] as string,
        statement: p["statement"] as string,
        derived_from_review_ids: (p["derived_from_review_ids"] as string[]).slice(),
      },
      decision: {
        decision_id: l7DecisionId(cluster.cluster_id),
        principle_id: l7PrincipleId(cluster.cluster_id),
        description: d["description"] as string,
        before_snapshot: d["before_snapshot"] as string,
        after_snapshot: d["after_snapshot"] as string,
        resolves_heuristics: (d["resolves_heuristics"] as string[]).slice(),
      },
      native_payload: payload,
    };
  } catch (e) {
    if (!(e instanceof L67ParseError)) throw e;
    return {
      cluster_id: cluster.cluster_id,
      status: "fallback",
      reason: e.message,
      principle: null,
      decision: null,
      native_payload: { fallback: true, reason: e.message, raw_response: responseText },
    };
  }
}
