// Golden tests: the TypeScript port of L5–L7 must reproduce the Python
// pipeline byte-for-byte (prompts) and value-for-value (derived data).
// Fixtures: tests/fixtures_l5_l7.py.

import { describe, expect, test } from "bun:test";
import { readFileSync } from "fs";
import { AuditVerdict, InsightCluster, PriorityScore, ReconciledVerdict } from "../src/types";
import { l5BuildBundle, l5BuildUserMessage, l5Reconcile } from "../src/l5";
import { l6AggregatePasses, l6BuildUserMessage, l6NeedsThirdPass, l6Score, l6WeightedTotal, l7BuildUserMessage, l7Decide } from "../src/l6_l7";
import { uzgodnienieRun } from "../src/entry_uzgodnienie";

type Obj = { [k: string]: unknown };
interface Case {
  branch: string;
  cluster_id: string;
  cluster: InsightCluster;
  verdicts: AuditVerdict[];
  l5: { message: string; response_text: string; ranked_violations: Obj[]; tensions: Obj[]; gaps: Obj[]; verdict: ReconciledVerdict };
  l6: { message: string; response_texts: string[]; priority: PriorityScore };
  l7: { message: string; priority_in: PriorityScore; response_text: string; principle: Obj; decision: Obj };
}
interface Agg {
  passes: { [k: string]: number }[];
  dims: { [k: string]: number };
  delta: number;
  needs_third: boolean;
  weighted_total: number;
}

const FX = JSON.parse(readFileSync(new URL("./fixtures/l5_l7.json", import.meta.url), "utf8")) as {
  cases: Case[];
  l6_aggregation: Agg[];
};

function clone<T>(x: T): T {
  return JSON.parse(JSON.stringify(x)) as T;
}

for (const c of FX.cases) {
  describe(`${c.branch} ${c.cluster_id}`, () => {
    const bundle = l5BuildBundle(c.cluster_id, c.verdicts);

    test("L5 prompt is byte-identical", () => {
      expect(l5BuildUserMessage(c.cluster, bundle)).toBe(c.l5.message);
    });

    test("L5 derived lists and ReconciledVerdict match Python", () => {
      const w: string[] = [];
      const out = l5Reconcile(c.cluster, c.verdicts, c.l5.response_text, w);
      expect(out.status).toBe("audited");
      const np = out.native_payload;
      expect(clone(np["ranked_violations"])).toEqual(c.l5.ranked_violations);
      expect(clone(np["tensions"])).toEqual(c.l5.tensions);
      expect(clone(np["gaps"])).toEqual(c.l5.gaps);
      expect(clone(out.verdict)).toEqual(c.l5.verdict);
      // Evidence chain: at least one ranked violation reaches reviews.
      expect(out.verdict.ranked_violations.some((v) => v.evidence_review_ids.length > 0)).toBe(true);
    });

    test("L5 through the Office Script entry point", () => {
      const r = uzgodnienieRun("l5_reconcile", JSON.stringify({ cluster: c.cluster, verdicts: c.verdicts, response_text: c.l5.response_text }));
      expect(r.ok).toBe(true);
      expect(clone((r.result as Obj)["verdict"])).toEqual(c.l5.verdict);
      const m = uzgodnienieRun("l5_message", JSON.stringify({ cluster: c.cluster, verdicts: c.verdicts }));
      expect((m.result as Obj)["message"]).toBe(c.l5.message);
    });

    test("L6 prompt is byte-identical", () => {
      expect(l6BuildUserMessage(c.cluster, c.l5.verdict)).toBe(c.l6.message);
    });

    test("L6 score matches Python", () => {
      const out = l6Score(c.cluster_id, c.l6.response_texts, null);
      expect(out.status).toBe("scored");
      const p = out.priority as PriorityScore;
      expect(p.dimensions).toEqual(c.l6.priority.dimensions);
      expect(p.meta_weights).toEqual(c.l6.priority.meta_weights);
      expect(p.validation_passes).toBe(c.l6.priority.validation_passes);
      expect(p.validation_delta).toBe(c.l6.priority.validation_delta);
      expect(p.weighted_total).toBeCloseTo(c.l6.priority.weighted_total, 9);
    });

    test("L7 prompt is byte-identical", () => {
      expect(l7BuildUserMessage(c.cluster, c.l5.verdict, c.l7.priority_in)).toBe(c.l7.message);
    });

    test("L7 principle and decision match Python", () => {
      const out = l7Decide(c.cluster, c.l5.verdict, c.l7.response_text);
      expect(out.status).toBe("decided");
      expect(clone(out.principle)).toEqual(c.l7.principle);
      expect(clone(out.decision)).toEqual(c.l7.decision);
    });
  });
}

