// L4 — six audit lenses. Port of src/auditable_design/layers/l4_audit*.py:
// build_user_message, parse_audit_response (+ _validate_finding),
// _build_heuristic_violations and the verdict assembly in audit_cluster.
//
// Lens ids are the skill ids. Every top-level name is prefixed with l4/L4
// because the Office Script bundle is a single flat file.

import { InsightCluster, HeuristicViolation, AuditVerdict } from "./types";
import { escapeXml, extractJsonObjectText, parseModelJson, evidenceReviewIdsFor } from "./common";

// ---------------------------------------------------------------------------
// Python-compatible formatting (repr, sorted, type names)
// ---------------------------------------------------------------------------

// Python repr() for the JSON-shaped values the parsers print with !r.
export function l4PyRepr(v: unknown): string {
  if (v === null || v === undefined) return "None";
  if (typeof v === "boolean") return v ? "True" : "False";
  if (typeof v === "number") return l4PyNum(v);
  if (typeof v === "string") return l4PyReprStr(v);
  if (Array.isArray(v)) return "[" + v.map((x: unknown): string => l4PyRepr(x)).join(", ") + "]";
  const o = v as { [k: string]: unknown };
  return (
    "{" +
    Object.keys(o)
      .map((k: string): string => l4PyReprStr(k) + ": " + l4PyRepr(o[k]))
      .join(", ") +
    "}"
  );
}

export function l4PyTuple(xs: unknown[]): string {
  return "(" + xs.map((x: unknown): string => l4PyRepr(x)).join(", ") + (xs.length === 1 ? ",)" : ")");
}

function l4PyNum(n: number): string {
  // JSON.parse cannot tell 3 from 3.0; integers print as Python ints.
  if (Number.isInteger(n)) return String(n);
  return String(n);
}

const L4_NONPRINTABLE = /[\p{Cc}\p{Cf}\p{Co}\p{Cn}\p{Cs}\p{Zl}\p{Zp}\p{Zs}]/u;

// Python str.__repr__: single quotes unless the text has ' and no ".
export function l4PyReprStr(s: string): string {
  const quote = s.indexOf("'") >= 0 && s.indexOf('"') < 0 ? '"' : "'";
  let out = quote;
  for (const ch of Array.from(s)) {
    const cp = ch.codePointAt(0) as number;
    if (ch === "\\") out += "\\\\";
    else if (ch === quote) out += "\\" + quote;
    else if (ch === "\n") out += "\\n";
    else if (ch === "\r") out += "\\r";
    else if (ch === "\t") out += "\\t";
    else if (ch !== " " && L4_NONPRINTABLE.test(ch)) {
      if (cp < 0x100) out += "\\x" + l4Hex(cp, 2);
      else if (cp < 0x10000) out += "\\u" + l4Hex(cp, 4);
      else out += "\\U" + l4Hex(cp, 8);
    } else out += ch;
  }
  return out + quote;
}

function l4Hex(n: number, width: number): string {
  let h = n.toString(16);
  while (h.length < width) h = "0" + h;
  return h;
}

// Python type(x).__name__ for JSON values.
export function l4PyType(v: unknown): string {
  if (v === null || v === undefined) return "NoneType";
  if (typeof v === "boolean") return "bool";
  if (typeof v === "number") return Number.isInteger(v) ? "int" : "float";
  if (typeof v === "string") return "str";
  if (Array.isArray(v)) return "list";
  return "dict";
}

function l4Sorted(xs: string[]): string {
  return l4PyRepr(xs.slice().sort());
}

function l4IsDict(v: unknown): boolean {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// Python: isinstance(v, int) and not isinstance(v, bool)
function l4IsPyInt(v: unknown): boolean {
  return typeof v === "number" && Number.isInteger(v);
}

class L4AuditParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "L4AuditParseError";
  }
}

function l4Fail(msg: string): never {
  throw new L4AuditParseError(msg);
}

// ---------------------------------------------------------------------------
// Lens registry
// ---------------------------------------------------------------------------

export const L4_NIELSEN_TO_ANCHORED: { [k: number]: number } = { 1: 3, 2: 5, 3: 7, 4: 9 };
const L4_VALID_SEVERITIES: number[] = [1, 2, 3, 4];
const L4_VALID_DIMENSION_SCORES: number[] = [1, 2, 3, 4, 5];
const L4_TOP_LEVEL_KEYS: string[] = ["summary", "dimension_scores", "findings"];
const L4_VALID_EVIDENCE_SOURCES: string[] = ["quotes", "ui_context", "html", "screenshot"];

type L4Finding = { [k: string]: unknown };
type L4Payload = { summary: string; dimension_scores: { [k: string]: number }; findings: L4Finding[] };

interface L4LensSpec {
  skillId: string;
  layerName: string;
  nativeFileName: string; // DEFAULT_NATIVE.name in the Python module
  model: string; // Python default MODEL (informational)
  maxTokens: number;
  dimensionKeys: string[];
  findingKeys: string[];
  withArtifacts: boolean; // html / screenshot_ref in the message (all but Norman)
  repairJson: boolean; // _repair_unescaped_string_quotes (all but Norman)
  validateFinding: (f: L4Finding, i: number, nQuotes: number) => void;
  crossChecks: (findings: L4Finding[], dscores: { [k: string]: number }) => void;
  reasoning: (f: L4Finding, cluster: InsightCluster, nielsen: number, anchored: number) => string;
}

