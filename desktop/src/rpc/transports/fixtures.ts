/**
 * Fixture helpers for the mock transport (pure functions, unit-tested).
 *
 * File naming in `tests/fixtures/rpc/`:
 * - `<method>.json`: the method's result (CLI-backed methods keep the `{ exitCode, data }` envelope);
 * - `<method>@<scenario>.json`: a JSON merge patch (RFC 7386) applied on top for one scenario.
 *   Objects merge key by key, arrays and scalars replace, `null` deletes a key;
 * - `_mock.json`: mock settings (`anchorDate`: the day the fixtures were written for).
 */

export type Json = null | boolean | number | string | Json[] | { [key: string]: Json };

export interface FixtureFile {
  method: string;
  scenario: string | null;
  value: Json;
}

export interface MockSettings {
  /** Local calendar day (YYYY-MM-DD) the fixture timestamps were written around. */
  anchorDate: string;
}

const FILE_NAME = /(?:^|\/)([A-Za-z0-9_.]+?)(?:@([A-Za-z0-9_-]+))?\.json$/;

export function parseFixturePath(path: string): { method: string; scenario: string | null } | null {
  const match = FILE_NAME.exec(path);
  if (!match?.[1]) return null;
  return { method: match[1], scenario: match[2] ?? null };
}

function isObject(value: Json | undefined): value is { [key: string]: Json } {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** RFC 7386 JSON merge patch. */
export function mergePatch(target: Json, patch: Json): Json {
  if (!isObject(patch)) return structuredClone(patch);
  const result: { [key: string]: Json } = isObject(target) ? structuredClone(target) : {};
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) {
      delete result[key];
    } else {
      result[key] = mergePatch(result[key] ?? null, value);
    }
  }
  return result;
}

const DATE_TIME = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})$/;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;
const DAY_MS = 86_400_000;

function formatDateOnly(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/** Calendar days between the anchor and `now`, in the viewer's local time. */
export function dayOffset(anchorDate: string, now: Date): number {
  const [y, m, d] = anchorDate.split("-").map(Number);
  if (y === undefined || m === undefined || d === undefined) return 0;
  const anchor = Date.UTC(y, m - 1, d);
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((today - anchor) / DAY_MS);
}

/** Move every ISO timestamp and date by whole days so "today" in the fixtures is today. */
/** Job records keep epoch seconds (serve_jobs.py); only these keys hold such numbers. */
const EPOCH_KEYS = new Set(["startedAt", "finishedAt"]);
const EPOCH_RANGE = [1e9, 1e10] as const;

export function shiftDates(value: Json, days: number, key?: string): Json {
  if (days === 0) return value;
  if (typeof value === "number") {
    return key && EPOCH_KEYS.has(key) && value > EPOCH_RANGE[0] && value < EPOCH_RANGE[1] ? value + (days * DAY_MS) / 1000 : value;
  }
  if (typeof value === "string") {
    if (DATE_TIME.test(value)) {
      const shifted = new Date(Date.parse(value) + days * DAY_MS).toISOString();
      return value.endsWith("Z") && !value.includes(".") ? shifted.replace(/\.\d{3}Z$/, "Z") : shifted;
    }
    if (DATE_ONLY.test(value)) {
      return formatDateOnly(new Date(Date.parse(`${value}T00:00:00Z`) + days * DAY_MS));
    }
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => shiftDates(item, days));
  if (isObject(value)) {
    const out: { [key: string]: Json } = {};
    for (const [name, item] of Object.entries(value)) out[name] = shiftDates(item, days, name);
    return out;
  }
  return value;
}

/** Group raw fixture modules (from import.meta.glob) by method, with scenario patches applied. */
export function resolveFixtures(
  modules: Record<string, unknown>,
  scenario: string,
): { results: Map<string, Json>; settings: MockSettings } {
  const files: FixtureFile[] = [];
  let settings: MockSettings = { anchorDate: "2026-11-26" };
  for (const [path, value] of Object.entries(modules)) {
    const parsed = parseFixturePath(path);
    if (!parsed) continue;
    if (parsed.method === "_mock") {
      const candidate = value as Partial<MockSettings>;
      if (typeof candidate.anchorDate === "string") settings = { anchorDate: candidate.anchorDate };
      continue;
    }
    files.push({ ...parsed, value: value as Json });
  }
  const results = new Map<string, Json>();
  for (const file of files) {
    if (file.scenario === null) results.set(file.method, structuredClone(file.value));
  }
  for (const file of files) {
    if (file.scenario !== scenario) continue;
    results.set(file.method, mergePatch(results.get(file.method) ?? null, file.value));
  }
  return { results, settings };
}
