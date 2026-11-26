import { describe, expect, it } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";
import type { Job } from "../../rpc";
import { deriveActivity, type ActivityModel } from "./model";

async function modelFor(scenario: string): Promise<ActivityModel> {
  const { app } = mockApp(scenario);
  await loadAll(app);
  const state = app.store.getState();
  const jobs = state.jobs.order.map((id) => state.jobs.byId[id]).filter((job): job is Job => job !== undefined);
  return deriveActivity({ queue: state.queue.status, library: state.library.library, jobs, courses: state.capture.dashboard?.courses ?? [] });
}

describe("Activity view-model", () => {
  it("splits the default queue into in progress and to check", async () => {
    const model = await modelFor("default");
    expect(model.inProgress.map((row) => row.lesson.lectureId)).toEqual(["STAT-L07"]);
    expect(model.inProgress[0]).toMatchObject({ elapsedSeconds: 412, actions: ["cancel"], lesson: { courseName: "Statistics", lessonNumber: 7 } });
    expect(model.toCheck.map((row) => [row.lesson.lectureId, row.reason])).toEqual([["DIRCOM-L02", "review"]]);
    expect(model.readyToTranscribe).toEqual([]);
    expect(model.notes.map((row) => row.status)).toEqual(["PAUSED_QUOTA", "COMPLETED", "COMPLETED"]);
    expect(model.notesNeedingYou).toBe(1);
    expect(model).toMatchObject({ paused: false, parallel: 1, waiting: 0, finishing: 1 });
  });

  it("offers ready recordings oldest first and never a hidden one", async () => {
    const model = await modelFor("workspace");
    expect(model.readyToTranscribe.map((row) => row.recordingId)).toEqual(["rmicro03c41d", "rimp1119b8e2"]);
    expect(model.readyToTranscribe[1]?.lesson).toMatchObject({ courseCode: null, title: "Recording 19 Nov 14:02" });
    expect(model.readyToTranscribe.some((row) => row.recordingId === "rmictest0a")).toBe(false);
    expect(model.history.map((row) => row.lesson.lectureId)).toEqual(["CORPFIN-L03", "MICRO-L04"]);
    expect(model.parallel).toBe(2);
  });

  it("counts waiting and finishing work while paused, running first", async () => {
    const model = await modelFor("paused");
    expect(model.paused).toBe(true);
    expect(model.inProgress.map((row) => row.chip.status)).toEqual(["RUNNING", "QUEUED", "QUEUED", "QUEUED"]);
    expect(model.inProgress.map((row) => row.queuePosition)).toEqual([null, 1, 2, 3]);
    expect(model).toMatchObject({ waiting: 3, finishing: 1 });
    expect(model.toCheck[0]).toMatchObject({ reason: "failed", actions: ["retry", "restart", "remove"] });
    // A queued recording is not offered again as ready.
    expect(model.readyToTranscribe.map((row) => row.recordingId)).toEqual([]);
  });

  it("drops unknown row actions and gives queued jobs a Remove", () => {
    const model = deriveActivity({
      queue: {
        parallel: 9,
        paused: false,
        ui_open: true,
        jobs: [
          { id: "a", recording_id: "r1", title: "STAT-L01", status: "QUEUED", actions: [] },
          { id: "b", recording_id: "r2", title: "Notes", status: "FAILED", actions: ["explode", "retry"] },
          { id: "c", recording_id: "r3", title: "Odd", status: "SOMETHING_NEW" },
        ],
      },
      library: null,
      jobs: [],
      courses: [{ code: "STAT", name: "Statistics" }],
    });
    expect(model.inProgress[0]?.actions).toEqual(["remove"]);
    expect(model.toCheck[0]?.actions).toEqual(["retry"]);
    expect(model.parallel).toBe(4);
    // Unknown statuses do not crash and do not land in a section.
    expect([...model.inProgress, ...model.toCheck, ...model.history].some((row) => row.jobId === "c")).toBe(false);
  });
});