export const L4_LENS_IDS: string[] = [
  "audit-usability-fundamentals",
  "audit-accessibility",
  "audit-business-alignment",
  "audit-decision-psychology",
  "audit-interaction-design",
  "audit-ux-architecture",
];

// ---------------------------------------------------------------------------
// Shared finding validators (same wording as the Python parsers)
// ---------------------------------------------------------------------------

function l4CheckFindingShape(f: unknown, i: number, keys: string[], dims: string[]): L4Finding {
  if (!l4IsDict(f)) l4Fail(`findings[${i}] must be dict, got ${l4PyType(f)}`);
  const finding = f as L4Finding;
  const actual = Object.keys(finding);
  const missing = keys.filter((k: string): boolean => actual.indexOf(k) < 0);
  if (missing.length) l4Fail(`findings[${i}] missing keys: ${l4Sorted(missing)}`);
  const extra = actual.filter((k: string): boolean => keys.indexOf(k) < 0);
  if (extra.length) l4Fail(`findings[${i}] unexpected keys: ${l4Sorted(extra)}`);
  const dim = finding["dimension"];
  if (typeof dim !== "string" || dims.indexOf(dim) < 0) {
    l4Fail(`findings[${i}].dimension=${l4PyRepr(dim)} not in ${l4Sorted(dims)}`);
  }
  return finding;
}

function l4CheckStrings(f: L4Finding, i: number, keys: string[]): void {
  for (const k of keys) {
    const v = f[k];
    if (typeof v !== "string") l4Fail(`findings[${i}].${k} must be str, got ${l4PyType(v)}`);
    if (!(v as string).trim()) l4Fail(`findings[${i}].${k} must be non-empty`);
  }
}

function l4CheckSeverity(f: L4Finding, i: number): number {
  const sev = f["severity"];
  if (!l4IsPyInt(sev)) l4Fail(`findings[${i}].severity must be int, got ${l4PyType(sev)}`);
  if (L4_VALID_SEVERITIES.indexOf(sev as number) < 0) l4Fail(`findings[${i}].severity=${sev} out of {1,2,3,4}`);
  return sev as number;
}

function l4CheckEnum(f: L4Finding, i: number, key: string, allowed: string[]): void {
  const v = f[key];
  if (typeof v !== "string") l4Fail(`findings[${i}].${key} must be str, got ${l4PyType(v)}`);
  if (allowed.indexOf(v as string) < 0) l4Fail(`findings[${i}].${key}=${l4PyRepr(v)} not in ${l4Sorted(allowed)}`);
}

function l4CheckEvidenceSource(f: L4Finding, i: number): string[] {
  const es = f["evidence_source"];
  if (!Array.isArray(es)) l4Fail(`findings[${i}].evidence_source must be list, got ${l4PyType(es)}`);
  const esources = es as unknown[];
  if (!esources.length) l4Fail(`findings[${i}].evidence_source must be non-empty`);
  esources.forEach((src: unknown, j: number): void => {
    if (typeof src !== "string") l4Fail(`findings[${i}].evidence_source[${j}] must be str, got ${l4PyType(src)}`);
    if (L4_VALID_EVIDENCE_SOURCES.indexOf(src as string) < 0) {
      l4Fail(`findings[${i}].evidence_source[${j}]=${l4PyRepr(src)} not in ${l4Sorted(L4_VALID_EVIDENCE_SOURCES)}`);
    }
  });
  const uniq = esources.filter((x: unknown, k: number): boolean => esources.indexOf(x) === k);
  if (uniq.length !== esources.length) {
    l4Fail(`findings[${i}].evidence_source contains duplicates: ${l4PyRepr(esources)}`);
  }
  return esources as string[];
}

function l4CheckIdxList(f: L4Finding, i: number): unknown[] {
  const idxs = f["evidence_quote_idxs"];
  if (!Array.isArray(idxs)) l4Fail(`findings[${i}].evidence_quote_idxs must be list, got ${l4PyType(idxs)}`);
  return idxs as unknown[];
}

function l4CheckIdxRange(idxs: unknown[], i: number, nQuotes: number): void {
  idxs.forEach((idx: unknown, j: number): void => {
    if (!l4IsPyInt(idx)) l4Fail(`findings[${i}].evidence_quote_idxs[${j}] must be int, got ${l4PyType(idx)}`);
    const n = idx as number;
    if (!(0 <= n && n < nQuotes)) l4Fail(`findings[${i}].evidence_quote_idxs[${j}]=${n} out of [0, ${nQuotes})`);
  });
}

// Coupling rule shared by Cooper / Garrett / Osterwalder.
function l4CheckQuotesCoupling(idxs: unknown[], esources: string[], i: number): void {
  const has = esources.indexOf("quotes") >= 0;
  if (has && !idxs.length) {
    l4Fail(
      `findings[${i}].evidence_source includes 'quotes' but evidence_quote_idxs is empty — SKILL.md rule: 'quotes' in evidence_source requires non-empty quote idxs`
    );
  }
  if (idxs.length && !has) {
    l4Fail(
      `findings[${i}].evidence_quote_idxs=${l4PyRepr(idxs)} is non-empty but 'quotes' is not in evidence_source=${l4PyRepr(
        esources
      )} — SKILL.md rule: non-empty quote idxs requires 'quotes' in evidence_source`
    );
  }
}

