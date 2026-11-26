import { describe, expect, it } from "vitest";

import { mockApp } from "../../tests/unit/helpers";

describe("jobs slice", () => {
  it("records stage timings from progress notifications", () => {
    const { app } = mockApp();
    const { jobs } = app.actions;
    jobs.started({ jobId: "j1", kind: "notes", state: "running", stage: "CHECKING", startedAt: "2026-11-26T10:00:00Z", lectureIds: ["STAT-L07"] });
    jobs.progress({ jobId: "j1", stage: "GENERATING", at: "2026-11-26T10:00:40Z" });
    jobs.progress({ jobId: "j1", stage: "GENERATING", at: "2026-11-26T10:01:00Z" });
    jobs.finished({ jobId: "j1", state: "succeeded", exitCode: 0, result: { status: "COMPLETED" }, at: "2026-11-26T10:06:00Z" });
    const state = app.store.getState().jobs;
    expect(state.order).toEqual(["j1"]);
    expect(state.byId.j1?.result?.status).toBe("COMPLETED");
    expect(state.stages.j1).toEqual([
      { stage: "CHECKING", startedAt: "2026-11-26T10:00:00Z", finishedAt: "2026-11-26T10:00:40Z" },
      { stage: "GENERATING", startedAt: "2026-11-26T10:00:40Z", finishedAt: "2026-11-26T10:06:00Z" },
    ]);
  });

  it("ignores progress for unknown jobs", () => {
    const { app } = mockApp();
    const before = app.store.getState();
    app.actions.jobs.progress({ jobId: "nope", stage: "GENERATING" });
    expect(app.store.getState()).toBe(before);
  });
});
