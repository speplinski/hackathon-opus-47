// Design brief — the pipeline's deliverable for designers. Port of
// scripts/export_design_brief.py (_build_brief, _evidence_per_finding,
// _recover_snapshot). Deterministic: no model call.
//
// Office Scripts have no crypto and no file system, so the caller passes
// the timestamp, the input files' sha256 (optional) and the bodies of any
// iteration artifacts the brief must read (iterations >= 2).

import {
  InsightCluster,
  ReconciledVerdict,
  PriorityScore,
  DesignDecision,
  OptimizationIteration,
} from "./types";

export interface BrInput {
  label: string;
  path: string;
  sha256?: string | null;
}

export interface BrPayload {
  cluster: InsightCluster;
  reconciled: ReconciledVerdict;
  priority: PriorityScore;
  decision: DesignDecision;
  iterations: OptimizationIteration[];
  verify_payload?: { [k: string]: unknown } | null;
  model_short: string;
  loop_verifier: string;
  generated_at?: string | null; // Python isoformat(timespec="seconds"), e.g. 2026-04-24T18:11:48+00:00
  inputs?: BrInput[];
  artifact_bodies?: { [ref: string]: string };
}

// Python format(x, ".Nf"): round-half-even on the exact binary value.
function brFixed(x: number, digits: number): string {
  const neg = x < 0 || (x === 0 && 1 / x < 0);
  const ax = Math.abs(x);
  const exact = ax.toPrecision(100);
  let body: string;
  if (/e/.test(exact)) body = ax.toFixed(digits);
  else {
    const parts = exact.split(".");
    const frac = (parts[1] || "").padEnd(digits + 1, "0");
    const kept = parts[0] + frac.slice(0, digits);
    const next = frac.charAt(digits);
    const rest = frac.slice(digits + 1);
    let up = false;
    if (next > "5") up = true;
    else if (next === "5") up = /[1-9]/.test(rest) || parseInt(kept.charAt(kept.length - 1), 10) % 2 === 1;
    let arr = kept.split("");
    if (up) {
      let i = arr.length - 1;
      while (i >= 0) {
        if (arr[i] === "9") {
          arr[i] = "0";
          i--;
        } else {
          arr[i] = String(parseInt(arr[i], 10) + 1);
          break;
        }
      }
      if (i < 0) arr = ["1"].concat(arr);
    }
    const all = arr.join("");
    const ip = all.slice(0, all.length - digits).replace(/^0+(?=\d)/, "") || "0";
    body = digits > 0 ? ip + "." + all.slice(all.length - digits) : ip;
  }
  return (neg ? "-" : "") + body;
}

function brPad2(n: number): string {
  return n < 10 && n >= 0 ? "0" + String(n) : String(n);
}

// Python slicing / len() count code points, not UTF-16 units.
function brCpLen(s: string): number {
  return Array.from(s).length;
}

function brCpSlice(s: string, n: number): string {
  return Array.from(s).slice(0, n).join("");
}

function brSum(o: { [k: string]: number }): number {
  return Object.keys(o).reduce((a: number, k: string): number => a + o[k], 0);
}

function brNowIso(): string {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "+00:00");
}

function brPyStr(v: unknown): string {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  return String(v);
}

function brExtractSection(body: string, header: string): string | null {
  const marker = `## ${header}\n`;
  const idx = body.indexOf(marker);
  if (idx < 0) return null;
  const after = body.slice(idx + marker.length);
  const end = after.indexOf("\n## ");
  return (end < 0 ? after : after.slice(0, end)).trim();
}

// _recover_snapshot
function brRecoverSnapshot(
  it: OptimizationIteration,
  decision: DesignDecision,
  bodies: { [ref: string]: string }
): string {
  if (it.iteration_index === 0) return decision.before_snapshot;
  if (it.iteration_index === 1) return decision.after_snapshot;
  const body = bodies[it.design_artifact_ref];
  if (body === undefined) return decision.after_snapshot;
  const s = brExtractSection(body, "new_snapshot");
  return s === null ? decision.after_snapshot : s;
}

