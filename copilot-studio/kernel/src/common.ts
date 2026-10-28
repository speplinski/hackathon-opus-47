// Helpers shared by all kernel operations. Ported from the Python
// pipeline; each function names its Python counterpart.

// prompt_builder._ESCAPE_MAP / str.translate({"&","<",">"})
export function escapeXml(s: string): string {
  return s.replace(/[&<>]/g, (c: string): string =>
    c === "&" ? "&amp;" : c === "<" ? "&lt;" : "&gt;"
  );
}

// prompt_builder.wrap_user_text (review_id always supplied by L0).
export function wrapUserText(text: string, reviewId: string): string {
  if (!reviewId) throw new Error("review_id must not be empty");
  if (/["'<>& \t\n\r]/.test(reviewId)) {
    throw new Error(`review_id contains a disallowed character: ${reviewId}`);
  }
  return `<user_review id="${reviewId}">${escapeXml(text)}</user_review>`;
}

// Python: _JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL) — greedy,
// first "{" to last "}".
export function extractJsonObjectText(text: string): string | null {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end < start) return null;
  return text.slice(start, end + 1);
}

// Python: _repair_unescaped_string_quotes. JS reports the failing
// position only in the error message ("... at position N"); when the
// runtime gives no position the repair is skipped.
export function repairUnescapedStringQuotes(raw: string, maxIters = 32): string {
  let s = raw;
  for (let k = 0; k < maxIters; k++) {
    try {
      JSON.parse(s);
      return s;
    } catch (err) {
      const msg = String((err as Error).message || err);
      const m = /position (\d+)/.exec(msg);
      const looksLikeDelimiter =
        /Expected ',' or '}'|Expected ',' or ']'|Expected double-quoted property name|Unexpected string|Unexpected token/.test(
          msg
        );
      if (!m || !looksLikeDelimiter) return s;
      const pos = parseInt(m[1], 10);
      let i = pos - 1;
      while (i >= 0 && s[i] !== '"') i--;
      if (i < 0) return s;
      let backslashes = 0;
      let j = i - 1;
      while (j >= 0 && s[j] === "\\") {
        backslashes++;
        j--;
      }
      if (backslashes % 2 === 1) return s;
      s = s.slice(0, i) + "\\" + s.slice(i);
    }
  }
  return s;
}

export interface ParsedObject {
  ok: boolean;
  data: { [k: string]: unknown } | null;
  error: string | null;
}

// Extract the JSON object from a model response, with one repair pass.
export function parseModelJson(text: string): ParsedObject {
  const raw = extractJsonObjectText(text);
  if (raw === null) return { ok: false, data: null, error: "no JSON object found in response" };
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (first) {
    const repaired = repairUnescapedStringQuotes(raw);
    if (repaired === raw) {
      return { ok: false, data: null, error: `malformed JSON: ${String((first as Error).message)}` };
    }
    try {
      data = JSON.parse(repaired);
    } catch (second) {
      return {
        ok: false,
        data: null,
        error: `malformed JSON: ${String((first as Error).message)}; repaired retry failed: ${String(
          (second as Error).message
        )}`,
      };
    }
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    return { ok: false, data: null, error: "expected a JSON object" };
  }
  return { ok: true, data: data as { [k: string]: unknown }, error: null };
}

export function isInt(v: unknown): boolean {
  return typeof v === "number" && Number.isInteger(v);
}

export function isNonEmptyString(v: unknown): boolean {
  return typeof v === "string" && v.trim().length > 0;
}

// Python json.dumps(x, ensure_ascii=False) default separators are
// (", ", ": "); JSON.stringify uses none. Only needed where a Python
// string was built from json.dumps; callers say so explicitly.
export function pyJsonDumps(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number" || typeof value === "boolean") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map((x: unknown): string => pyJsonDumps(x)).join(", ") + "]";
  const obj = value as { [k: string]: unknown };
  return (
    "{" +
    Object.keys(obj)
      .map((k: string): string => JSON.stringify(k) + ": " + pyJsonDumps(obj[k]))
      .join(", ") +
    "}"
  );
}

// Python "{:.2f}".format(x) (round-half-even differences are negligible
// for the values the pipeline formats).
export function fmt2(x: number): string {
  return x.toFixed(2);
}

export function uniqueInOrder(xs: string[]): string[] {
  const out: string[] = [];
  for (const x of xs) if (out.indexOf(x) < 0) out.push(x);
  return out;
}

// InsightCluster.evidence_review_ids_for
export function evidenceReviewIdsFor(
  representativeReviewIds: string[] | undefined,
  quoteIdxs: number[]
): string[] {
  if (!representativeReviewIds || representativeReviewIds.length === 0) return [];
  const seen: string[] = [];
  for (const idx of quoteIdxs) {
    const rid = representativeReviewIds[idx];
    if (rid !== undefined && seen.indexOf(rid) < 0) seen.push(rid);
  }
  return seen;
}
