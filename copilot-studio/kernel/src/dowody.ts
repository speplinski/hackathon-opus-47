// Evidence lookup for the conversational agent: for each ranked
// violation of a reconciled verdict, which reviews and quotes support
// it, or whether it rests on the product artifact only.

import type { InsightCluster, ReconciledVerdict } from "./types";

export interface DwEntry {
  rank: number;
  heuristic: string;
  severity: number;
  basis: "quotes" | "artifact_only";
  reviews: { review_id: string; quotes: string[]; text: string | null }[];
}

export function dwEvidence(
  cluster: InsightCluster,
  reconciled: ReconciledVerdict,
  heuristic: string | null,
  utteranceTexts: { [id: string]: string } | null
): DwEntry[] {
  const repIds = cluster.representative_review_ids || [];
  const out: DwEntry[] = [];
  reconciled.ranked_violations.forEach((v, i: number): void => {
    if (heuristic && v.heuristic !== heuristic && v.heuristic.indexOf(heuristic) < 0) return;
    const reviews = v.evidence_review_ids.map((rid: string) => ({
      review_id: rid,
      quotes: cluster.representative_quotes.filter((_q: string, k: number): boolean => repIds[k] === rid),
      text: utteranceTexts && utteranceTexts[rid] !== undefined ? utteranceTexts[rid] : null,
    }));
    out.push({
      rank: i + 1,
      heuristic: v.heuristic,
      severity: v.severity,
      basis: reviews.length ? "quotes" : "artifact_only",
      reviews,
    });
  });
  return out;
}

// Plain-language summary the agent can return as is.
export function dwSummary(entries: DwEntry[]): string {
  if (!entries.length) return "Nie znaleziono takiego ustalenia w uzgodnionym rankingu.";
  return entries
    .map((e: DwEntry): string => {
      const head = `${e.rank}. ${e.heuristic} (dotkliwość ${e.severity})`;
      if (e.basis === "artifact_only") return `${head}: brak cytatu klienta, ustalenie oparte tylko na artefakcie produktu.`;
      const parts = e.reviews.map((r) => `${r.review_id}: ${r.quotes.map((q: string) => `„${q}”`).join("; ")}`);
      return `${head}: ${e.reviews.length} recenzj${e.reviews.length === 1 ? "a" : "e"} — ${parts.join(" · ")}`;
    })
    .join("\n");
}