function l4QuoteRefs(f: L4Finding, cluster: InsightCluster): string {
  return (f["evidence_quote_idxs"] as number[])
    .map((idx: number): string => `q[${idx}]=${l4PyReprStr(cluster.representative_quotes[idx])}`)
    .join("; ");
}

function l4Tail(nielsen: number, anchored: number): string {
  return `(severity: Nielsen ${nielsen} → anchored ${anchored})`;
}

// Cross-finding rule: "<condition> at sev >= 3 forces dimension <= 2".
function l4ForceDimension(
  findings: L4Finding[],
  dscores: { [k: string]: number },
  when: (f: L4Finding) => boolean,
  message: (f: L4Finding, i: number, dim: string, score: number) => string
): void {
  findings.forEach((f: L4Finding, i: number): void => {
    if (when(f) && (f["severity"] as number) >= 3) {
      const dim = f["dimension"] as string;
      const score = dscores[dim];
      if (score > 2) l4Fail(message(f, i, dim, score));
    }
  });
}

function l4NoDuplicatePairs(findings: L4Finding[], second: string): void {
  const seen: string[] = [];
  findings.forEach((f: L4Finding, i: number): void => {
    const key = JSON.stringify([f["heuristic"], f[second]]);
    if (seen.indexOf(key) >= 0) {
      l4Fail(
        `findings[${i}] repeats (heuristic, ${second}) pair ${l4PyTuple([f["heuristic"], f[second]])} — SKILL.md forbids duplicates`
      );
    }
    seen.push(key);
  });
}

// ---------------------------------------------------------------------------
// Lens specs
// ---------------------------------------------------------------------------

const L4_NORMAN: L4LensSpec = {
  skillId: "audit-usability-fundamentals",
  layerName: "l4_audit",
  nativeFileName: "l4_audit_verdicts.native.jsonl",
  model: "claude-sonnet-4-6",
  maxTokens: 4096,
  dimensionKeys: ["interaction_fundamentals", "action_cognition", "error_architecture", "system_maturity"],
  findingKeys: ["dimension", "heuristic", "violation", "severity", "evidence_quote_idxs", "recommendation"],
  withArtifacts: false,
  repairJson: false,
  validateFinding(f0: L4Finding, i: number, nQuotes: number): void {
    const f = l4CheckFindingShape(f0, i, this.findingKeys, this.dimensionKeys);
    l4CheckStrings(f, i, ["heuristic", "violation", "recommendation"]);
    l4CheckSeverity(f, i);
    const idxs = l4CheckIdxList(f, i);
    if (!idxs.length) {
      l4Fail(`findings[${i}].evidence_quote_idxs is empty; every finding must be anchored to at least one quote`);
    }
    l4CheckIdxRange(idxs, i, nQuotes);
  },
  crossChecks(): void {},
  reasoning(f: L4Finding, cluster: InsightCluster, nielsen: number, anchored: number): string {
    return (
      `[${f["dimension"]}] ${f["violation"]} ` +
      `Recommendation: ${f["recommendation"]} ` +
      `Evidence: ${l4QuoteRefs(f, cluster)} ` +
      l4Tail(nielsen, anchored)
    );
  },
};

const L4_WCAG_REF_RE = /^\d+\.\d+\.\d+$/;

const L4_ACCESSIBILITY: L4LensSpec = {
  skillId: "audit-accessibility",
  layerName: "l4_audit_accessibility",
  nativeFileName: "l4_audit_accessibility_verdicts.native.jsonl",
  model: "claude-sonnet-4-6",
  maxTokens: 6144,
  dimensionKeys: ["perceivable", "operable", "understandable", "robust", "inclusive_cognitive"],
  findingKeys: [
    "dimension",
    "heuristic",
    "wcag_ref",
    "wcag_level",
    "violation",
    "severity",
    "evidence_source",
    "evidence_quote_idxs",
    "recommendation",
  ],
  withArtifacts: true,
  repairJson: true,
  validateFinding(f0: L4Finding, i: number, nQuotes: number): void {
    const f = l4CheckFindingShape(f0, i, this.findingKeys, this.dimensionKeys);
    l4CheckStrings(f, i, ["heuristic", "violation", "recommendation"]);
    const sev = l4CheckSeverity(f, i);
    const levels = ["A", "AA", "AAA", "inclusive"];
    const level = f["wcag_level"];
    if (typeof level !== "string" || levels.indexOf(level) < 0) {
      l4Fail(`findings[${i}].wcag_level=${l4PyRepr(level)} not in ${l4Sorted(levels)}`);
    }
    const wref = f["wcag_ref"];
    if (level === "inclusive") {
      if (wref !== null) {
        l4Fail(`findings[${i}].wcag_ref=${l4PyRepr(wref)} must be null when wcag_level=='inclusive'`);
      }
    } else {
      if (typeof wref !== "string") {
        l4Fail(`findings[${i}].wcag_ref must be str for WCAG level ${l4PyRepr(level)}, got ${l4PyType(wref)}`);
      }
      // Python re.match with $ also accepts one trailing "\n".
      if (!L4_WCAG_REF_RE.test((wref as string).replace(/\n$/, ""))) {
        l4Fail(
          `findings[${i}].wcag_ref=${l4PyRepr(wref)} does not match '<principle>.<guideline>.<criterion>' (e.g. '1.4.3')`
        );
      }
      if (wref === "4.1.1") {
        l4Fail(`findings[${i}].wcag_ref=${l4PyRepr(wref)} is obsolete in WCAG 2.2 and must not be cited (SKILL.md rule)`);
      }
    }
    if (level === "AAA" && sev !== 1) {
      l4Fail(
        `findings[${i}] has wcag_level='AAA' with severity=${sev}; SKILL.md requires AAA findings to carry severity 1 (advisory)`
      );
    }
    const esources = l4CheckEvidenceSource(f, i);
    const idxs = l4CheckIdxList(f, i);
    const has = esources.indexOf("quotes") >= 0;
    if (has && !idxs.length) {
      l4Fail(`findings[${i}].evidence_source includes 'quotes' but evidence_quote_idxs is empty`);
    }
    if (!has && idxs.length) {
      l4Fail(
        `findings[${i}].evidence_source does not include 'quotes' but evidence_quote_idxs=${l4PyRepr(idxs)} is non-empty`
      );
    }
    l4CheckIdxRange(idxs, i, nQuotes);
  },
  crossChecks(): void {},
  reasoning(f: L4Finding, cluster: InsightCluster, nielsen: number, anchored: number): string {
    const level = f["wcag_level"] as string;
    const sources = f["evidence_source"] as string[];
    const levelTag = level === "inclusive" ? "(inclusive)" : `(WCAG ${f["wcag_ref"]} ${level})`;
    const idxs = f["evidence_quote_idxs"] as number[];
    const inner = idxs.length
      ? l4QuoteRefs(f, cluster)
      : "(no quote anchor — finding observed from " +
        sources.filter((s: string): boolean => s !== "quotes").join("+") +
        ")";
    return (
      `[${f["dimension"]}] ${levelTag} ${f["violation"]} ` +
      `Recommendation: ${f["recommendation"]} ` +
      `Evidence (${sources.join("+")}): ${inner} ` +
      l4Tail(nielsen, anchored)
    );
  },
};

