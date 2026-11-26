import { describe, expect, it, vi } from "vitest";

import { mockApp } from "../../tests/unit/helpers";

describe("jobs slice", () => {
  it("records stage timings from progress notifications (server shapes)", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-11-26T10:00:00Z"));
    try {
      const { app } = mockApp();
      const { jobs } = app.actions;
      jobs.started({ jobId: "j1", method: "notes.run", params: { course: "STAT", lessons: "7" }, pid: 4242 });
      jobs.progress({ jobId: "j1", stage: "CHECKING", at: "2026-11-26T10:00:00Z" });
      jobs.progress({ jobId: "j1", stage: "GENERATING", at: "2026-11-26T10:00:40Z" });
      jobs.progress({ jobId: "j1", stage: "GENERATING", at: "2026-11-26T10:01:00Z" });
      vi.setSystemTime(new Date("2026-11-26T10:06:00Z"));
      jobs.finished({ jobId: "j1", state: "COMPLETED", exitCode: 0, result: { exitCode: 0, data: { status: "COMPLETED", batch_id: "b-1" } } });
      const state = app.store.getState().jobs;
      expect(state.order).toEqual(["j1"]);
      expect(state.byId.j1).toMatchObject({ kind: "notes", state: "succeeded", courseCode: "STAT", lectureIds: ["STAT-L07"], batchId: "b-1", result: { status: "COMPLETED" } });
      expect(state.stages.j1).toEqual([
        { stage: "CHECKING", startedAt: "2026-11-26T10:00:00.000Z", finishedAt: "2026-11-26T10:00:40.000Z" },
        { stage: "GENERATING", startedAt: "2026-11-26T10:00:40.000Z", finishedAt: "2026-11-26T10:06:00.000Z" },
      ]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores progress for unknown jobs", () => {
    const { app } = mockApp();
    const before = app.store.getState();
    app.actions.jobs.progress({ jobId: "nope", stage: "GENERATING" });
    expect(app.store.getState()).toBe(before);
  });
});
