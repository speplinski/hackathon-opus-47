// L5 reconcile — port of src/auditable_design/layers/l5_reconcile.py.
//
// The model (skill "sot-reconcile") returns {summary, graph}. Everything
// else here is deterministic: the prompt envelope, the validation of the
// graph against the L4 bundle, the derivation of the flat lists
// (ranked_violations / tensions / gaps), the ReconciledVerdict and the
// evidence-review-id union over the L4 findings each ranked entry
// collapses.

import { escapeXml, parseModelJson } from "./common";
import { AuditVerdict, HeuristicViolation, InsightCluster, ReconciledVerdict, SkillTension } from "./types";

export const L5_SKILL_ID = "sot-reconcile";

export const L5_VALID_L4_SKILLS: string[] = [
  "audit-usability-fundamentals",
  "audit-accessibility",
  "audit-decision-psychology",
  "audit-business-alignment",
  "audit-interaction-design",
  "audit-ux-architecture",
];

export const L5_SKILL_TO_FRAME: { [skill: string]: string } = {
  "audit-usability-fundamentals": "fundamentals",
  "audit-accessibility": "accessibility",
  "audit-decision-psychology": "decision",
  "audit-business-alignment": "business",
  "audit-interaction-design": "interaction",
  "audit-ux-architecture": "architecture",
};

// Canonical tie-break order used when rendering <verdicts>.
export const L5_CANONICAL_ORDER: string[] = [
  "audit-business-alignment",
  "audit-decision-psychology",
  "audit-accessibility",
  "audit-usability-fundamentals",
  "audit-interaction-design",
  "audit-ux-architecture",
];

const L5_VALID_NODE_TYPES: string[] = ["violation", "corroboration", "contradiction", "tension", "gap"];
const L5_VALID_RELATION_TYPES: string[] = ["corroborates", "contradicts", "in_tension_with", "elaborates"];
export const L5_VALID_TENSION_AXES: string[] = [
  "user_control_vs_platform_norms",
  "efficiency_vs_safety",
  "conversion_vs_user_wellbeing",
  "discoverability_vs_density",
  "principled_accretion_vs_featuritis",
  "idiom_vs_metaphor",
  "system1_ease_vs_system2_deliberation",
];
const L5_VALID_EVIDENCE_SOURCES: string[] = ["quotes", "ui_context", "html", "screenshot"];
const L5_TOP_LEVEL_KEYS: string[] = ["summary", "graph"];
const L5_LEGACY_TOP_LEVEL: string[] = ["ranked_violations", "tensions", "gaps"];
const L5_NODE_KEYS: string[] = [
  "id",
  "type",
  "label",
  "rationale",
  "confidence",
  "source_skill",
  "source_heuristic",
  "source_severity_anchored",
  "source_finding_idx",
  "member_ids",
  "skill_a",
  "skill_b",
  "axis",
  "resolution",
  "evidence_source",
  "evidence_quote_idxs",
  "why_missed",
];
const L5_EDGE_KEYS: string[] = ["source", "target", "type"];

type L5Obj = { [k: string]: unknown };

export interface L5Bundle {
  cluster_id: string;
  verdicts_by_skill: { [skill: string]: AuditVerdict };
}

export interface L5FindingRef {
  skill: string;
  finding_idx: number;
}

export interface L5RankedEntry {
  heuristic: string;
  violation: string;
  severity: number;
  source_skills: string[];
  rationale: string;
  source_findings: L5FindingRef[];
  corroboration_count: number;
  unique_frames: number;
  rank_score: number;
}

export interface L5GapEntry {
  rationale: string;
  evidence_source: string[];
  evidence_quote_idxs: number[];
  why_missed: string;
}

export interface L5Outcome {
  cluster_id: string;
  status: "audited" | "fallback";
  reason: string | null;
  verdict: ReconciledVerdict;
  native_payload: L5Obj;
}

