// L4 port vs the Python pipeline. Fixtures: tests/fixtures_l4.py.
import { describe, expect, test } from "bun:test";
import { readFileSync } from "fs";
import { InsightCluster } from "../src/types";
import { l4BuildMessage, l4ParseResponse, l4MakeVerdict, l4PyReprStr, L4_LENS_IDS } from "../src/l4";

interface Case {
  kind: "audited" | "rejected" | "accepted";
  lens: string;
  cluster_id: string;
  name?: string;
  branch?: string;
  response_text: string;
  expected_message?: string;
  expected_violations?: unknown[];
  expected_verdict_id?: string;
  expected_native_ref?: string;
  expected_reason?: string | null;
  reason_exact?: boolean;
}

const fx = JSON.parse(readFileSync(new URL("./fixtures/l4.json", import.meta.url), "utf8")) as {
  clusters: { [id: string]: InsightCluster };
  cases: Case[];
  repr_cases: { s: string; repr: string }[];
};

const label = (c: Case): string => `${c.lens} ${c.cluster_id} ${c.name || c.branch || ""}`;

describe("l4 build_user_message", () => {
  for (const c of fx.cases.filter((x) => x.kind === "audited")) {
    test(`byte-identical: ${label(c)}`, () => {
      expect(l4BuildMessage(c.lens, fx.clusters[c.cluster_id])).toBe(c.expected_message as string);
    });
  }
  test("Norman omits html/screenshot_ref, other lenses include them", () => {
    const cl: InsightCluster = { ...fx.clusters["cluster_11"], html: "<b>x</b>", screenshot_ref: "a<b>.png" };
    expect(l4BuildMessage("audit-usability-fundamentals", cl)).not.toContain("<screenshot_ref>");
    const m = l4BuildMessage("audit-accessibility", cl);
    expect(m).toContain("  <html><![CDATA[\n<b>x</b>\n]]></html>");
    expect(m).toContain("<screenshot_ref>a&lt;b&gt;.png</screenshot_ref>");
  });
});

describe("l4 verdicts on study payloads", () => {
  for (const c of fx.cases.filter((x) => x.kind === "audited")) {
    test(`conversion: ${label(c)}`, () => {
      const out = l4MakeVerdict({
        lens: c.lens,
        cluster: fx.clusters[c.cluster_id],
        response_text: c.response_text,
        model: "m",
        produced_at: "2026-10-28T00:00:00Z",
        skill_hash: "h",
      });
      expect(out.status).toBe("audited");
      expect(out.verdict.verdict_id).toBe(c.expected_verdict_id as string);
      expect(out.verdict.native_payload_ref).toBe(c.expected_native_ref as string);
      expect(out.verdict.relevant_heuristics).toEqual(c.expected_violations as never);
    });
  }
});

describe("l4 parser rejections match Python", () => {
  for (const c of fx.cases.filter((x) => x.kind !== "audited")) {
    test(`${c.kind}: ${label(c)}`, () => {
      const r = l4ParseResponse(c.lens, c.response_text, fx.clusters[c.cluster_id].representative_quotes.length);
      expect(r.ok).toBe(c.kind === "accepted");
      if (c.kind === "rejected" && c.reason_exact) expect(r.reason).toBe(c.expected_reason as string);
    });
  }
  test("fallback verdict shape", () => {
    const out = l4MakeVerdict({ lens: "audit-ux-architecture", cluster: fx.clusters["cluster_11"], response_text: "no" });
    expect(out.status).toBe("fallback");
    expect(out.verdict.relevant_heuristics).toEqual([]);
    expect(out.native_payload).toEqual({ fallback: true, reason: out.reason, raw_response: "no" });
    expect(out.verdict.native_payload_ref).toBe("l4_audit_ux_architecture_verdicts.native.jsonl#audit-ux-architecture__cluster_11");
  });
  test("unknown lens is an error, not a silent pass", () => {
    expect(() => l4BuildMessage("audit-vibes", fx.clusters["cluster_11"])).toThrow();
  });
  test("all six lenses covered by fixtures", () => {
    const seen = new Set(fx.cases.map((c) => c.lens));
    for (const id of L4_LENS_IDS) expect(seen.has(id)).toBe(true);
  });
});

describe("python repr", () => {
  for (const r of fx.repr_cases) {
    test(JSON.stringify(r.s), () => expect(l4PyReprStr(r.s)).toBe(r.repr));
  }
});