const L4_BLOCKS: string[] = ["cs", "vp", "ch", "cr", "r_dollar", "kr", "ka", "kp", "c_dollar"];
const L4_PATTERNS: string[] = [
  "multi_sided",
  "freemium",
  "long_tail",
  "subscription",
  "unbundled",
  "open",
  "none_identified",
];

const L4_BUSINESS: L4LensSpec = {
  skillId: "audit-business-alignment",
  layerName: "l4_audit_business_alignment",
  nativeFileName: "l4_audit_business_alignment_verdicts.native.jsonl",
  model: "claude-sonnet-4-6",
  maxTokens: 6144,
  dimensionKeys: ["value_delivery", "revenue_relationships", "infrastructure_fit", "pattern_coherence"],
  findingKeys: [
    "dimension",
    "heuristic",
    "building_blocks",
    "tension",
    "pattern",
    "violation",
    "severity",
    "evidence_source",
    "evidence_quote_idxs",
    "recommendation",
  ],
  withArtifacts: true,
  repairJson: true,
  validateFinding(f0: L4Finding, i: number, nQuotes: number): void {
    const f = l4CheckFindingShape(f0, i, this.findingKeys, this.dimensionKeys);
    l4CheckStrings(f, i, ["heuristic", "violation", "recommendation"]);
    const bb = f["building_blocks"];
    if (!Array.isArray(bb)) l4Fail(`findings[${i}].building_blocks must be list, got ${l4PyType(bb)}`);
    const bblocks = bb as unknown[];
    if (!bblocks.length) {
      l4Fail(
        `findings[${i}].building_blocks must be non-empty (SKILL.md rule: every finding names at least one block)`
      );
    }
    bblocks.forEach((b: unknown, j: number): void => {
      if (typeof b !== "string") l4Fail(`findings[${i}].building_blocks[${j}] must be str, got ${l4PyType(b)}`);
      if (L4_BLOCKS.indexOf(b as string) < 0) {
        l4Fail(`findings[${i}].building_blocks[${j}]=${l4PyRepr(b)} not in ${l4Sorted(L4_BLOCKS)}`);
      }
    });
    if (bblocks.filter((x: unknown, k: number): boolean => bblocks.indexOf(x) === k).length !== bblocks.length) {
      l4Fail(`findings[${i}].building_blocks contains duplicates: ${l4PyRepr(bblocks)}`);
    }
    const t = f["tension"];
    if (!Array.isArray(t)) l4Fail(`findings[${i}].tension must be list, got ${l4PyType(t)}`);
    const tension = t as unknown[];
    if (tension.length) {
      if (tension.length !== 2) {
        l4Fail(
          `findings[${i}].tension must be either [] (single-block) or a 2-element list; got length ${tension.length}: ${l4PyRepr(
            tension
          )}`
        );
      }
      tension.forEach((x: unknown, j: number): void => {
        if (typeof x !== "string") l4Fail(`findings[${i}].tension[${j}] must be str, got ${l4PyType(x)}`);
        if (L4_BLOCKS.indexOf(x as string) < 0) {
          l4Fail(`findings[${i}].tension[${j}]=${l4PyRepr(x)} not in ${l4Sorted(L4_BLOCKS)}`);
        }
      });
      const a = tension[0] as string;
      const b = tension[1] as string;
      if (a === b) l4Fail(`findings[${i}].tension must be two distinct blocks, got ${l4PyRepr(tension)}`);
      if (a >= b) {
        l4Fail(
          `findings[${i}].tension=${l4PyRepr(
            tension
          )} not in lexicographic order (SKILL.md rule: tension pair must be lex-ordered so parser can dedupe (a,b) from (b,a))`
        );
      }
      const missingInBb = (tension as string[]).filter((x: string): boolean => bblocks.indexOf(x) < 0);
      if (missingInBb.length) {
        l4Fail(
          `findings[${i}].tension=${l4PyRepr(tension)} names blocks ${l4PyRepr(
            missingInBb
          )} not present in building_blocks=${l4PyRepr(
            bblocks
          )} (a tension must be between blocks the finding already names)`
        );
      }
    }
    l4CheckEnum(f, i, "pattern", L4_PATTERNS);
    l4CheckSeverity(f, i);
    const esources = l4CheckEvidenceSource(f, i);
    const idxs = l4CheckIdxList(f, i);
    l4CheckQuotesCoupling(idxs, esources, i);
    l4CheckIdxRange(idxs, i, nQuotes);
  },
  crossChecks(findings: L4Finding[], dscores: { [k: string]: number }): void {
    l4ForceDimension(
      findings,
      dscores,
      (f: L4Finding): boolean => (f["tension"] as string[]).length > 0,
      (f: L4Finding, i: number, dim: string, score: number): string =>
        `findings[${i}] has tension=${l4PyRepr(f["tension"])} at severity ${f["severity"]} in dimension ${l4PyRepr(
          dim
        )}, but dimension_scores[${l4PyRepr(
          dim
        )}]=${score} > 2 (SKILL.md rule: cross-block tension at sev ≥ 3 forces dimension ≤ 2)`
    );
    // (heuristic, tuple(tension)) duplicates
    const seen: string[] = [];
    findings.forEach((f: L4Finding, i: number): void => {
      const key = JSON.stringify([f["heuristic"], f["tension"]]);
      if (seen.indexOf(key) >= 0) {
        const pair = "(" + l4PyRepr(f["heuristic"]) + ", " + l4PyTuple(f["tension"] as unknown[]) + ")";
        l4Fail(`findings[${i}] repeats (heuristic, tension) pair ${pair} — SKILL.md forbids duplicates`);
      }
      seen.push(key);
    });
  },
  reasoning(f: L4Finding, cluster: InsightCluster, nielsen: number, anchored: number): string {
    const tension = f["tension"] as string[];
    const tensionTag = tension.length ? `tension: ${tension[0]}↔${tension[1]}` : "tension: none";
    const inner = l4QuoteRefs(f, cluster);
    return (
      `[${f["dimension"]}] (blocks: ${(f["building_blocks"] as string[]).join(",")}; ${tensionTag}; pattern: ${f["pattern"]}) ` +
      `${f["violation"]} ` +
      `Recommendation: ${f["recommendation"]} ` +
      `Evidence (${(f["evidence_source"] as string[]).join("+")}): ${inner || "—"} ` +
      l4Tail(nielsen, anchored)
    );
  },
};

