import { describe, expect, it } from "vitest";

import dashboard from "../tests/fixtures/rpc/capture.dashboard.json";
import { capsuleState, dashboardFrom, formatElapsed } from "./capsule-model";

describe("capsule model", () => {
  it("unwraps the result envelope and the watch snapshot alike", () => {
    expect(dashboardFrom(dashboard)?.schemaVersion).toBe(1);
    const inner = (dashboard as { data: unknown }).data;
    expect(dashboardFrom({ topic: "capture", hash: "h", data: inner })?.schemaVersion).toBe(1);
    expect(dashboardFrom({ topic: "capture", hash: "h", data: null, error: { code: -32001 } })).toBeNull();
    expect(dashboardFrom("nonsense")).toBeNull();
  });

  it("formats elapsed time like the recorder", () => {
    expect(formatElapsed(0)).toBe("0:00");
    expect(formatElapsed(65)).toBe("1:05");
    expect(formatElapsed(3723)).toBe("1:02:03");
    expect(formatElapsed(-4)).toBe("0:00");
  });

  it("shows recording with the lesson title and hides when idle", () => {
    const base = dashboardFrom(dashboard);
    expect(capsuleState(base, new Date())).toBeNull();
    const exporting = base && { ...base, runtime: { ...base.runtime, recording: false, exporting: true } };
    expect(capsuleState(exporting, new Date())).toEqual({ kind: "saving" });
    const started = base && {
      ...base,
      runtime: { ...base.runtime, recording: true, currentSession: { id: "s", status: "RECORDING", actualStart: "2026-11-26T11:58:55Z", event: { id: "e", title: "Statistics · 5" } } },
    };
    expect(capsuleState(started, new Date("2026-11-26T12:00:00Z"))).toEqual({ kind: "recording", elapsed: "1:05", title: "Statistics · 5" });
  });
});