describe("L6 aggregation (synthetic, Python-computed)", () => {
  for (const a of FX.l6_aggregation) {
    test(`${a.passes.length} passes`, () => {
      const r = l6AggregatePasses(a.passes);
      expect(r.dims).toEqual(a.dims);
      expect(r.delta).toBe(a.delta);
      expect(l6NeedsThirdPass(a.passes[0], a.passes[1])).toBe(a.needs_third);
      expect(l6WeightedTotal(r.dims, { severity: 0.2, reach: 0.2, persistence: 0.2, business_impact: 0.2, cognitive_cost: 0.2 })).toBeCloseTo(a.weighted_total, 9);
    });
  }
});

// --- rejection cases -------------------------------------------------------

const C0 = FX.cases[0];

function mutate5(f: (resp: Obj) => void): string {
  const resp = JSON.parse(C0.l5.response_text) as Obj;
  f(resp);
  return JSON.stringify(resp);
}

function nodes(resp: Obj): Obj[] {
  return (resp["graph"] as Obj)["nodes"] as Obj[];
}

describe("L5 rejects malformed responses (fallback, never silent)", () => {
  const cases: [string, string][] = [
    ["not JSON", "I cannot help with that."],
    ["unexpected top-level key", mutate5((r) => (r["extra"] = 1))],
    [
      "violation heuristic does not match the L4 finding",
      mutate5((r) => {
        const v = nodes(r).find((n) => n["type"] === "violation") as Obj;
        v["source_heuristic"] = "invented_heuristic";
      }),
    ],
    [
      "violation severity does not match the L4 finding",
      mutate5((r) => {
        const v = nodes(r).find((n) => n["type"] === "violation") as Obj;
        v["source_severity_anchored"] = (v["source_severity_anchored"] as number) === 9 ? 7 : 9;
      }),
    ],
    [
      "corroboration cites a non-violation node",
      mutate5((r) => {
        const ns = nodes(r);
        ns.push({ ...(ns.find((n) => n["type"] === "violation") as Obj), id: "zz_corr", type: "corroboration", member_ids: ["nope_1", "nope_2"] });
      }),
    ],
    [
      "edge to an unknown node",
      mutate5((r) => {
        ((r["graph"] as Obj)["edges"] as Obj[]).push({ source: "ghost", target: "ghost2", type: "corroborates" });
      }),
    ],
    [
      "node missing a key",
      mutate5((r) => {
        delete nodes(r)[0]["why_missed"];
      }),
    ],
  ];
  for (const [name, text] of cases) {
    test(name, () => {
      const out = l5Reconcile(C0.cluster, C0.verdicts, text, []);
      expect(out.status).toBe("fallback");
      expect(out.verdict.ranked_violations).toEqual([]);
      expect(out.native_payload["fallback"]).toBe(true);
      expect(out.native_payload["raw_response"]).toBe(text);
    });
  }

  test("gap breaking the bidirectional quotes rule", () => {
    const text = mutate5((r) => {
      nodes(r).push({
        id: "zz_gap", type: "gap", label: "x", rationale: "x", confidence: 0.5,
        source_skill: null, source_heuristic: null, source_severity_anchored: null, source_finding_idx: null,
        member_ids: [], skill_a: null, skill_b: null, axis: null, resolution: null,
        evidence_source: ["quotes"], evidence_quote_idxs: [], why_missed: "x",
      });
    });
    expect(l5Reconcile(C0.cluster, C0.verdicts, text, []).status).toBe("fallback");
  });

  test("empty bundle skips the model call", () => {
    const m = uzgodnienieRun("l5_message", JSON.stringify({ cluster: C0.cluster, verdicts: [] }));
    expect((m.result as Obj)["skip"]).toBe(true);
    const out = l5Reconcile(C0.cluster, [], null, []);
    expect(out.status).toBe("fallback");
    expect(out.reason).toBe("no L4 verdicts in input bundle");
  });
});