// _evidence_per_finding
function brEvidencePerFinding(cluster: InsightCluster, reconciled: ReconciledVerdict): string[] {
  const lines: string[] = ["**Evidence per finding:**", ""];
  if (!reconciled.ranked_violations.some((v) => (v.evidence_review_ids || []).length > 0)) {
    lines.push(
      "Per-finding review ids are not available for this run " +
        "(data produced before L3 recorded the review behind each " +
        "representative quote). The chain resolves to the cluster's " +
        "member reviews listed above.",
      ""
    );
    return lines;
  }
  const quotesByReview: { [rid: string]: string[] } = {};
  const rids = cluster.representative_review_ids || [];
  const n = Math.min(rids.length, cluster.representative_quotes.length);
  for (let i = 0; i < n; i++) {
    (quotesByReview[rids[i]] = quotesByReview[rids[i]] || []).push(cluster.representative_quotes[i]);
  }
  for (const v of reconciled.ranked_violations) {
    const ids = v.evidence_review_ids || [];
    if (!ids.length) {
      lines.push(`- \`${v.heuristic}\`: no quote cited (artifact-only evidence)`);
      continue;
    }
    const refs = ids.map((rid: string): string => {
      const qs = quotesByReview[rid] || [];
      const shown = qs.map((q: string): string => `“${q}”`).join("; ");
      return `\`${rid}\`` + (shown ? ` — ${shown}` : "");
    });
    lines.push(`- \`${v.heuristic}\`: ` + refs.join(" · "));
  }
  lines.push("");
  return lines;
}

