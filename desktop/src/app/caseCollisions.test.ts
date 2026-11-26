import { expect, it } from "vitest";

// Windows and the default macOS file system ignore letter case: an import of "./BackendFailure"
// can resolve to "backendFailure.ts" before "BackendFailure.tsx". Linux does not, so the clash only
// shows up when the app is built on another OS. Keep module names unique once case is ignored.
const modules = Object.keys(import.meta.glob(["/src/**/*.ts", "/src/**/*.tsx"]));

it("has no two modules whose import paths differ only by letter case", () => {
  const byKey = new Map<string, string[]>();
  for (const file of modules) {
    const key = file.replace(/\.tsx?$/, "").toLowerCase();
    byKey.set(key, [...(byKey.get(key) ?? []), file]);
  }
  const clashes = [...byKey.values()].filter((files) => files.length > 1);
  expect(clashes).toEqual([]);
});