const L4_DECISION: L4LensSpec = {
  skillId: "audit-decision-psychology",
  layerName: "l4_audit_decision_psychology",
  nativeFileName: "l4_audit_decision_psychology_verdicts.native.jsonl",
  model: "claude-sonnet-4-6",
  maxTokens: 6144,
  dimensionKeys: ["cognitive_load_ease", "choice_architecture", "judgment_heuristics", "temporal_experience"],
  findingKeys: [
    "dimension",
    "heuristic",
    "mechanism",
    "intent",
    "violation",
    "severity",
    "evidence_source",
    "evidence_quote_idxs",
    "recommendation",
  ],
  withArtifacts: true,
  repairJson: true,
  validateFinding(f0: L4Finding, i: number, nQuotes: number): void {
    const f = l4CheckFindingShape(f0, i, this.findingKeys, this.dimensionKeys);
    l4CheckStrings(f, i, ["heuristic", "mechanism", "violation", "recommendation"]);
    const intents = ["nudge", "dark_pattern", "unintentional", "absent"];
    const intent = f["intent"];
    if (typeof intent !== "string" || intents.indexOf(intent) < 0) {
      l4Fail(`findings[${i}].intent=${l4PyRepr(intent)} not in ${l4Sorted(intents)}`);
    }
    const sev = l4CheckSeverity(f, i);
    if (intent === "dark_pattern" && sev < 2) {
      l4Fail(
        `findings[${i}] is intent='dark_pattern' with severity=${sev}; SKILL.md requires dark_pattern findings to carry severity ≥ 2 (never cosmetic)`
      );
    }
    const esources = l4CheckEvidenceSource(f, i);
    if (esources.indexOf("quotes") < 0) {
      l4Fail(
        `findings[${i}].evidence_source=${l4PyRepr(
          esources
        )} does not include 'quotes'; decision-psychology findings must anchor to at least one quote (SKILL.md rule)`
      );
    }
    const idxs = l4CheckIdxList(f, i);
    if (!idxs.length) {
      l4Fail(
        `findings[${i}].evidence_quote_idxs is empty but 'quotes' is in evidence_source (SKILL.md rule: every decision-psychology finding needs at least one quote)`
      );
    }
    l4CheckIdxRange(idxs, i, nQuotes);
  },
  crossChecks(findings: L4Finding[], dscores: { [k: string]: number }): void {
    l4ForceDimension(
      findings,
      dscores,
      (f: L4Finding): boolean => f["intent"] === "dark_pattern",
      (f: L4Finding, i: number, dim: string, score: number): string =>
        `findings[${i}] is dark_pattern severity ${f["severity"]} in dimension ${l4PyRepr(dim)}, but dimension_scores[${l4PyRepr(
          dim
        )}]=${score} > 2 (SKILL.md rule: dark_pattern ≥ sev 3 forces dimension ≤ 2)`
    );
    l4NoDuplicatePairs(findings, "mechanism");
  },
  reasoning(f: L4Finding, cluster: InsightCluster, nielsen: number, anchored: number): string {
    return (
      `[${f["dimension"]}] (mechanism: ${f["mechanism"]}; intent: ${f["intent"]}) ` +
      `${f["violation"]} ` +
      `Recommendation: ${f["recommendation"]} ` +
      `Evidence (${(f["evidence_source"] as string[]).join("+")}): ${l4QuoteRefs(f, cluster)} ` +
      l4Tail(nielsen, anchored)
    );
  },
};