class L5ParseError extends Error {}

function l5Fail(msg: string): never {
  throw new L5ParseError(msg);
}

function l5IsObj(v: unknown): v is L5Obj {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function l5IsNonEmptyStr(v: unknown): v is string {
  return typeof v === "string" && v.trim().length > 0;
}

function l5IsInt(v: unknown): v is number {
  return typeof v === "number" && Number.isInteger(v);
}

function l5Sorted(xs: string[]): string[] {
  return xs.slice().sort();
}

function l5Repr(v: unknown): string {
  return typeof v === "string" ? `'${v}'` : JSON.stringify(v);
}

// Python str.strip(chars)
function l5StripChars(s: string, chars: string): string {
  let a = 0;
  let b = s.length;
  while (a < b && chars.indexOf(s[a]) >= 0) a++;
  while (b > a && chars.indexOf(s[b - 1]) >= 0) b--;
  return s.slice(a, b);
}

function l5CodePointLength(s: string): number {
  let n = 0;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) i++;
    n++;
  }
  return n;
}

// Python `str(x or y or z)` over JSON values.
function l5Truthy(v: unknown): boolean {
  if (v === null || v === undefined || v === false || v === 0 || v === "") return false;
  if (Array.isArray(v)) return v.length > 0;
  if (l5IsObj(v)) return Object.keys(v).length > 0;
  return true;
}

function l5PyStr(v: unknown): string {
  if (typeof v === "string") return v;
  if (v === null || v === undefined) return "None";
  if (v === true) return "True";
  if (v === false) return "False";
  return String(v);
}

// ---------------------------------------------------------------------------
// Bundle
// ---------------------------------------------------------------------------

// load_verdicts_bundle, restricted to one cluster. Later rows win on a
// duplicate (cluster, skill) pair, as in Python.
export function l5BuildBundle(clusterId: string, verdicts: AuditVerdict[]): L5Bundle {
  const bySkill: { [skill: string]: AuditVerdict } = {};
  for (let i = 0; i < verdicts.length; i++) {
    const v = verdicts[i];
    if (v.cluster_id !== clusterId) continue;
    if (L5_VALID_L4_SKILLS.indexOf(v.skill_id) < 0) {
      throw new Error(`l5_reconcile: verdict ${i} has unknown skill_id=${l5Repr(v.skill_id)}`);
    }
    bySkill[v.skill_id] = v;
  }
  return { cluster_id: clusterId, verdicts_by_skill: bySkill };
}

// ---------------------------------------------------------------------------
// Prompt build
// ---------------------------------------------------------------------------

