// L0–L3b: intake, relevance classification, complaint graphs,
// grouping and labelling. Ports of l1_classify.py, l2_structure.py and
// l3b_label.py. L3 (embedding + HDBSCAN) has no equivalent inside
// Copilot Studio, so it is replaced by an LLM grouping step whose output
// is checked here; see L3 below.

import type { ComplaintEdge, ComplaintGraph, ComplaintNode, InsightCluster, Utterance } from "./types";
import { escapeXml, parseModelJson, wrapUserText } from "./common";

// ---------------------------------------------------------------------
// L0 — intake
// ---------------------------------------------------------------------

export interface L0Item {
  id?: string | null;
  review_id?: string | null;
  text?: string | null;
  date?: string | null;
  timestamp_utc?: string | null;
  rating?: number | null;
  segment?: string | null;
  source_ref?: string | null;
}

export interface L0Source {
  source_type: string;
  items: L0Item[];
}

// FNV-1a 32-bit, hex. Used only to derive an id when the source has
// none; deterministic so a re-run assigns the same ids.
export function l0Fnv1a(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return ("00000000" + h.toString(16)).slice(-8);
}

export function l0Normalize(sources: L0Source[]): { utterances: Utterance[]; rejected: { source_type: string; index: number; reason: string }[] } {
  const utterances: Utterance[] = [];
  const rejected: { source_type: string; index: number; reason: string }[] = [];
  const seen: { [id: string]: boolean } = {};
  for (const src of sources) {
    const st = String(src.source_type || "unknown").replace(/[^A-Za-z0-9_-]/g, "_");
    src.items.forEach((item: L0Item, index: number): void => {
      const text = typeof item.text === "string" ? item.text : "";
      if (!text.trim()) {
        rejected.push({ source_type: st, index, reason: "empty text" });
        return;
      }
      let id = item.id || item.review_id || `${st}-${l0Fnv1a(st + "\u0000" + text)}`;
      id = String(id).replace(/["'<>& \t\n\r]/g, "_");
      if (seen[id]) {
        rejected.push({ source_type: st, index, reason: `duplicate id ${id}` });
        return;
      }
      seen[id] = true;
      utterances.push({
        utterance_id: id,
        source_type: st,
        text,
        date: item.date || item.timestamp_utc || null,
        segment: item.segment || null,
        rating: typeof item.rating === "number" ? item.rating : null,
        source_ref: item.source_ref || null,
      });
    });
  }
  return { utterances, rejected };
}

// ---------------------------------------------------------------------
// L1 — classification (l1_classify.py)
// ---------------------------------------------------------------------

export const L1_UX_TAGS: string[] = ["paywall", "hearts_streak", "notifications", "ads", "feature_removal", "interface_other"];
export const L1_NON_UX_TAGS: string[] = ["content_quality", "billing", "bug", "off_topic"];

// build_user_message: `rating: {rating}\n<user_review ...>`. Sources
// without a rating get "n/a" (the Python pipeline only saw store reviews).
export function l1BuildMessage(u: Utterance): string {
  const rating = typeof u.rating === "number" ? String(u.rating) : "n/a";
  return `rating: ${rating}\n${wrapUserText(u.text, u.utterance_id)}`;
}

export interface L1Result {
  utterance_id: string;
  ok: boolean;
  is_ux_relevant: boolean | null;
  classifier_confidence: number | null;
  rubric_tags: string[];
  error: string | null;
}

export function l1Parse(utteranceId: string, text: string): L1Result {
  const fail = (error: string): L1Result => ({
    utterance_id: utteranceId,
    ok: false,
    is_ux_relevant: null,
    classifier_confidence: null,
    rubric_tags: [],
    error,
  });
  const p = parseModelJson(text);
  if (!p.ok || !p.data) return fail(p.error || "parse error");
  const d = p.data;
  const keys = Object.keys(d).sort();
  const expected = ["classifier_confidence", "is_ux_relevant", "rubric_tags"];
  if (keys.join(",") !== expected.join(",")) return fail(`key set mismatch: got ${keys.join(",")}`);
  if (typeof d.is_ux_relevant !== "boolean") return fail("is_ux_relevant must be a bool");
  const conf = d.classifier_confidence;
  if (typeof conf !== "number") return fail("classifier_confidence must be a number");
  if (conf < 0 || conf > 1) return fail(`classifier_confidence out of [0, 1]: ${conf}`);
  if (!Array.isArray(d.rubric_tags)) return fail("rubric_tags must be a list");
  const vocab = L1_UX_TAGS.concat(L1_NON_UX_TAGS);
  const tags: string[] = [];
  for (const t of d.rubric_tags as unknown[]) {
    if (typeof t !== "string") return fail("rubric_tags element must be a string");
    if (vocab.indexOf(t) < 0) return fail(`rubric_tags element '${t}' not in closed vocabulary`);
    if (tags.indexOf(t) < 0) tags.push(t);
  }
  return {
    utterance_id: utteranceId,
    ok: true,
    is_ux_relevant: d.is_ux_relevant as boolean,
    classifier_confidence: conf,
    rubric_tags: tags,
    error: null,
  };
}

// ---------------------------------------------------------------------
// L2 — complaint graph (l2_structure.py)
// ---------------------------------------------------------------------

export const L2_NODE_TYPES: string[] = ["pain", "expectation", "triggered_element", "workaround", "lost_value"];
export const L2_RELATION_TYPES: string[] = ["triggers", "violates_expectation", "compensates_for", "correlates_with"];
export const L2_MIN_NODES = 3;
export const L2_MAX_NODES = 7;

export function l2BuildMessage(u: Utterance): string {
  return wrapUserText(u.text, u.utterance_id);
}

// Find the end of a balanced {...} starting at `start`, honouring JSON
// strings. Returns -1 if unbalanced.
function l2MatchBrace(text: string, start: number): number {
  let depth = 0;
  let inStr = false;
  for (let i = start; i < text.length; i++) {
    const c = text[i];
    if (inStr) {
      if (c === "\\") i++;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') inStr = true;
    else if (c === "{") depth++;
    else if (c === "}") {
      depth--;
      if (depth === 0) return i;
    }
  }
  return -1;
}

// _extract_json_objects: every top-level object, scanning left to right.
export function l2ExtractJsonObjects(text: string): { [k: string]: unknown }[] {
  const out: { [k: string]: unknown }[] = [];
  let pos = 0;
  while (pos < text.length) {
    const idx = text.indexOf("{", pos);
    if (idx < 0) break;
    const end = l2MatchBrace(text, idx);
    if (end < 0) {
      pos = idx + 1;
      continue;
    }
    try {
      const obj: unknown = JSON.parse(text.slice(idx, end + 1));
      if (obj !== null && typeof obj === "object" && !Array.isArray(obj)) out.push(obj as { [k: string]: unknown });
      pos = end + 1;
    } catch {
      pos = idx + 1;
    }
  }
  return out;
}

interface L2RawNode {
  node_id: string;
  node_type: string;
  verbatim_quote: string;
}

function l2ParseResponse(text: string): { nodes: L2RawNode[]; edges: ComplaintEdge[] } {
  const objects = l2ExtractJsonObjects(text);
  if (objects.length === 0) throw new Error("no JSON object found in response");
  const data = objects[objects.length - 1];
  const keys = Object.keys(data);
  const extra = keys.filter((k: string): boolean => k !== "nodes" && k !== "edges");
  if (extra.length) throw new Error(`unexpected top-level keys: ${extra.sort().join(",")}`);
  if (!("nodes" in data)) throw new Error("missing required top-level key: 'nodes'");
  if (!Array.isArray(data.nodes)) throw new Error("nodes must be a list");
  const rawEdges = "edges" in data ? data.edges : [];
  if (!Array.isArray(rawEdges)) throw new Error("edges must be a list");
  const nodes: L2RawNode[] = [];
  const ids: string[] = [];
  (data.nodes as unknown[]).forEach((n: unknown, i: number): void => {
    if (n === null || typeof n !== "object" || Array.isArray(n)) throw new Error(`nodes[${i}] must be an object`);
    const o = n as { [k: string]: unknown };
    const k = Object.keys(o).sort().join(",");
    if (k !== "node_id,node_type,verbatim_quote") throw new Error(`nodes[${i}] key set mismatch: got ${k}`);
    if (typeof o.node_id !== "string" || !o.node_id) throw new Error(`nodes[${i}].node_id must be a non-empty string`);
    const nid = o.node_id as string;
    if (ids.indexOf(nid) >= 0) throw new Error(`duplicate node_id '${nid}'`);
    ids.push(nid);
    if (L2_NODE_TYPES.indexOf(o.node_type as string) < 0) throw new Error(`nodes[${i}].node_type '${String(o.node_type)}' not in closed vocabulary`);
    if (typeof o.verbatim_quote !== "string" || !o.verbatim_quote) throw new Error(`nodes[${i}].verbatim_quote must be a non-empty string`);
    nodes.push({ node_id: nid, node_type: o.node_type as string, verbatim_quote: o.verbatim_quote });
  });
  const edges: ComplaintEdge[] = [];
  (rawEdges as unknown[]).forEach((e: unknown, i: number): void => {
    if (e === null || typeof e !== "object" || Array.isArray(e)) throw new Error(`edges[${i}] must be an object`);
    const o = e as { [k: string]: unknown };
    const k = Object.keys(o).sort().join(",");
    if (k !== "dst,relation,src") throw new Error(`edges[${i}] key set mismatch: got ${k}`);
    if (typeof o.src !== "string" || ids.indexOf(o.src) < 0) throw new Error(`edges[${i}].src does not reference a known node`);
    if (typeof o.dst !== "string" || ids.indexOf(o.dst) < 0) throw new Error(`edges[${i}].dst does not reference a known node`);
    if (o.src === o.dst) throw new Error(`edges[${i}] self-loop on '${o.src}'`);
    if (L2_RELATION_TYPES.indexOf(o.relation as string) < 0) throw new Error(`edges[${i}].relation '${String(o.relation)}' not in closed vocabulary`);
    edges.push({ src: o.src, dst: o.dst, relation: o.relation as string });
  });
  return { nodes, edges };
}

// Python str offsets are code points; JS indexes UTF-16 units.
function l2CodePointLength(s: string): number {
  return Array.from(s).length;
}

export interface L2Outcome {
  review_id: string;
  status: "graph" | "quarantine" | "error";
  graph: ComplaintGraph | null;
  quarantine: { review_id: string; reason: string; node_count: number; detail: string } | null;
  error: string | null;
}

// extract_graph
export function l2Extract(u: Utterance, responseText: string): L2Outcome {
  let parsed: { nodes: L2RawNode[]; edges: ComplaintEdge[] };
  try {
    parsed = l2ParseResponse(responseText);
  } catch (e) {
    // Python raises ParseError here and the batch records it as a
    // failure (not a quarantine); the flow logs it the same way.
    return { review_id: u.utterance_id, status: "error", graph: null, quarantine: null, error: String((e as Error).message) };
  }
  const q = (reason: string, count: number, detail: string): L2Outcome => ({
    review_id: u.utterance_id,
    status: "quarantine",
    graph: null,
    quarantine: { review_id: u.utterance_id, reason, node_count: count, detail },
    error: null,
  });
  const withOffsets: ComplaintNode[] = [];
  const missing: string[] = [];
  for (const n of parsed.nodes) {
    const at = u.text.indexOf(n.verbatim_quote);
    if (at < 0) {
      missing.push(n.node_id);
      continue;
    }
    const start = l2CodePointLength(u.text.slice(0, at));
    withOffsets.push({ ...n, quote_start: start, quote_end: start + l2CodePointLength(n.verbatim_quote) });
  }
  if (missing.length) {
    return q("hallucination", parsed.nodes.length, `verbatim_quote not found in source for node_ids=[${missing.map((m: string): string => `'${m}'`).join(", ")}]`);
  }
  for (let i = 0; i < withOffsets.length; i++) {
    for (let j = i + 1; j < withOffsets.length; j++) {
      const a = withOffsets[i];
      const b = withOffsets[j];
      if (a.verbatim_quote === b.verbatim_quote) return q("substring_containment", withOffsets.length, `identical quotes ${a.node_id}==${b.node_id}`);
      if (b.verbatim_quote.indexOf(a.verbatim_quote) >= 0) return q("substring_containment", withOffsets.length, `'${a.node_id}'.quote ⊂ '${b.node_id}'.quote`);
      if (a.verbatim_quote.indexOf(b.verbatim_quote) >= 0) return q("substring_containment", withOffsets.length, `'${b.node_id}'.quote ⊂ '${a.node_id}'.quote`);
    }
  }
  const n = withOffsets.length;
  if (n < L2_MIN_NODES) return q("under_minimum_nodes", n, `${n} nodes < MIN_NODES=${L2_MIN_NODES} (thin-review path, SKILL §thin)`);
  if (n > L2_MAX_NODES) return q("over_maximum_nodes", n, `${n} nodes > MAX_NODES=${L2_MAX_NODES} (should not happen — model drift?)`);
  return {
    review_id: u.utterance_id,
    status: "graph",
    graph: { review_id: u.utterance_id, nodes: withOffsets, edges: parsed.edges },
    quarantine: null,
    error: null,
  };
}

// ---------------------------------------------------------------------
// L3 — grouping (replaces embedding + HDBSCAN)
// ---------------------------------------------------------------------
//
// The Python L3 embeds `triggered_element` and `lost_value` node quotes
// with a local sentence encoder and clusters them with HDBSCAN; neither
// runs inside Copilot Studio. Here a prompt groups the same nodes and
// picks representatives; the kernel checks every id it returns and
// builds InsightCluster records with the review behind each quote.

export const L3_CLUSTERABLE: string[] = ["triggered_element", "lost_value"];
export const L3_MAX_QUOTES = 5;

export interface L3Node {
  key: string; // "<review_id>#<node_id>"
  review_id: string;
  node_type: string;
  quote: string;
}

export function l3Nodes(graphs: ComplaintGraph[]): L3Node[] {
  const out: L3Node[] = [];
  for (const g of graphs) {
    for (const n of g.nodes) {
      if (L3_CLUSTERABLE.indexOf(n.node_type) >= 0) {
        out.push({ key: `${g.review_id}#${n.node_id}`, review_id: g.review_id, node_type: n.node_type, quote: n.verbatim_quote });
      }
    }
  }
  return out;
}

// Nodes are numbered 0..N-1 so the model never has to copy long ids.
export function l3BuildMessage(nodes: L3Node[]): string {
  const lines = nodes.map((n: L3Node, i: number): string => `  <n idx="${i}" type="${n.node_type}">${escapeXml(n.quote)}</n>`);
  return `<nodes>\n${lines.join("\n")}\n</nodes>`;
}

export interface L3BuildResult {
  clusters: InsightCluster[];
  noise_idxs: number[];
  issues: string[];
}

// Expected model output:
// {"clusters": [{"member_idxs": [..], "representative_idxs": [..]}], "noise_idxs": [..]}
export function l3BuildClusters(nodes: L3Node[], responseText: string, minClusterSize = 5): L3BuildResult {
  const p = parseModelJson(responseText);
  if (!p.ok || !p.data) throw new Error(p.error || "parse error");
  const d = p.data;
  if (!Array.isArray(d.clusters)) throw new Error("clusters must be a list");
  const issues: string[] = [];
  const used: { [i: number]: number } = {};
  const clusters: InsightCluster[] = [];
  (d.clusters as unknown[]).forEach((c: unknown, ci: number): void => {
    if (c === null || typeof c !== "object") throw new Error(`clusters[${ci}] must be an object`);
    const o = c as { [k: string]: unknown };
    const members = Array.isArray(o.member_idxs) ? (o.member_idxs as unknown[]) : [];
    const reps = Array.isArray(o.representative_idxs) ? (o.representative_idxs as unknown[]) : [];
    const memberIdxs: number[] = [];
    for (const m of members) {
      if (typeof m !== "number" || !Number.isInteger(m) || m < 0 || m >= nodes.length) {
        issues.push(`clusters[${ci}]: member idx ${String(m)} out of range — dropped`);
        continue;
      }
      if (used[m] !== undefined) {
        issues.push(`clusters[${ci}]: idx ${m} already in clusters[${used[m]}] — dropped`);
        continue;
      }
      used[m] = ci;
      memberIdxs.push(m);
    }
    if (memberIdxs.length < minClusterSize) {
      issues.push(`clusters[${ci}]: ${memberIdxs.length} members < ${minClusterSize} — treated as noise`);
      for (const m of memberIdxs) delete used[m];
      return;
    }
    // Representatives: members only, distinct quote text first (the
    // study showed repeated one-word quotes cannot be traced to one
    // review), at most L3_MAX_QUOTES.
    const repIdxs: number[] = [];
    const repTexts: string[] = [];
    for (const r of reps) {
      if (typeof r !== "number" || memberIdxs.indexOf(r) < 0) {
        issues.push(`clusters[${ci}]: representative ${String(r)} is not a member — dropped`);
        continue;
      }
      const t = nodes[r].quote.trim().toLowerCase();
      if (repTexts.indexOf(t) >= 0) continue;
      repIdxs.push(r);
      repTexts.push(t);
      if (repIdxs.length === L3_MAX_QUOTES) break;
    }
    for (const m of memberIdxs) {
      if (repIdxs.length >= Math.min(L3_MAX_QUOTES, memberIdxs.length)) break;
      const t = nodes[m].quote.trim().toLowerCase();
      if (repIdxs.indexOf(m) < 0 && repTexts.indexOf(t) < 0) {
        repIdxs.push(m);
        repTexts.push(t);
      }
    }
    const memberReviews: string[] = [];
    for (const m of memberIdxs) if (memberReviews.indexOf(nodes[m].review_id) < 0) memberReviews.push(nodes[m].review_id);
    const n = clusters.length;
    const cid = `cluster_${n < 10 ? "0" + n : String(n)}`;
    clusters.push({
      cluster_id: cid,
      label: `UNLABELED:${cid}`,
      member_review_ids: memberReviews.sort(),
      centroid_vector_ref: "llm-grouping",
      representative_quotes: repIdxs.map((r: number): string => nodes[r].quote),
      representative_review_ids: repIdxs.map((r: number): string => nodes[r].review_id),
      ui_context: null,
      html: null,
      screenshot_ref: null,
    });
  });
  const noise: number[] = [];
  for (let i = 0; i < nodes.length; i++) if (used[i] === undefined) noise.push(i);
  return { clusters, noise_idxs: noise, issues };
}

// ---------------------------------------------------------------------
// L3b — labelling (l3b_label.py)
// ---------------------------------------------------------------------

export const L3B_LABEL_MIN_LEN = 1;
export const L3B_LABEL_MAX_LEN = 60;

export function l3bBuildMessage(quotes: string[]): string {
  const inner = quotes.map((q: string): string => `  <q>${escapeXml(q)}</q>`).join("\n");
  return `<cluster_quotes>\n${inner}\n</cluster_quotes>`;
}

export function l3bParseLabel(text: string): string {
  const p = parseModelJson(text);
  if (!p.ok || !p.data) throw new Error(p.error || "parse error");
  const d = p.data;
  if (!("label" in d)) throw new Error("missing required top-level key: 'label'");
  const extra = Object.keys(d).filter((k: string): boolean => k !== "label");
  if (extra.length) throw new Error(`unexpected top-level keys: ${extra.sort().join(",")}`);
  if (typeof d.label !== "string") throw new Error("'label' must be str");
  const label = d.label.trim();
  const n = Array.from(label).length;
  if (n < L3B_LABEL_MIN_LEN) throw new Error(`label length ${n} < LABEL_MIN_LEN=${L3B_LABEL_MIN_LEN}`);
  if (n > L3B_LABEL_MAX_LEN) throw new Error(`label length ${n} > LABEL_MAX_LEN=${L3B_LABEL_MAX_LEN}`);
  if (label.toLowerCase().indexOf("unlabeled:") === 0) throw new Error("label echoes the UNLABELED: placeholder");
  return label;
}

export function l3bApply(cluster: InsightCluster, responseText: string): { status: "labeled" | "fallback"; reason: string | null; cluster: InsightCluster } {
  try {
    const label = l3bParseLabel(responseText);
    return { status: "labeled", reason: null, cluster: { ...cluster, label } };
  } catch (e) {
    return { status: "fallback", reason: String((e as Error).message), cluster: { ...cluster, label: `UNLABELED:${cluster.cluster_id}` } };
  }
}
