import { describe, expect, it } from "vitest";

import { setupDetailSchema } from "../../rpc";
import setupRunning from "../../../tests/fixtures/rpc/setup.status@setup-running.json";
import { DEFAULT_STYLE, EMPTY_RECORD, markStep } from "./model";
import { loadRecord, parseRecord, saveRecord, STORAGE_KEY, type RecordStorage } from "./storage";

function memory(): RecordStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (key) => data.get(key) ?? null, setItem: (key, value) => void data.set(key, value) };
}

describe("onboarding.json", () => {
  it("round-trips a record", () => {
    const storage = memory();
    const record = { ...markStep({ ...EMPTY_RECORD, language: "it" as const, provider: "codex" as const, style: DEFAULT_STYLE }, "language", "done"), micTestAt: 1 };
    saveRecord(storage, record);
    expect(storage.data.has(STORAGE_KEY)).toBe(true);
    expect(loadRecord(storage)).toEqual(record);
  });

  it("starts fresh on missing, corrupt or foreign data", () => {
    expect(parseRecord(null)).toBe(EMPTY_RECORD);
    expect(parseRecord("{not json")).toBe(EMPTY_RECORD);
    expect(parseRecord(JSON.stringify({ version: 2 }))).toBe(EMPTY_RECORD);
    expect(parseRecord(JSON.stringify({ ...EMPTY_RECORD, provider: "other" }))).toBe(EMPTY_RECORD);
    expect(loadRecord(null)).toBe(EMPTY_RECORD);
  });

  it("fills fields added after the first release", () => {
    const { folder: _folder, calendar: _calendar, micTestAt: _mic, ...older } = EMPTY_RECORD;
    expect(parseRecord(JSON.stringify(older))).toEqual(EMPTY_RECORD);
  });

  it("survives storage that throws", () => {
    const broken: RecordStorage = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("full");
      },
    };
    expect(loadRecord(broken)).toBe(EMPTY_RECORD);
    expect(() => saveRecord(broken, EMPTY_RECORD)).not.toThrow();
  });

  it("reads the installer's extra progress fields", () => {
    const parsed = setupDetailSchema.parse(setupRunning);
    expect(parsed.overallTotal).toBe(1_006_000_000);
    expect(parsed.components?.map((component) => component.id)).toEqual(["runtime", "speech", "libreoffice"]);
  });
});