export function l5BuildUserMessage(cluster: InsightCluster, bundle: L5Bundle): string {
  const parts: string[] = [
    "<cluster>",
    `  <cluster_id>${escapeXml(cluster.cluster_id)}</cluster_id>`,
    `  <label>${escapeXml(cluster.label)}</label>`,
  ];
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

  parts.push("<verdicts>");
  for (const skillId of L5_CANONICAL_ORDER) {
    const verdict = bundle.verdicts_by_skill[skillId];
    if (verdict === undefined) continue;
    parts.push(`  <verdict skill="${skillId}">`);
    verdict.relevant_heuristics.forEach((f: HeuristicViolation, idx: number): void => {
      parts.push(
        `    <finding idx="${idx}" heuristic="${escapeXml(f.heuristic)}" ` +
          `severity="${f.severity}">` +
          `${escapeXml(f.violation)} ` +
          `[reasoning: ${escapeXml(f.reasoning)}]` +
          `</finding>`
      );
    });
    parts.push("  </verdict>");
  }
  parts.push("</verdicts>");
  return parts.join("\n");
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function l5CheckKeys(obj: L5Obj, expected: string[], where: string): void {
  const keys = Object.keys(obj);
  const missing = expected.filter((k: string): boolean => keys.indexOf(k) < 0);
  if (missing.length) l5Fail(`${where} missing keys: ${JSON.stringify(l5Sorted(missing))}`);
  const extra = keys.filter((k: string): boolean => expected.indexOf(k) < 0);
  if (extra.length) l5Fail(`${where} unexpected keys: ${JSON.stringify(l5Sorted(extra))}`);
}

function l5ValidateSkillPair(node: L5Obj, i: number, bundle: L5Bundle, kind: string): void {
  const a = node["skill_a"];
  const b = node["skill_b"];
  if (typeof a !== "string" || L5_VALID_L4_SKILLS.indexOf(a) < 0) {
    l5Fail(`graph.nodes[${i}] ${kind}.skill_a=${l5Repr(a)} not in VALID_L4_SKILLS`);
  }
  if (typeof b !== "string" || L5_VALID_L4_SKILLS.indexOf(b) < 0) {
    l5Fail(`graph.nodes[${i}] ${kind}.skill_b=${l5Repr(b)} not in VALID_L4_SKILLS`);
  }
  if (a === b) l5Fail(`graph.nodes[${i}] ${kind}.skill_a == skill_b (${l5Repr(a)}); must differ`);
  for (const sk of [a as string, b as string]) {
    if (bundle.verdicts_by_skill[sk] === undefined) {
      l5Fail(
        `graph.nodes[${i}] ${kind} references skill ${l5Repr(sk)} not present in input bundle for cluster ${l5Repr(
          bundle.cluster_id
        )}`
      );
    }
  }
}

function l5ValidateViolationNode(node: L5Obj, i: number, bundle: L5Bundle): void {
  const ss = node["source_skill"];
  if (typeof ss !== "string" || L5_VALID_L4_SKILLS.indexOf(ss) < 0) {
    l5Fail(`graph.nodes[${i}] violation.source_skill=${l5Repr(ss)} not in VALID_L4_SKILLS`);
  }
  const verdict = bundle.verdicts_by_skill[ss as string];
  if (verdict === undefined) {
    l5Fail(
      `graph.nodes[${i}] violation.source_skill=${l5Repr(ss)} not present in input bundle for cluster ${l5Repr(
        bundle.cluster_id
      )}`
    );
  }
  const idx = node["source_finding_idx"];
  if (!l5IsInt(idx)) l5Fail(`graph.nodes[${i}] violation.source_finding_idx must be int`);
  const n = verdict.relevant_heuristics.length;
  if (!(idx >= 0 && idx < n)) {
    l5Fail(`graph.nodes[${i}] violation.source_finding_idx=${idx} out of [0, ${n}) for skill=${l5Repr(ss)}`);
  }
  const finding = verdict.relevant_heuristics[idx];
  if (node["source_heuristic"] !== finding.heuristic) {
    l5Fail(
      `graph.nodes[${i}] violation.source_heuristic=${l5Repr(node["source_heuristic"])} does not match finding ${idx} of skill ${l5Repr(
        ss
      )} (=${l5Repr(finding.heuristic)})`
    );
  }
  const sev = node["source_severity_anchored"];
  if (!l5IsInt(sev)) l5Fail(`graph.nodes[${i}] violation.source_severity_anchored must be int`);
  if (sev !== finding.severity) {
    l5Fail(
      `graph.nodes[${i}] violation.source_severity_anchored=${sev} does not match source finding severity=${finding.severity}`
    );
  }
}

function l5ValidateGapNode(node: L5Obj, i: number, nQuotes: number): void {
  const evs = node["evidence_source"];
  if (!Array.isArray(evs)) l5Fail(`graph.nodes[${i}] gap.evidence_source must be list`);
  if (evs.length === 0) l5Fail(`graph.nodes[${i}] gap.evidence_source must be non-empty`);
  evs.forEach((src: unknown, j: number): void => {
    if (typeof src !== "string") l5Fail(`graph.nodes[${i}] gap.evidence_source[${j}] must be str`);
    if (L5_VALID_EVIDENCE_SOURCES.indexOf(src) < 0) {
      l5Fail(`graph.nodes[${i}] gap.evidence_source[${j}]=${l5Repr(src)} not in ${JSON.stringify(l5Sorted(L5_VALID_EVIDENCE_SOURCES))}`);
    }
  });
  const uniq = evs.filter((x: unknown, k: number): boolean => evs.indexOf(x) === k);
  if (uniq.length !== evs.length) l5Fail(`graph.nodes[${i}] gap.evidence_source has duplicates`);

  const idxs = node["evidence_quote_idxs"];
  if (!Array.isArray(idxs)) l5Fail(`graph.nodes[${i}] gap.evidence_quote_idxs must be list`);
  const hasQuotes = evs.indexOf("quotes") >= 0;
  if (hasQuotes && idxs.length === 0) {
    l5Fail(`graph.nodes[${i}] gap: 'quotes' in evidence_source but evidence_quote_idxs empty (bidirectional rule)`);
  }
  if (idxs.length > 0 && !hasQuotes) {
    l5Fail(`graph.nodes[${i}] gap: evidence_quote_idxs non-empty but 'quotes' not in evidence_source (bidirectional rule)`);
  }
  idxs.forEach((idx: unknown, j: number): void => {
    if (!l5IsInt(idx)) l5Fail(`graph.nodes[${i}] gap.evidence_quote_idxs[${j}] must be int`);
    if (!(idx >= 0 && idx < nQuotes)) {
      l5Fail(`graph.nodes[${i}] gap.evidence_quote_idxs[${j}]=${idx} out of [0, ${nQuotes})`);
    }
  });
  if (!l5IsNonEmptyStr(node["why_missed"])) l5Fail(`graph.nodes[${i}] gap.why_missed must be non-empty str`);
}

function l5ValidateNode(node: unknown, i: number, bundle: L5Bundle, nQuotes: number, warnings: string[]): void {
  if (!l5IsObj(node)) l5Fail(`graph.nodes[${i}] must be dict`);
  l5CheckKeys(node, L5_NODE_KEYS, `graph.nodes[${i}]`);
  if (!l5IsNonEmptyStr(node["id"])) l5Fail(`graph.nodes[${i}].id must be non-empty str`);
  const ntype = node["type"];
  if (typeof ntype !== "string" || L5_VALID_NODE_TYPES.indexOf(ntype) < 0) {
    l5Fail(`graph.nodes[${i}].type=${l5Repr(ntype)} not in ${JSON.stringify(l5Sorted(L5_VALID_NODE_TYPES))}`);
  }
  for (const k of ["label", "rationale"]) {
    if (!l5IsNonEmptyStr(node[k])) l5Fail(`graph.nodes[${i}].${k} must be non-empty str`);
  }
  const conf = node["confidence"];
  if (typeof conf !== "number") l5Fail(`graph.nodes[${i}].confidence must be number`);
  if (!(conf >= 0.0 && conf <= 1.0)) l5Fail(`graph.nodes[${i}].confidence=${conf} out of [0.0, 1.0]`);

  if (ntype === "violation") {
    l5ValidateViolationNode(node, i, bundle);
  } else if (ntype === "corroboration") {
    const mIds = node["member_ids"];
    if (!Array.isArray(mIds)) l5Fail(`graph.nodes[${i}] corroboration.member_ids must be list`);
    if (mIds.length < 2) l5Fail(`graph.nodes[${i}] corroboration.member_ids must have ≥ 2 entries (SKILL.md rule)`);
    mIds.forEach((mid: unknown, j: number): void => {
      if (typeof mid !== "string") l5Fail(`graph.nodes[${i}].member_ids[${j}] must be str`);
    });
  } else if (ntype === "contradiction") {
    l5ValidateSkillPair(node, i, bundle, "contradiction");
    if (!l5IsNonEmptyStr(node["resolution"])) l5Fail(`graph.nodes[${i}] contradiction.resolution must be non-empty str`);
  } else if (ntype === "tension") {
    l5ValidateSkillPair(node, i, bundle, "tension");
    const axis = node["axis"];
    if (!l5IsNonEmptyStr(axis)) l5Fail(`graph.nodes[${i}] tension.axis must be non-empty str`);
    if (L5_VALID_TENSION_AXES.indexOf(axis) < 0) {
      // Soft close, as in Python: flag, do not reject.
      warnings.push(`graph.nodes[${i}] tension.axis=${l5Repr(axis)} is not in the closed set`);
    }
    if (!l5IsNonEmptyStr(node["resolution"])) l5Fail(`graph.nodes[${i}] tension.resolution must be non-empty str`);
  } else if (ntype === "gap") {
    l5ValidateGapNode(node, i, nQuotes);
  }
}

function l5ValidateEdge(edge: unknown, i: number, nodeIds: string[]): void {
  if (!l5IsObj(edge)) l5Fail(`graph.edges[${i}] must be dict`);
  l5CheckKeys(edge, L5_EDGE_KEYS, `graph.edges[${i}]`);
  for (const endpoint of ["source", "target"]) {
    const v = edge[endpoint];
    if (typeof v !== "string" || nodeIds.indexOf(v) < 0) {
      l5Fail(`graph.edges[${i}].${endpoint}=${l5Repr(v)} not a known node id`);
    }
  }
  const t = edge["type"];
  if (typeof t !== "string" || L5_VALID_RELATION_TYPES.indexOf(t) < 0) {
    l5Fail(`graph.edges[${i}].type=${l5Repr(t)} not in ${JSON.stringify(l5Sorted(L5_VALID_RELATION_TYPES))}`);
  }
}

// ---------------------------------------------------------------------------
// Flat-list derivation (_derive_flat_lists_from_graph)
// ---------------------------------------------------------------------------

function l5FindingRef(node: L5Obj): L5FindingRef {
  return { skill: node["source_skill"] as string, finding_idx: Math.trunc(node["source_finding_idx"] as number) };
}

export function l5DeriveFlatLists(
  nodes: L5Obj[],
  tensionNodes: L5Obj[],
  gapNodes: L5Obj[]
): { ranked: L5RankedEntry[]; tensions: SkillTension[]; gaps: L5GapEntry[] } {
  const nodesById: { [id: string]: L5Obj } = {};
  for (const n of nodes) nodesById[n["id"] as string] = n;
  const violations = nodes.filter((n: L5Obj): boolean => n["type"] === "violation");
  const corroborations = nodes.filter((n: L5Obj): boolean => n["type"] === "corroboration");

  const vidToCorr: { [vid: string]: string } = {};
  for (const c of corroborations) {
    const mids = Array.isArray(c["member_ids"]) ? (c["member_ids"] as string[]) : [];
    for (const vid of mids) {
      if (nodesById[vid] !== undefined && vidToCorr[vid] === undefined) vidToCorr[vid] = c["id"] as string;
    }
  }

  const ranked: L5RankedEntry[] = [];

  for (const c of corroborations) {
    const mids = Array.isArray(c["member_ids"]) ? (c["member_ids"] as string[]) : [];
    const members = mids
      .filter((vid: string): boolean => nodesById[vid] !== undefined && nodesById[vid]["type"] === "violation")
      .map((vid: string): L5Obj => nodesById[vid]);
    if (members.length === 0) continue;
    const severity = Math.max(...members.map((m: L5Obj): number => Math.trunc(m["source_severity_anchored"] as number)));
    const sourceSkills = l5Sorted(
      members
        .map((m: L5Obj): string => m["source_skill"] as string)
        .filter((s: string, k: number, arr: string[]): boolean => arr.indexOf(s) === k)
    );
    const label = l5PyStr(l5Truthy(c["label"]) ? c["label"] : "").trim();
    let heuristic: string;
    if (label && l5CodePointLength(label) <= 80 && l5StripChars(label, "_ ").indexOf(" ") < 0) {
      heuristic = label;
    } else {
      const slugs = l5Sorted(
        members
          .map((m: L5Obj): string => m["source_heuristic"] as string)
          .filter((s: string, k: number, arr: string[]): boolean => arr.indexOf(s) === k)
      );
      heuristic = slugs.length <= 3 ? slugs.join("__") : `${slugs[0]}__corroborated`;
    }
    const violationText = l5PyStr(
      l5Truthy(c["rationale"]) ? c["rationale"] : label ? label : "Corroborated defect across multiple skills."
    );
    const cRationale = c["rationale"] === undefined ? "" : l5PyStr(c["rationale"]);
    const rationale = (
      `Corroboration across ${sourceSkills.length} skill(s) ` + `(${sourceSkills.join(", ")}): ${cRationale.trim() || label}`
    ).trim();
    ranked.push({
      heuristic,
      violation: violationText,
      severity,
      source_skills: sourceSkills,
      rationale,
      source_findings: members.map((m: L5Obj): L5FindingRef => l5FindingRef(m)),
      corroboration_count: 0,
      unique_frames: 0,
      rank_score: 0,
    });
  }

  for (const v of violations) {
    if (vidToCorr[v["id"] as string] !== undefined) continue;
    const sh = v["source_heuristic"] as string;
    const raw = l5Truthy(v["rationale"]) ? v["rationale"] : l5Truthy(v["label"]) ? v["label"] : sh;
    const violationText = l5PyStr(raw).trim() || sh;
    ranked.push({
      heuristic: sh,
      violation: violationText,
      severity: Math.trunc(v["source_severity_anchored"] as number),
      source_skills: [v["source_skill"] as string],
      rationale:
        `Solitary ${l5PyStr(v["source_skill"])} finding ` +
        `(severity ${l5PyStr(v["source_severity_anchored"])}); ` +
        `no cross-skill corroboration surfaced for this defect.`,
      source_findings: [l5FindingRef(v)],
      corroboration_count: 0,
      unique_frames: 0,
      rank_score: 0,
    });
  }

  for (const e of ranked) {
    e.corroboration_count = e.source_skills.length;
    const frames: string[] = [];
    for (const s of e.source_skills) {
      const f = L5_SKILL_TO_FRAME[s];
      if (frames.indexOf(f) < 0) frames.push(f);
    }
    e.unique_frames = frames.length;
    e.rank_score = e.severity * e.corroboration_count;
  }
  // Array.prototype.sort is stable (ES2019), like Python's sort.
  ranked.sort(
    (a: L5RankedEntry, b: L5RankedEntry): number =>
      b.rank_score - a.rank_score || b.unique_frames - a.unique_frames || b.severity - a.severity
  );

  const tensions: SkillTension[] = tensionNodes.map(
    (t: L5Obj): SkillTension => ({
      skill_a: t["skill_a"] as string,
      skill_b: t["skill_b"] as string,
      axis: t["axis"] as string,
      resolution: t["resolution"] as string,
    })
  );
  const gaps: L5GapEntry[] = gapNodes.map(
    (g: L5Obj): L5GapEntry => ({
      rationale: g["rationale"] as string,
      evidence_source: (g["evidence_source"] as string[]).slice(),
      evidence_quote_idxs: (g["evidence_quote_idxs"] as number[]).slice(),
      why_missed: g["why_missed"] as string,
    })
  );
  return { ranked, tensions, gaps };
}

// ---------------------------------------------------------------------------
// Parse (parse_reconcile_response)
// ---------------------------------------------------------------------------

export function l5ParseReconcileResponse(
  text: string,
  bundle: L5Bundle,
  nQuotes: number,
  warnings: string[]
): L5Obj {
  const parsed = parseModelJson(text);
  if (!parsed.ok || parsed.data === null) l5Fail(parsed.error || "unparseable response");
  const data = parsed.data;

  const actual = Object.keys(data);
  const missing = L5_TOP_LEVEL_KEYS.filter((k: string): boolean => actual.indexOf(k) < 0);
  if (missing.length) l5Fail(`missing required top-level keys: ${JSON.stringify(l5Sorted(missing))}`);
  const extra = actual.filter(
    (k: string): boolean => L5_TOP_LEVEL_KEYS.indexOf(k) < 0 && L5_LEGACY_TOP_LEVEL.indexOf(k) < 0
  );
  if (extra.length) l5Fail(`unexpected top-level keys: ${JSON.stringify(l5Sorted(extra))}`);
  for (const k of L5_LEGACY_TOP_LEVEL) {
    if (k in data) {
      warnings.push(`legacy top-level key '${k}' ignored — parser derives from graph`);
      delete data[k];
    }
  }

  if (!l5IsNonEmptyStr(data["summary"])) l5Fail("'summary' must be a non-empty str");
  const graph = data["graph"];
  if (!l5IsObj(graph)) l5Fail("'graph' must be dict");
  const gKeys = Object.keys(graph);
  const gMissing = ["nodes", "edges"].filter((k: string): boolean => gKeys.indexOf(k) < 0);
  if (gMissing.length) l5Fail(`graph missing keys: ${JSON.stringify(gMissing)}`);
  const gExtra = gKeys.filter((k: string): boolean => k !== "nodes" && k !== "edges");
  if (gExtra.length) l5Fail(`graph has unexpected keys: ${JSON.stringify(l5Sorted(gExtra))}`);
  const nodes = graph["nodes"];
  const edges = graph["edges"];
  if (!Array.isArray(nodes)) l5Fail("graph.nodes must be list");
  if (!Array.isArray(edges)) l5Fail("graph.edges must be list");

  const nodeIds: string[] = [];
  const violationIds: string[] = [];
  const tensionNodes: L5Obj[] = [];
  const gapNodes: L5Obj[] = [];
  nodes.forEach((node: unknown, i: number): void => {
    l5ValidateNode(node, i, bundle, nQuotes, warnings);
    const n = node as L5Obj;
    const id = n["id"] as string;
    if (nodeIds.indexOf(id) >= 0) l5Fail(`graph.nodes[${i}] duplicate id=${l5Repr(id)}`);
    nodeIds.push(id);
    if (n["type"] === "violation") violationIds.push(id);
    else if (n["type"] === "tension") tensionNodes.push(n);
    else if (n["type"] === "gap") gapNodes.push(n);
  });
  nodes.forEach((node: unknown, i: number): void => {
    const n = node as L5Obj;
    if (n["type"] !== "corroboration") return;
    for (const mid of n["member_ids"] as string[]) {
      if (violationIds.indexOf(mid) < 0) {
        l5Fail(`graph.nodes[${i}] corroboration.member_ids contains ${l5Repr(mid)} which is not a violation-node id`);
      }
    }
  });
  edges.forEach((edge: unknown, i: number): void => l5ValidateEdge(edge, i, nodeIds));

  const flat = l5DeriveFlatLists(nodes as L5Obj[], tensionNodes, gapNodes);
  data["ranked_violations"] = flat.ranked;
  data["tensions"] = flat.tensions;
  data["gaps"] = flat.gaps;
  return data;
}

// ---------------------------------------------------------------------------
// ReconciledVerdict construction
// ---------------------------------------------------------------------------

// evidence_review_ids_for_entry: union, in first-seen order, of the L4
// evidence_review_ids of every finding the ranked entry collapses.
export function l5EvidenceReviewIdsForEntry(entry: L5Obj, bundle: L5Bundle): string[] {
  const seen: string[] = [];
  const refs = Array.isArray(entry["source_findings"]) ? (entry["source_findings"] as L5FindingRef[]) : [];
  for (const ref of refs) {
    const verdict = bundle.verdicts_by_skill[ref.skill];
    if (verdict === undefined) continue;
    const idx = ref.finding_idx;
    if (!(idx >= 0 && idx < verdict.relevant_heuristics.length)) continue;
    for (const rid of verdict.relevant_heuristics[idx].evidence_review_ids || []) {
      if (seen.indexOf(rid) < 0) seen.push(rid);
    }
  }
  return seen;
}

export function l5BuildReconciledVerdict(payload: L5Obj, clusterId: string, bundle: L5Bundle | null): ReconciledVerdict {
  const ranked: HeuristicViolation[] = [];
  for (const raw of payload["ranked_violations"] as L5Obj[]) {
    const reasoningParts = [
      `rank_score=${l5PyStr(raw["rank_score"])} ` +
        `(severity=${l5PyStr(raw["severity"])} × ` +
        `corroboration=${l5PyStr(raw["corroboration_count"])}, ` +
        `unique_frames=${l5PyStr(raw["unique_frames"])})`,
      `skills=[${(raw["source_skills"] as string[]).join(", ")}]`,
      `rationale: ${l5PyStr(raw["rationale"])}`,
    ];
    ranked.push({
      heuristic: raw["heuristic"] as string,
      violation: raw["violation"] as string,
      severity: raw["severity"] as number,
      evidence_review_ids: bundle !== null ? l5EvidenceReviewIdsForEntry(raw, bundle) : [],
      reasoning: reasoningParts.join(" | "),
    });
  }
  const tensions: SkillTension[] = (payload["tensions"] as L5Obj[]).map(
    (t: L5Obj): SkillTension => ({
      skill_a: t["skill_a"] as string,
      skill_b: t["skill_b"] as string,
      axis: t["axis"] as string,
      resolution: t["resolution"] as string,
    })
  );
  return { cluster_id: clusterId, ranked_violations: ranked, tensions };
}

function l5EmptyVerdict(clusterId: string): ReconciledVerdict {
  return { cluster_id: clusterId, ranked_violations: [], tensions: [] };
}

// reconcile_cluster minus the model call: the flow calls the prompt
// with l5BuildUserMessage(...) and hands the raw response back here.
export function l5Reconcile(
  cluster: InsightCluster,
  verdicts: AuditVerdict[],
  responseText: string | null,
  warnings: string[]
): L5Outcome {
  const bundle = l5BuildBundle(cluster.cluster_id, verdicts);
  if (Object.keys(bundle.verdicts_by_skill).length === 0) {
    const reason = "no L4 verdicts in input bundle";
    return {
      cluster_id: cluster.cluster_id,
      status: "fallback",
      reason,
      verdict: l5EmptyVerdict(cluster.cluster_id),
      native_payload: { fallback: true, reason, raw_response: "" },
    };
  }
  const text = responseText === null ? "" : responseText;
  try {
    const payload = l5ParseReconcileResponse(text, bundle, cluster.representative_quotes.length, warnings);
    return {
      cluster_id: cluster.cluster_id,
      status: "audited",
      reason: null,
      verdict: l5BuildReconciledVerdict(payload, cluster.cluster_id, bundle),
      native_payload: payload,
    };
  } catch (e) {
    if (!(e instanceof L5ParseError)) throw e;
    const reason = e.message;
    return {
      cluster_id: cluster.cluster_id,
      status: "fallback",
      reason,
      verdict: l5EmptyVerdict(cluster.cluster_id),
      native_payload: { fallback: true, reason, raw_response: text },
    };
  }
}

// Python's _native_row for L5 (keyed by cluster_id).
export function l5NativeRow(outcome: L5Outcome): L5Obj {
  return { cluster_id: outcome.cluster_id, status: outcome.status, payload: outcome.native_payload };
}
