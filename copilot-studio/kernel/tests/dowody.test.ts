import { expect, test } from "bun:test";
import { readFileSync } from "fs";
import { dwEvidence, dwSummary } from "../src/dowody";

const fx = JSON.parse(readFileSync(`${import.meta.dir}/fixtures/dowody.json`, "utf8"));

test("cluster_11 Opus 4.7: six findings rest on quotes, upgrade_path_opaque on the artifact", () => {
  const all = dwEvidence(fx.cluster, fx.reconciled, null, null);
  expect(all.length).toBe(7);
  expect(all.filter((e) => e.basis === "quotes").length).toBe(6);
  const opaque = all.find((e) => e.heuristic === "upgrade_path_opaque");
  expect(opaque?.basis).toBe("artifact_only");
  for (const e of all) for (const r of e.reviews) expect(r.quotes.length).toBeGreaterThan(0);
  expect(dwSummary(dwEvidence(fx.cluster, fx.reconciled, "upgrade_path", null))).toContain("artefakcie");
});
