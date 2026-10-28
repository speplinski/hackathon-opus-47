// Verify-on-product — checks the L5 reconciled heuristics against real
// product artefacts (screenshots, exported mock-ups). Port of
// scripts/verify_on_product.py (message envelope, payload validation,
// markdown artifact, provenance summary).
//
// The Python script sends ONE user message whose content alternates
// "[Screenshot: <label>]" text blocks and base64 images, followed by the
// XML envelope built here. In Copilot Studio the images are separate
// "Image or document" prompt inputs, so the kernel returns the caption
// lines and the envelope; the prompt instructions list the captions in
// the same order as the image inputs (see OPS_L8_BRIEF.md).

import { ReconciledVerdict } from "./types";
import { escapeXml, extractJsonObjectText } from "./common";

export const VP_VALID_VERDICTS: string[] = ["confirmed", "partial", "refuted"];
export const VP_VALID_SEVERITIES: number[] = [0, 3, 5, 7, 9];

export interface VpScreenshot {
  label: string;
  path?: string; // repo- or OneDrive-relative path, used in the markdown
}

export interface VpMessage {
  screenshot_captions: string[];
  envelope: string;
  prompt_text: string; // captions (one per line) + blank line + envelope
}

export function vpMessage(reconciled: ReconciledVerdict, screenshots: VpScreenshot[]): VpMessage {
  const captions = screenshots.map((s: VpScreenshot): string => `[Screenshot: ${s.label}]`);
  const env: string[] = [`<cluster_id>${escapeXml(reconciled.cluster_id)}</cluster_id>`, "<baseline_heuristics>"];
  for (const v of reconciled.ranked_violations) {
    env.push(`  <h slug="${escapeXml(v.heuristic)}" baseline_severity="${v.severity}">${escapeXml(v.violation)}</h>`);
  }
  env.push("</baseline_heuristics>");
  env.push(
    "<task>For each heuristic above, verify against the screenshots and emit grounded evidence per the " +
      "output format in the system prompt.</task>"
  );
  const envelope = env.join("\n");
  return {
    screenshot_captions: captions,
    envelope,
    prompt_text: captions.length ? captions.join("\n") + "\n\n" + envelope : envelope,
  };
}

function vpRepr(v: unknown): string {
  if (v === null || v === undefined) return "None";
  if (typeof v === "string") return "'" + v.replace(/\\/g, "\\\\").replace(/'/g, "\\'") + "'";
  if (typeof v === "boolean") return v ? "True" : "False";
  return JSON.stringify(v);
}

function vpSortedRepr(xs: string[]): string {
  return "[" + xs.slice().sort().map((x: string): string => vpRepr(x)).join(", ") + "]";
}

export interface VpParseResult {
  ok: boolean;
  payload: { [k: string]: unknown } | null;
  error: string | null;
}

// _extract_json + _validate_payload
export function vpParse(text: string, baselineSlugs: string[]): VpParseResult {
  const fail = (error: string): VpParseResult => ({ ok: false, payload: null, error });
  const raw = extractJsonObjectText(text);
  if (raw === null) return fail(`no JSON object in response: ${vpRepr(text)}`);
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    return fail(String((e as Error).message));
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) return fail("expected a JSON object");
  const payload = data as { [k: string]: unknown };
  if (!Object.prototype.hasOwnProperty.call(payload, "grounded_evidence")) return fail("missing 'grounded_evidence' key");
  const ge = payload["grounded_evidence"];
  if (ge === null || typeof ge !== "object" || Array.isArray(ge)) return fail("'grounded_evidence' must be dict");
  const g = ge as { [k: string]: unknown };
  const keys = Object.keys(g);
  const missing = baselineSlugs.filter((s: string): boolean => keys.indexOf(s) < 0);
  const extra = keys.filter((s: string): boolean => baselineSlugs.indexOf(s) < 0);
  if (missing.length) return fail(`grounded_evidence missing heuristics: ${vpSortedRepr(missing)}`);
  if (extra.length) return fail(`grounded_evidence extra heuristics: ${vpSortedRepr(extra)}`);
  for (const slug of keys) {
    const entry = g[slug];
    const e = entry !== null && typeof entry === "object" && !Array.isArray(entry) ? (entry as { [k: string]: unknown }) : {};
    const verdict = e["confirmed"];
    if (typeof verdict !== "string" || VP_VALID_VERDICTS.indexOf(verdict) < 0) {
      return fail(`${slug}: 'confirmed' must be one of confirmed|partial|refuted, got ${vpRepr(verdict)}`);
    }
    const sev = e["adjusted_severity"];
    if (typeof sev !== "number" || VP_VALID_SEVERITIES.indexOf(sev) < 0) {
      return fail(`${slug}: 'adjusted_severity' must be in {0,3,5,7,9}, got ${vpRepr(sev)}`);
    }
  }
  if (typeof payload["summary"] !== "string") return fail("'summary' must be non-empty str");
  return { ok: true, payload, error: null };
}