const L4_INTERACTION: L4LensSpec = {
  skillId: "audit-interaction-design",
  layerName: "l4_audit_interaction_design",
  nativeFileName: "l4_audit_interaction_design_verdicts.native.jsonl",
  model: "claude-sonnet-4-6",
  maxTokens: 6144,
  dimensionKeys: ["posture_platform_fit", "flow_excise", "idioms_learnability", "etiquette_forgiveness"],
  findingKeys: [
    "dimension",
    "heuristic",
    "posture",
    "user_tier",
    "excise_type",
    "violation",
    "severity",
    "evidence_source",
    "evidence_quote_idxs",
    "recommendation",
  ],
  withArtifacts: true,
  repairJson: true,
  validateFinding(f0: L4Finding, i: number, nQuotes: number): void {
    const f = l4CheckFindingShape(f0, i, this.findingKeys, this.dimensionKeys);
    l4CheckStrings(f, i, ["heuristic", "violation", "recommendation"]);
    l4CheckEnum(f, i, "posture", ["sovereign", "transient", "daemonic", "satellite", "standalone", "mixed", "not_applicable"]);
    l4CheckEnum(f, i, "user_tier", ["beginner", "intermediate", "expert", "all"]);
    l4CheckEnum(f, i, "excise_type", ["navigational", "modal", "skeuomorphic", "stylistic", "none"]);
    l4CheckSeverity(f, i);
    const esources = l4CheckEvidenceSource(f, i);
    const idxs = l4CheckIdxList(f, i);
    l4CheckQuotesCoupling(idxs, esources, i);
    l4CheckIdxRange(idxs, i, nQuotes);
  },
  crossChecks(findings: L4Finding[], dscores: { [k: string]: number }): void {
    l4ForceDimension(
      findings,
      dscores,
      (f: L4Finding): boolean => f["posture"] === "mixed",
      (f: L4Finding, i: number, dim: string, score: number): string =>
        `findings[${i}] has posture='mixed' at severity ${f["severity"]} in dimension ${l4PyRepr(
          dim
        )}, but dimension_scores[${l4PyRepr(dim)}]=${score} > 2 (SKILL.md rule: posture mismatch at sev ≥ 3 forces dimension ≤ 2)`
    );
    l4ForceDimension(
      findings,
      dscores,
      (f: L4Finding): boolean => f["excise_type"] !== "none",
      (f: L4Finding, i: number, dim: string, score: number): string =>
        `findings[${i}] has excise_type=${l4PyRepr(f["excise_type"])} at severity ${f["severity"]} in dimension ${l4PyRepr(
          dim
        )}, but dimension_scores[${l4PyRepr(dim)}]=${score} > 2 (SKILL.md rule: excise at sev ≥ 3 forces dimension ≤ 2)`
    );
    findings.forEach((f: L4Finding, i: number): void => {
      if (f["dimension"] === "flow_excise" && f["excise_type"] === "none") {
        l4Fail(
          `findings[${i}] is in dimension 'flow_excise' but excise_type='none' — SKILL.md rule: every finding in flow_excise must name a non-'none' excise_type`
        );
      }
    });
    l4NoDuplicatePairs(findings, "posture");
  },
  reasoning(f: L4Finding, cluster: InsightCluster, nielsen: number, anchored: number): string {
    const excise = f["excise_type"] as string;
    const exciseTag = excise !== "none" ? `excise: ${excise}` : "excise: none";
    const inner = l4QuoteRefs(f, cluster);
    return (
      `[${f["dimension"]}] (posture: ${f["posture"]}; tier: ${f["user_tier"]}; ${exciseTag}) ` +
      `${f["violation"]} ` +
      `Recommendation: ${f["recommendation"]} ` +
      `Evidence (${(f["evidence_source"] as string[]).join("+")}): ${inner || "—"} ` +
      l4Tail(nielsen, anchored)
    );
  },
};

