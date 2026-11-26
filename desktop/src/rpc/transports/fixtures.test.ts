import { describe, expect, it } from "vitest";

import { methods, type MethodName } from "../schema";
import { dayOffset, mergePatch, parseFixturePath, resolveFixtures, shiftDates } from "./fixtures";

const modules = import.meta.glob("../../../tests/fixtures/rpc/*.json", { eager: true, import: "default" });

describe("fixture helpers", () => {
  it("parses method and scenario from file names", () => {
    expect(parseFixturePath("/x/capture.dashboard.json")).toEqual({ method: "capture.dashboard", scenario: null });
    expect(parseFixturePath("/x/job.list@approval.json")).toEqual({ method: "job.list", scenario: "approval" });
  });

  it("applies RFC 7386 merge patches", () => {
    expect(mergePatch({ a: { b: 1, c: 2 }, d: [1] }, { a: { c: null, e: 3 }, d: [2] })).toEqual({ a: { b: 1, e: 3 }, d: [2] });
  });

  it("moves dates by whole days and leaves other strings alone", () => {
    const shifted = shiftDates({ at: "2026-11-26T08:00:00Z", day: "2026-11-26", title: "2026" }, 2);
    expect(shifted).toEqual({ at: "2026-11-28T08:00:00Z", day: "2026-11-28", title: "2026" });
    expect(dayOffset("2026-11-26", new Date(2026, 10, 26, 23, 0))).toBe(0);
  });
});

describe("fixtures match the schemas (mock ↔ contract)", () => {
  const scenarios = new Set(["default"]);
  for (const path of Object.keys(modules)) {
    const parsed = parseFixturePath(path);
    if (parsed?.scenario) scenarios.add(parsed.scenario);
  }
  for (const scenario of scenarios) {
    it(`every fixture parses in scenario "${scenario}"`, () => {
      const { results } = resolveFixtures(modules, scenario);
      for (const [method, value] of results) {
        expect(Object.hasOwn(methods, method), `${method} has a schema`).toBe(true);
        const spec = methods[method as MethodName];
        const outcome = spec.result.safeParse(value);
        expect(outcome.success, `${method}@${scenario}: ${outcome.success ? "" : JSON.stringify(outcome.error.issues.slice(0, 2))}`).toBe(true);
      }
    });
  }
});