function vpEntry(payload: { [k: string]: unknown }, slug: string): { [k: string]: unknown } {
  const ge = payload["grounded_evidence"] as { [k: string]: { [k: string]: unknown } };
  return ge[slug];
}

// The markdown artifact written next to the JSON (verify_on_product_*.md).
export function vpRenderMarkdown(
  reconciled: ReconciledVerdict,
  payload: { [k: string]: unknown },
  model: string,
  screenshots: VpScreenshot[]
): string {
  const cid = reconciled.cluster_id;
  const slugCount = reconciled.ranked_violations
    .map((v) => v.heuristic)
    .filter((h: string, i: number, a: string[]): boolean => a.indexOf(h) === i).length;
  const lines: string[] = [
    `# ${cid} — verify-on-product grounded evidence`,
    "",
    `Model: \`${model}\` · Cluster: \`${cid}\` · Baseline heuristics: ${slugCount}`,
    "",
    "## Screenshots audited",
    "",
  ];
  for (const s of screenshots) lines.push(`- \`${s.path || ""}\` — ${s.label}`);
  lines.push(
    "",
    "## Baseline heuristics (from L5 reconciled) vs grounded evidence",
    "",
    "| Heuristic | Baseline sev | Adjusted sev | Verdict | Evidence |",
    "|---|---|---|---|---|"
  );
  for (const v of reconciled.ranked_violations) {
    const e = vpEntry(payload, v.heuristic);
    const ev = String(e["evidence"]).split("|").join("\\|").split("\n").join(" ");
    lines.push(`| \`${v.heuristic}\` | ${v.severity} | ${e["adjusted_severity"]} | ${e["confirmed"]} | ${ev} |`);
  }
  lines.push("", "## Per-heuristic reasoning", "");
  for (const v of reconciled.ranked_violations) {
    const e = vpEntry(payload, v.heuristic);
    lines.push(
      `### \`${v.heuristic}\``,
      `- **Verdict:** ${e["confirmed"]}`,
      `- **Baseline severity:** ${v.severity} → **adjusted:** ${e["adjusted_severity"]}`,
      `- **Evidence:** ${e["evidence"]}`,
      `- **Reasoning:** ${e["reasoning"]}`,
      ""
    );
  }
  lines.push("## Summary", "", String(payload["summary"]), "");
  return lines.join("\n");
}

export interface VpSummary {
  baseline_heuristic_count: number;
  baseline_severity_sum: number;
  adjusted_severity_sum: number;
  verdict_counts: { [k: string]: number };
}

// The counts the Python script writes into its provenance sidecar.
export function vpSummary(reconciled: ReconciledVerdict, payload: { [k: string]: unknown }): VpSummary {
  const ge = payload["grounded_evidence"] as { [k: string]: { [k: string]: unknown } };
  const counts: { [k: string]: number } = {};
  for (const slug of Object.keys(ge)) {
    const k = String(ge[slug]["confirmed"]);
    counts[k] = (counts[k] || 0) + 1;
  }
  return {
    baseline_heuristic_count: reconciled.ranked_violations
      .map((v) => v.heuristic)
      .filter((h: string, i: number, a: string[]): boolean => a.indexOf(h) === i).length,
    baseline_severity_sum: reconciled.ranked_violations.reduce((a: number, v): number => a + Math.trunc(v.severity), 0),
    adjusted_severity_sum: reconciled.ranked_violations.reduce(
      (a: number, v): number => a + Math.trunc(Number(ge[v.heuristic]["adjusted_severity"])),
      0
    ),
    verdict_counts: counts,
  };
}