// _build_brief
export function brRender(p: BrPayload): string {
  const cluster = p.cluster;
  const reconciled = p.reconciled;
  const priority = p.priority;
  const decision = p.decision;
  const verify = p.verify_payload === undefined ? null : p.verify_payload;
  const bodies = p.artifact_bodies || {};
  const prefix = `iteration__${cluster.cluster_id}__`;
  const iterations = p.iterations.filter((it) => it.iteration_id.indexOf(prefix) === 0);
  if (!iterations.length) throw new Error(`no iterations for ${cluster.cluster_id}`);

  const baseline: { [h: string]: number } = {};
  for (const v of reconciled.ranked_violations) baseline[v.heuristic] = Math.trunc(v.severity);
  const baselineSum = brSum(baseline);
  const nBaseline = Object.keys(baseline).length;

  // Python sorted() is stable; Array.prototype.sort is stable in ES2019.
  const itersSorted = iterations.slice().sort((a, b) => a.iteration_index - b.iteration_index);
  let finalAccepted: OptimizationIteration | null = null;
  for (let i = itersSorted.length - 1; i >= 0; i--) {
    if (itersSorted[i].accepted) {
      finalAccepted = itersSorted[i];
      break;
    }
  }
  if (finalAccepted === null) throw new Error("no accepted iteration found");
  const finalScores = finalAccepted.scores["reconciled"] || {};
  const finalSum = Object.keys(finalScores).length ? brSum(finalScores) : baselineSum;
  const reductionPct = (100.0 * (baselineSum - finalSum)) / Math.max(1, baselineSum);

  const ge = (verify && (verify["grounded_evidence"] as { [k: string]: { [k: string]: unknown } })) || {};
  const counts: { [k: string]: number } = { confirmed: 0, partial: 0, refuted: 0 };
  for (const slug of Object.keys(ge)) {
    const c = (ge[slug] || {})["confirmed"];
    if (typeof c === "string" && Object.prototype.hasOwnProperty.call(counts, c)) counts[c] += 1;
  }
  const verifiedTotal = counts.confirmed + counts.partial + counts.refuted;
  const groundedRatio = (counts.confirmed + 0.5 * counts.partial) / Math.max(1, verifiedTotal);
  const loopConverged = itersSorted.some((it) => it.iteration_index >= 2 && it.accepted);
  const rejected = itersSorted.filter((it) => !it.accepted);
  const chainPerFinding = reconciled.ranked_violations.some((v) => (v.evidence_review_ids || []).length > 0);

  const lines: string[] = [];
  lines.push(
    `# Design brief — ${cluster.cluster_id}`,
    "",
    `**Label:** ${cluster.label}`,
    "",
    `**Pipeline model:** \`${p.model_short}\` · ` +
      `**loop verifier:** \`${p.loop_verifier}\` · ` +
      `**cluster members:** ${cluster.member_review_ids.length} reviews · ` +
      `**baseline heuristics:** ${nBaseline}`,
    "",
    `**Generated:** ${p.generated_at || brNowIso()}`,
    "",
    "---",
    ""
  );

  let exec =
    `Users of **${cluster.label.toLowerCase()}** report friction ` +
    `captured as ${nBaseline} named heuristic ` +
    `violations across six design lenses ` +
    `(Norman / WCAG / Kahneman / Osterwalder / Cooper / ` +
    `Garrett), with reconciled severity sum **${baselineSum}**. ` +
    `The pipeline proposes a direction (L7) that, refined ` +
    `through iterative self-verification (L8 loop), drives the ` +
    `measured severity to **${finalSum}** ` +
    `(**${brFixed(reductionPct, 0)}% reduction** on the same ` +
    `heuristic list).`;
  if (verify !== null) {
    exec +=
      ` Real-product verification against product screenshots ` +
      `confirms ${counts.confirmed} of ` +
      `${verifiedTotal} heuristics, softens ` +
      `${counts.partial} to partial, and refutes ` +
      `${counts.refuted} as a review-inferred ` +
      `false-positive — a correction the pipeline could not ` +
      `make from review text alone.`;
  }
  exec += " Self-verified (ensemble-internal); external validation remains the design team's responsibility.";
  lines.push("## Executive summary", "", exec, "", "---", "");

  lines.push(
    "## User pain signal",
    "",
    `Cluster aggregates **${cluster.member_review_ids.length} ` +
      `reviews** with ${cluster.representative_quotes.length} ` +
      `representative quotes captured by L3b.`,
    ""
  );
  if (cluster.ui_context) lines.push(`**UI context (as identified by L3b):** ${cluster.ui_context}`, "");
  lines.push("**Representative user quotes:**", "");
  for (const q of cluster.representative_quotes) lines.push(`> ${q}`, "");
  lines.push("**Informing review IDs:**", "");
  for (const rid of cluster.member_review_ids) lines.push(`- \`${rid}\``);
  lines.push("", "---", "");

  lines.push(
    "## Measured pain spaces",
    "",
    "L5 reconciliation across six lenses produced the ranked " +
      "list below. Severities use the ADR-008 anchored scale " +
      "(`{0, 3, 5, 7, 9}`) designed for cross-run " +
      "reproducibility rather than calibrated intensity.",
    ""
  );
  if (verify !== null) {
    lines.push(
      "Grounded evidence from product screenshots is attached per heuristic (confirmed / partial / refuted).",
      "",
      "| Heuristic | L5 sev | Grounded verdict | Adjusted sev | Evidence |",
      "|---|---|---|---|---|"
    );
    for (const v of reconciled.ranked_violations) {
      const entry = ge[v.heuristic] || {};
      const verdict = Object.prototype.hasOwnProperty.call(entry, "confirmed") ? brPyStr(entry["confirmed"]) : "—";
      const adj = Object.prototype.hasOwnProperty.call(entry, "adjusted_severity")
        ? brPyStr(entry["adjusted_severity"])
        : String(v.severity);
      const evRaw = Object.prototype.hasOwnProperty.call(entry, "evidence") ? brPyStr(entry["evidence"]) : "";
      const ev = evRaw.split("|").join("\\|").split("\n").join(" ");
      lines.push(`| \`${v.heuristic}\` | ${v.severity} | ${verdict} | ${adj} | ${ev || "—"} |`);
    }
  } else {
    lines.push("| Heuristic | L5 sev | Violation |", "|---|---|---|");
    for (const v of reconciled.ranked_violations) {
      let viol = v.violation.split("|").join("\\|").split("\n").join(" ");
      viol = brCpSlice(viol, 180) + (brCpLen(viol) > 180 ? "…" : "");
      lines.push(`| \`${v.heuristic}\` | ${v.severity} | ${viol} |`);
    }
  }
  lines.push("", `**Baseline severity sum:** ${baselineSum}`, "");
  for (const l of brEvidencePerFinding(cluster, reconciled)) lines.push(l);
  if (reconciled.tensions.length) {
    lines.push("**Skill tensions surfaced by reconciliation:**", "");
    for (const t of reconciled.tensions) {
      lines.push(`- \`${t.skill_a}\` ↔ \`${t.skill_b}\` on *${t.axis}* — resolved: ${t.resolution}`);
    }
    lines.push("");
  }
  lines.push("---", "");

  lines.push(
    "## Priority reasoning",
    "",
    "L6 weights the cluster on five dimensions (anchored 0–10).",
    "",
    "| Dimension | Score | Meta-weight |",
    "|---|---|---|"
  );
  for (const dim of Object.keys(priority.dimensions)) {
    const w = Object.prototype.hasOwnProperty.call(priority.meta_weights, dim) ? priority.meta_weights[dim] : 0.0;
    lines.push(`| ${dim} | ${priority.dimensions[dim]} | ${brFixed(w, 2)} |`);
  }
  lines.push(
    "",
    `**Weighted total:** ${brFixed(priority.weighted_total, 1)} · ` +
      `validation passes: ${priority.validation_passes} · ` +
      `validation delta: ${brFixed(priority.validation_delta, 2)}`,
    "",
    "---",
    ""
  );

  lines.push(
    "## Validated direction",
    "",
    "L7 proposed a design decision for the highest-priority " +
      "pain space. L8 loop then refined the decision through " +
      "iterative self-verification: the loop's final accepted " +
      "iteration (iter " +
      `${brPad2(finalAccepted.iteration_index)}) drops the ` +
      `measured severity from **${baselineSum}** to ` +
      `**${finalSum}**.`,
    "",
    "### Before (current product state, per L7)",
    "",
    `> ${decision.before_snapshot}`,
    "",
    "### After (validated direction)",
    ""
  );
  lines.push(`> ${brRecoverSnapshot(finalAccepted, decision, bodies)}`, "");
  lines.push(
    "### Per-heuristic delta (L5 baseline → loop final)",
    "",
    "| Heuristic | Baseline | Final | Δ |",
    "|---|---|---|---|"
  );
  for (const v of reconciled.ranked_violations) {
    const b = baseline[v.heuristic];
    const f = Object.prototype.hasOwnProperty.call(finalScores, v.heuristic) ? finalScores[v.heuristic] : b;
    const delta = f - b;
    lines.push(`| \`${v.heuristic}\` | ${b} | ${f} | ${delta >= 0 ? "+" : ""}${delta} |`);
  }
  lines.push(
    "",
    `**Severity reduction:** ${baselineSum - finalSum} units (${brFixed(reductionPct, 0)}%).`,
    "",
    "**Resolves heuristics (per L7 decision):**",
    ""
  );
  for (const h of decision.resolves_heuristics) lines.push(`- \`${h}\``);
  lines.push("", "---", "");

  if (verify !== null && verify["summary"]) {
    lines.push(
      "## Out-of-baseline observations",
      "",
      "Real-product verification can surface defects that the " +
        "review-inferred heuristic list did not name. These are " +
        "candidates for inclusion in the next clustering cycle.",
      "",
      "> " + String(verify["summary"]).split("\n").join("\n> "),
      "",
      "---",
      ""
    );
  }

  lines.push(
    "## Audit trail — iteration log",
    "",
    "Every iteration the loop produced is recorded below, " +
      "including rejected attempts. This is the transparency " +
      "guarantee: the designer can see not only the final " +
      "direction but also what the pipeline tried and why each " +
      "attempt was accepted or rejected.",
    "",
    "| Iter | Status | Severity sum | Parent | Notes |",
    "|---|---|---|---|---|"
  );
  for (const it of itersSorted) {
    const sevSum = brSum(it.scores["reconciled"] || {});
    const status = it.accepted ? "✓ accepted" : "✗ rejected";
    const parent = it.parent_iteration_id || "—";
    let note = (it.reasoning || it.regression_reason || "").split("|").join("\\|").split("\n").join(" ");
    note = brCpSlice(note, 120) + (brCpLen(note) > 120 ? "…" : "");
    lines.push(`| ${brPad2(it.iteration_index)} | ${status} | ${sevSum} | \`${parent}\` | ${note} |`);
  }
  if (rejected.length) {
    lines.push("", "**Rejected-iteration reasons (verifier verdict):**", "");
    for (const it of rejected) {
      const reason = (it.regression_reason || "").split("\n").join(" ");
      lines.push(`- iter ${brPad2(it.iteration_index)} → ${reason || "(no reason recorded)"}`);
    }
  }
  lines.push("", "---", "");

  lines.push(
    "## Signal quality indicators",
    "",
    "These are transparent components, not a rollup score. The designer weights them based on context.",
    "",
    `- **Severity reduction**: ${brFixed(reductionPct, 0)}% (${baselineSum} → ${finalSum})`,
    `- **Loop convergence**: ${loopConverged ? "converged" : "stalled"} · ` +
      `${itersSorted.length} total iterations · ${rejected.length} rejected`
  );
  if (verify !== null) {
    lines.push(
      `- **Grounded-evidence ratio**: ${counts.confirmed} confirmed / ${counts.partial} partial / ` +
        `${counts.refuted} refuted (weighted score: ${brFixed(groundedRatio * 100, 0)}%)`
    );
  } else {
    lines.push(
      "- **Real-product grounding**: not run for this cluster (pipeline operated on L7 prose snapshots only)"
    );
  }
  lines.push("", "---", "");

  lines.push(
    "## Handoff — what the designer owns next",
    "",
    "This brief is **direction, not specification**. Translate " +
      "into wireframes, components, and flows in the tooling your " +
      "team uses (Claude design, Figma, Linear). The agent does " +
      "not commit, does not merge, does not ship — the work " +
      "starts here and is owned by human design/engineering.",
    "",
    "**What the brief guarantees:**",
    "",
    chainPerFinding
      ? "- Every finding that cites user quotes has a typed chain " +
          "back to the reviews those quotes come from (see *Evidence " +
          "per finding*); findings without quotes rest on the product " +
          "artifact and are marked as such. Where run, real-product " +
          "screenshots add a second, grounded check."
      : "- Every finding traces back to this cluster's member " +
          "reviews and representative quotes, and (where run) " +
          "real-product screenshots. Per-finding review ids are not " +
          "available for this run.",
    "- The validated direction self-verifies (ensemble-internal) " +
      "against the same heuristic baseline — i.e. the re-audit " +
      "confirms the direction reduces measured pain.",
    "- Every rejected loop attempt is preserved above for audit.",
    "",
    "**What the brief does NOT guarantee:**",
    "",
    "- Real-user validation (A/B testing, longitudinal study).",
    "- Implementation feasibility in the team's tech stack.",
    "- Aesthetic / brand fit with the product's visual system.",
    "- That the direction is the *only* direction that would " +
      "work — it is one validated direction, not the space of " +
      "valid directions.",
    "",
    "Designers should feel free to reject the direction on any " +
      "of the above axes. The pipeline's job is to ensure " +
      "rejection happens on the basis of visible evidence, not " +
      "blind trust in either side.",
    "",
    "---",
    ""
  );

  lines.push("## Provenance", "", "| Layer | Input file | sha256 |", "|---|---|---|");
  for (const inp of p.inputs || []) {
    const sha = inp.sha256 ? inp.sha256.slice(0, 16) : "missing";
    lines.push(`| ${inp.label} | \`${inp.path}\` | \`${sha}\` |`);
  }
  lines.push("", "");
  return lines.join("\n");
}
