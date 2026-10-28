import { describe, expect, test } from "bun:test";
import { readFileSync } from "fs";
import type { Utterance } from "../src/types";
import {
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
  l3bParseLabel,
} from "../src/l0_l3";

interface Fx {
  l1: { utterance: Utterance; message: string; response_text: string; expected: { ok: boolean; is_ux_relevant?: boolean; classifier_confidence?: number; rubric_tags?: string[] } }[];
  l2: { utterance: Utterance; message: string; response_text: string; expected: { status: string; graph?: unknown; reason?: string; node_count?: number } }[];
  l3b_messages: { quotes: string[]; message: string }[];
  l3b_parse: { response_text: string; ok: boolean; label?: string }[];
}
const fx = JSON.parse(readFileSync(`${import.meta.dir}/fixtures/l0_l3.json`, "utf8")) as Fx;

describe("L1 (against Python on logged responses)", () => {
  test("messages are byte-identical", () => {
    for (const c of fx.l1) expect(l1BuildMessage(c.utterance)).toBe(c.message);
  });
  test("parser agrees", () => {
    for (const c of fx.l1) {
      const r = l1Parse(c.utterance.utterance_id, c.response_text);
      expect(r.ok).toBe(c.expected.ok);
      if (c.expected.ok) {
        expect(r.is_ux_relevant).toBe(c.expected.is_ux_relevant as boolean);
        expect(r.classifier_confidence).toBe(c.expected.classifier_confidence as number);
        expect(r.rubric_tags).toEqual(c.expected.rubric_tags as string[]);
      }
    }
  });
  test("parser rejects bad shapes", () => {
    expect(l1Parse("x", "no json").ok).toBe(false);
    expect(l1Parse("x", '{"is_ux_relevant": true, "classifier_confidence": 1.2, "rubric_tags": []}').ok).toBe(false);
    expect(l1Parse("x", '{"is_ux_relevant": true, "classifier_confidence": 0.5, "rubric_tags": ["made_up"]}').ok).toBe(false);
    expect(l1Parse("x", '{"is_ux_relevant": "yes", "classifier_confidence": 0.5, "rubric_tags": []}').ok).toBe(false);
  });
});

describe("L2 (against Python extract_graph on logged responses)", () => {
  test("messages are byte-identical", () => {
    for (const c of fx.l2) expect(l2BuildMessage(c.utterance)).toBe(c.message);
  });
  test("graphs and quarantine reasons agree", () => {
    for (const c of fx.l2) {
      const o = l2Extract(c.utterance, c.response_text);
      expect(o.status).toBe(c.expected.status as "graph" | "quarantine" | "error");
      if (c.expected.status === "graph") expect(o.graph).toEqual(c.expected.graph as never);
      if (c.expected.status === "quarantine") {
        expect(o.quarantine?.reason).toBe(c.expected.reason as string);
        expect(o.quarantine?.node_count).toBe(c.expected.node_count as number);
      }
    }
  });
  test("hallucinated quote goes to quarantine", () => {
    const u: Utterance = { utterance_id: "r1", source_type: "t", text: "the app froze and I lost my streak today" };
    const resp = JSON.stringify({
      nodes: [
        { node_id: "n1", node_type: "pain", verbatim_quote: "the app froze" },
        { node_id: "n2", node_type: "lost_value", verbatim_quote: "lost my streak" },
        { node_id: "n3", node_type: "triggered_element", verbatim_quote: "the paywall" },
      ],
      edges: [],
    });
    const o = l2Extract(u, resp);
    expect(o.status).toBe("quarantine");
    expect(o.quarantine?.reason).toBe("hallucination");
  });
});

describe("L3 grouping and L3b", () => {
  test("L3b messages are byte-identical", () => {
    for (const c of fx.l3b_messages) expect(l3bBuildMessage(c.quotes)).toBe(c.message);
  });
  test("L3b parser agrees on logged responses", () => {
    for (const c of fx.l3b_parse) {
      if (c.ok) expect(l3bParseLabel(c.response_text)).toBe(c.label as string);
      else expect(() => l3bParseLabel(c.response_text)).toThrow();
    }
    expect(l3bApply({ cluster_id: "cluster_01", label: "x", member_review_ids: ["a"], centroid_vector_ref: "r", representative_quotes: ["q"] }, '{"label": "UNLABELED: x"}').status).toBe("fallback");
  });
  test("grouping keeps review per quote, drops bad ids and repeated text", () => {
    const graphs = fx.l2.filter((c) => c.expected.status === "graph").map((c) => c.expected.graph) as never[];
    const nodes = l3Nodes(graphs);
    expect(nodes.length).toBeGreaterThan(10);
    expect(l3BuildMessage(nodes.slice(0, 2))).toContain('<n idx="0"');
    const members = [0, 1, 2, 3, 4, 5, 6];
    const resp = JSON.stringify({ clusters: [{ member_idxs: members.concat([9999]), representative_idxs: [0, 0, 1, 2, 500] }, { member_idxs: [7, 8], representative_idxs: [7] }] });
    const r = l3BuildClusters(nodes, resp, 5);
    expect(r.clusters.length).toBe(1);
    const c = r.clusters[0];
    expect(c.representative_quotes.length).toBeLessThanOrEqual(5);
    c.representative_quotes.forEach((q: string, i: number) => {
      const rid = (c.representative_review_ids as string[])[i];
      expect(nodes.some((n) => n.review_id === rid && n.quote === q)).toBe(true);
    });
    expect(r.issues.some((s: string) => s.includes("9999"))).toBe(true);
    expect(r.noise_idxs).toContain(7);
  });
});

describe("L0", () => {
  test("assigns stable ids, rejects empty and duplicate items", () => {
    const r = l0Normalize([{ source_type: "nps comment", items: [{ text: "slow login" }, { text: "" }, { id: "a", text: "x" }, { id: "a", text: "y" }] }]);
    expect(r.utterances.length).toBe(2);
    expect(r.utterances[0].utterance_id).toBe(l0Normalize([{ source_type: "nps comment", items: [{ text: "slow login" }] }]).utterances[0].utterance_id);
    expect(r.utterances[0].source_type).toBe("nps_comment");
    expect(r.rejected.length).toBe(2);
  });
});