const L4_UX_ARCHITECTURE: L4LensSpec = {
  skillId: "audit-ux-architecture",
  layerName: "l4_audit_ux_architecture",
  nativeFileName: "l4_audit_ux_architecture_verdicts.native.jsonl",
  model: "claude-sonnet-4-6",
  maxTokens: 6144,
  dimensionKeys: ["strategy_coherence", "scope_coverage", "structure_navigation", "skeleton_wireframe", "surface_sensory"],
  findingKeys: [
    "dimension",
    "heuristic",
    "product_type",
    "decision_mode",
    "violation",
    "severity",
    "evidence_source",
    "evidence_quote_idxs",
    "recommendation",
  ],
  withArtifacts: true,
  repairJson: true,
  validateFinding(f0: L4Finding, i: number, nQuotes: number): void {
    const f = l4CheckFindingShape(f0, i, this.findingKeys, this.dimensionKeys);
    l4CheckStrings(f, i, ["heuristic", "violation", "recommendation"]);
    l4CheckEnum(f, i, "product_type", ["functional", "informational", "hybrid", "not_applicable"]);
    l4CheckEnum(f, i, "decision_mode", ["conscious", "default", "mimicry", "fiat", "not_applicable"]);
    l4CheckSeverity(f, i);
    const esources = l4CheckEvidenceSource(f, i);
    const idxs = l4CheckIdxList(f, i);
    l4CheckQuotesCoupling(idxs, esources, i);
    l4CheckIdxRange(idxs, i, nQuotes);
  },
  crossChecks(findings: L4Finding[], dscores: { [k: string]: number }): void {
    l4ForceDimension(
      findings,
      dscores,
      (f: L4Finding): boolean => ["default", "mimicry", "fiat"].indexOf(f["decision_mode"] as string) >= 0,
      (f: L4Finding, i: number, dim: string, score: number): string =>
        `findings[${i}] has decision_mode=${l4PyRepr(f["decision_mode"])} at severity ${f["severity"]} in dimension ${l4PyRepr(
          dim
        )}, but dimension_scores[${l4PyRepr(
          dim
        )}]=${score} > 2 (SKILL.md rule: unconscious decision at sev ≥ 3 forces dimension ≤ 2)`
    );
    l4NoDuplicatePairs(findings, "product_type");
  },
  reasoning(f: L4Finding, cluster: InsightCluster, nielsen: number, anchored: number): string {
    const inner = l4QuoteRefs(f, cluster);
    return (
      `[${f["dimension"]}] (product_type: ${f["product_type"]}; decision_mode: ${f["decision_mode"]}) ` +
      `${f["violation"]} ` +
      `Recommendation: ${f["recommendation"]} ` +
      `Evidence (${(f["evidence_source"] as string[]).join("+")}): ${inner || "—"} ` +
      l4Tail(nielsen, anchored)
    );
  },
};

const L4_SPECS: { [skillId: string]: L4LensSpec } = {
  "audit-usability-fundamentals": L4_NORMAN,
  "audit-accessibility": L4_ACCESSIBILITY,
  "audit-business-alignment": L4_BUSINESS,
  "audit-decision-psychology": L4_DECISION,
  "audit-interaction-design": L4_INTERACTION,
  "audit-ux-architecture": L4_UX_ARCHITECTURE,
};

export function l4Spec(lens: string): L4LensSpec {
  const spec = L4_SPECS[lens];
  if (!spec) throw new Error(`unknown lens ${JSON.stringify(lens)}; expected one of ${L4_LENS_IDS.join(", ")}`);
  return spec;
}

export function l4LensInfo(): { lens: string; skill_id: string; layer: string; python_default_model: string; max_tokens: number; native_file: string; artifacts_in_message: boolean }[] {
  return L4_LENS_IDS.map((id: string) => {
    const s = L4_SPECS[id];
    return {
      lens: id,
      skill_id: s.skillId,
      layer: s.layerName,
      python_default_model: s.model,
      max_tokens: s.maxTokens,
      native_file: s.nativeFileName,
      artifacts_in_message: s.withArtifacts,
    };
  });
}

// ---------------------------------------------------------------------------
// build_user_message
// ---------------------------------------------------------------------------

function l4IsNone(v: unknown): boolean {
  return v === null || v === undefined;
}

export function l4BuildMessage(lens: string, cluster: InsightCluster): string {
  const spec = l4Spec(lens);
  const parts: string[] = ["<cluster>", `  <label>${escapeXml(cluster.label)}</label>`];
  if (!l4IsNone(cluster.ui_context)) parts.push(`  <ui_context>${escapeXml(cluster.ui_context as string)}</ui_context>`);
  if (spec.withArtifacts) {
    if (!l4IsNone(cluster.html)) parts.push(`  <html><![CDATA[\n${cluster.html}\n]]></html>`);
    if (!l4IsNone(cluster.screenshot_ref)) {
      parts.push(`  <screenshot_ref>${escapeXml(cluster.screenshot_ref as string)}</screenshot_ref>`);
    }
  }
  cluster.representative_quotes.forEach((q: string, i: number): void => {
    parts.push(`  <q idx="${i}">${escapeXml(q)}</q>`);
  });
  parts.push("</cluster>");
  return parts.join("\n");
}

// ---------------------------------------------------------------------------
// parse_audit_response
// ---------------------------------------------------------------------------

export interface L4ParseResult {
  ok: boolean;
  payload: L4Payload | null;
  reason: string | null;
}

