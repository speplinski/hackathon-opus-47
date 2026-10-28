// Loads a bundled Office Script from dist/ and returns its main(),
// so tests exercise exactly the file that gets pasted into Excel.
import { mkdirSync, readFileSync, writeFileSync } from "fs";

type Main = (workbook: unknown, operation: string, payload: string) => string;
const cache: { [name: string]: Main } = {};

export async function loadScript(name: string): Promise<Main> {
  if (cache[name]) return cache[name];
  const src = readFileSync(`${import.meta.dir}/../dist/${name}.ts`, "utf8");
  mkdirSync(`${import.meta.dir}/.tmp`, { recursive: true });
  const path = `${import.meta.dir}/.tmp/${name}.ts`;
  writeFileSync(path, `${src}\nexport { main };\n`);
  const mod = (await import(path)) as { main: Main };
  cache[name] = mod.main;
  return mod.main;
}

// Call an op and return its result; throws when ok is false.
export async function call(script: string, operation: string, payload: unknown): Promise<{ [k: string]: unknown }> {
  const main = await loadScript(script);
  const out = JSON.parse(main({}, operation, JSON.stringify(payload))) as { ok: boolean; result: { [k: string]: unknown }; errors: string[] };
  if (!out.ok) throw new Error(`${script}/${operation}: ${out.errors.join("; ")}`);
  return out.result;
}