describe("L6 pass handling", () => {
  test("drift > 1 asks for a third pass, then takes the median", () => {
    const [t1] = C0.l6.response_texts;
    const p1 = JSON.parse(t1) as Obj;
    const p2 = clone(p1);
    const p3 = clone(p1);
    (p2["dimensions"] as Obj)["reach"] = ((p1["dimensions"] as Obj)["reach"] as number) >= 5 ? 0 : 10;
    (p3["dimensions"] as Obj)["reach"] = 5;
    const two = l6Score(C0.cluster_id, [t1, JSON.stringify(p2)], null);
    expect(two.status).toBe("needs_third_pass");
    const three = l6Score(C0.cluster_id, [t1, JSON.stringify(p2), JSON.stringify(p3)], null);
    expect(three.status).toBe("scored");
    expect((three.priority as PriorityScore).validation_passes).toBe(3);
    expect((three.priority as PriorityScore).dimensions["reach"]).toBe(5);
  });

  test("one malformed pass → fallback with the other pass's scores", () => {
    const out = l6Score(C0.cluster_id, [C0.l6.response_texts[0], "{}"], null);
    expect(out.status).toBe("fallback");
    expect((out.priority as PriorityScore).validation_delta).toBe(0);
  });

  test("both passes malformed → zero scores", () => {
    const out = l6Score(C0.cluster_id, ["x", "y"], null);
    expect(out.status).toBe("fallback");
    expect((out.priority as PriorityScore).weighted_total).toBe(0);
  });

  test("dimension out of range is rejected", () => {
    const p = JSON.parse(C0.l6.response_texts[0]) as Obj;
    (p["dimensions"] as Obj)["severity"] = 11;
    const out = l6Score(C0.cluster_id, [JSON.stringify(p), JSON.stringify(p)], null);
    expect(out.status).toBe("fallback");
  });
});

describe("L7 cross-reference checks", () => {
  test("hallucinated review id → fallback", () => {
    const p = JSON.parse(C0.l7.response_text) as Obj;
    ((p["principle"] as Obj)["derived_from_review_ids"] as string[]).push("not_a_member");
    const out = l7Decide(C0.cluster, C0.l5.verdict, JSON.stringify(p));
    expect(out.status).toBe("fallback");
    expect(out.reason).toContain("hallucinated citation");
  });

  test("invented heuristic slug → fallback", () => {
    const p = JSON.parse(C0.l7.response_text) as Obj;
    ((p["decision"] as Obj)["resolves_heuristics"] as string[]).push("invented_slug");
    const out = l7Decide(C0.cluster, C0.l5.verdict, JSON.stringify(p));
    expect(out.status).toBe("fallback");
    expect(out.reason).toContain("invented slug");
  });
});

describe("entry point", () => {
  test("unknown operation returns ok:false", () => {
    const r = uzgodnienieRun("nope", "{}");
    expect(r.ok).toBe(false);
  });
  test("l6_score and l7_decide through the entry", () => {
    const s = uzgodnienieRun("l6_score", JSON.stringify({ cluster_id: C0.cluster_id, response_texts: C0.l6.response_texts }));
    expect(s.ok).toBe(true);
    const d = uzgodnienieRun("l7_decide", JSON.stringify({ cluster: C0.cluster, reconciled: C0.l5.verdict, response_text: C0.l7.response_text }));
    expect(d.ok).toBe(true);
    expect(clone((d.result as Obj)["decision"])).toEqual(C0.l7.decision);
  });
});