export function l4ParseResponse(lens: string, text: string, nQuotes: number): L4ParseResult {
  const spec = l4Spec(lens);
  try {
    let data: unknown;
    if (spec.repairJson) {
      const parsed = parseModelJson(text);
      if (!parsed.ok) {
        // parseModelJson also rejects non-objects; Python words that case differently.
        l4Fail(parsed.error as string);
      }
      data = parsed.data;
    } else {
      const raw = extractJsonObjectText(text);
      if (raw === null) l4Fail(`no JSON object found in response: ${l4PyReprStr(text)}`);
      try {
        data = JSON.parse(raw as string);
      } catch (e) {
        l4Fail(`malformed JSON: ${String((e as Error).message)}`);
      }
      if (!l4IsDict(data)) l4Fail(`expected JSON object, got ${l4PyType(data)}`);
    }
    const obj = data as { [k: string]: unknown };
    const actual = Object.keys(obj);
    const missing = L4_TOP_LEVEL_KEYS.filter((k: string): boolean => actual.indexOf(k) < 0);
    if (missing.length) l4Fail(`missing required top-level keys: ${l4Sorted(missing)}`);
    const extra = actual.filter((k: string): boolean => L4_TOP_LEVEL_KEYS.indexOf(k) < 0);
    if (extra.length) l4Fail(`unexpected top-level keys: ${l4Sorted(extra)}`);

    const summary = obj["summary"];
    if (typeof summary !== "string") l4Fail(`'summary' must be str, got ${l4PyType(summary)}`);
    if (!(summary as string).trim()) l4Fail("'summary' must be non-empty");

    const ds = obj["dimension_scores"];
    if (!l4IsDict(ds)) l4Fail(`'dimension_scores' must be dict, got ${l4PyType(ds)}`);
    const dscores = ds as { [k: string]: unknown };
    const dsActual = Object.keys(dscores);
    const dsMissing = spec.dimensionKeys.filter((k: string): boolean => dsActual.indexOf(k) < 0);
    if (dsMissing.length) l4Fail(`dimension_scores missing keys: ${l4Sorted(dsMissing)}`);
    const dsExtra = dsActual.filter((k: string): boolean => spec.dimensionKeys.indexOf(k) < 0);
    if (dsExtra.length) l4Fail(`dimension_scores has unexpected keys: ${l4Sorted(dsExtra)}`);
    for (const k of dsActual) {
      const v = dscores[k];
      if (!l4IsPyInt(v)) l4Fail(`dimension_scores[${l4PyReprStr(k)}] must be int, got ${l4PyType(v)}`);
      if (L4_VALID_DIMENSION_SCORES.indexOf(v as number) < 0) {
        l4Fail(`dimension_scores[${l4PyReprStr(k)}]=${v} out of {1,2,3,4,5}`);
      }
    }

    const fs = obj["findings"];
    if (!Array.isArray(fs)) l4Fail(`'findings' must be list, got ${l4PyType(fs)}`);
    const findings = fs as L4Finding[];
    findings.forEach((f: L4Finding, i: number): void => spec.validateFinding(f, i, nQuotes));
    spec.crossChecks(findings, dscores as { [k: string]: number });
    return { ok: true, payload: obj as unknown as L4Payload, reason: null };
  } catch (e) {
    const msg = e instanceof L4AuditParseError ? e.message : `internal: ${String((e as Error).message || e)}`;
    return { ok: false, payload: null, reason: msg };
  }
}

// ---------------------------------------------------------------------------
// _build_heuristic_violations
// ---------------------------------------------------------------------------

export function l4BuildViolations(lens: string, payload: L4Payload, cluster: InsightCluster): HeuristicViolation[] {
  const spec = l4Spec(lens);
  return payload.findings.map((f: L4Finding): HeuristicViolation => {
    const nielsen = f["severity"] as number;
    const anchored = L4_NIELSEN_TO_ANCHORED[nielsen];
    return {
      heuristic: f["heuristic"] as string,
      violation: f["violation"] as string,
      severity: anchored,
      evidence_review_ids: evidenceReviewIdsFor(cluster.representative_review_ids, f["evidence_quote_idxs"] as number[]),
      reasoning: spec.reasoning(f, cluster, nielsen, anchored),
    };
  });
}

// ---------------------------------------------------------------------------
// audit_cluster (minus the model call, which the flow makes)
// ---------------------------------------------------------------------------

export interface L4VerdictInput {
  lens: string;
  cluster: InsightCluster;
  response_text: string;
  model?: string;
  produced_at?: string;
  skill_hash?: string;
  native_payload_ref?: string; // default: "<python DEFAULT_NATIVE name>#<verdict_id>"
}

export interface L4VerdictOutput {
  status: "audited" | "fallback";
  reason: string | null;
  verdict: AuditVerdict;
  native_payload: { [k: string]: unknown };
}

export function l4VerdictId(skillId: string, clusterId: string): string {
  return `${skillId}__${clusterId}`;
}

export function l4MakeVerdict(input: L4VerdictInput): L4VerdictOutput {
  const spec = l4Spec(input.lens);
  const cluster = input.cluster;
  const verdictId = l4VerdictId(spec.skillId, cluster.cluster_id);
  const base: AuditVerdict = {
    verdict_id: verdictId,
    cluster_id: cluster.cluster_id,
    skill_id: spec.skillId,
    relevant_heuristics: [],
    native_payload_ref: input.native_payload_ref || `${spec.nativeFileName}#${verdictId}`,
    produced_at: input.produced_at || new Date().toISOString(),
    claude_model: input.model || "unknown",
    skill_hash: input.skill_hash || "unknown",
  };
  const parsed = l4ParseResponse(input.lens, input.response_text, cluster.representative_quotes.length);
  if (!parsed.ok) {
    return {
      status: "fallback",
      reason: parsed.reason,
      verdict: base,
      native_payload: { fallback: true, reason: parsed.reason, raw_response: input.response_text },
    };
  }
  const payload = parsed.payload as L4Payload;
  base.relevant_heuristics = l4BuildViolations(input.lens, payload, cluster);
  return {
    status: "audited",
    reason: null,
    verdict: base,
    native_payload: payload as unknown as { [k: string]: unknown },
  };
}
