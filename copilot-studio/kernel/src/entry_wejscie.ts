// AD_Wejscie — Office Script entry for L0–L3b.
// Called from the flow with Excel Online (Business) > Run script.
// Parameters: operation (string), payload (JSON string). Returns a
// JSON string: {ok, operation, result, errors}.

import type { ComplaintGraph, InsightCluster, KernelResult, Utterance } from "./types";
import {
  L0Source,
  L3Node,
  l0Normalize,
  l1BuildMessage,
  l1Parse,
  l2BuildMessage,
  l2Extract,
  l3BuildClusters,
  l3BuildMessage,
  l3Nodes,
  l3bApply,
  l3bBuildMessage,
} from "./l0_l3";

function main(workbook: ExcelScript.Workbook, operation: string, payload: string): string {
  const out: KernelResult = { ok: true, operation, result: null, errors: [] };
  try {
    const p = JSON.parse(payload || "{}") as { [k: string]: unknown };
    switch (operation) {
      case "l0_normalize": {
        out.result = l0Normalize(p.sources as L0Source[]);
        break;
      }
      case "l1_messages": {
        const us = p.utterances as Utterance[];
        out.result = us.map((u: Utterance) => ({ utterance_id: u.utterance_id, message: l1BuildMessage(u) }));
        break;
      }
      case "l1_parse": {
        const items = p.items as { utterance_id: string; response_text: string }[];
        out.result = items.map((it) => l1Parse(it.utterance_id, it.response_text));
        break;
      }
      case "l2_messages": {
        const us = p.utterances as Utterance[];
        out.result = us.map((u: Utterance) => ({ utterance_id: u.utterance_id, message: l2BuildMessage(u) }));
        break;
      }
      case "l2_extract": {
        // items: [{utterance: Utterance, response_text}]
        const items = p.items as { utterance: Utterance; response_text: string }[];
        const outcomes = items.map((it) => l2Extract(it.utterance, it.response_text));
        out.result = {
          graphs: outcomes.filter((o) => o.status === "graph").map((o) => o.graph),
          quarantine: outcomes.filter((o) => o.status === "quarantine").map((o) => o.quarantine),
          errors: outcomes.filter((o) => o.status === "error").map((o) => ({ review_id: o.review_id, error: o.error })),
        };
        break;
      }
      case "l3_message": {
        const nodes = l3Nodes(p.graphs as ComplaintGraph[]);
        out.result = { node_count: nodes.length, nodes, message: l3BuildMessage(nodes) };
        break;
      }
      case "l3_clusters": {
        const nodes = p.nodes as L3Node[];
        const min = typeof p.min_cluster_size === "number" ? (p.min_cluster_size as number) : 5;
        try {
          out.result = { status: "clustered", reason: null, ...l3BuildClusters(nodes, String(p.response_text), min) };
        } catch (e) {
          // Unusable model response: a recorded fallback, not an input error.
          out.result = { status: "fallback", reason: String((e as Error).message), clusters: [], noise_idxs: [], issues: [] };
        }
        break;
      }
      case "l3b_message": {
        const c = p.cluster as InsightCluster;
        out.result = { cluster_id: c.cluster_id, message: l3bBuildMessage(c.representative_quotes) };
        break;
      }
      case "l3b_label": {
        out.result = l3bApply(p.cluster as InsightCluster, String(p.response_text));
        break;
      }
      default:
        out.ok = false;
        out.errors.push(`unknown operation: ${operation}`);
    }
  } catch (e) {
    out.ok = false;
    out.errors.push(String((e as Error).message || e));
  }
  return JSON.stringify(out);
}
