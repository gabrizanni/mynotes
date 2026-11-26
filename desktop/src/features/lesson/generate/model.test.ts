import { describe, expect, it } from "vitest";

import { mockApp } from "../../../../tests/unit/helpers";
import type { Job } from "../../../rpc";
import { startSync } from "../../../state";
import {
  flowView,
  jobDetails,
  normalizeLessonList,
  OUTCOME_STATUSES,
  outcomeOf,
  runParams,
  sourceChecklist,
  stageTimeline,
  warningsOf,
  DEFAULT_OPTIONS,
} from "./model";

const base: Job = {
  jobId: "j1",
  kind: "notes",
  state: "running",
  stage: "CHECKING",
  startedAt: "2026-11-26T10:00:00Z",
  courseCode: "STAT",
  lectureIds: ["STAT-L07"],
  batchId: "b1",
  result: null,
};

const finished = (state: string, status: string | null, extra: Partial<Job> = {}): Job => ({
  ...base,
  state,
  stage: null,
  finishedAt: "2026-11-26T10:08:00Z",
  result: status ? { status } : null,
  ...extra,
});

describe("outcome mapping", () => {
  it.each([
    ["COMPLETED", "completed", "openWord", "success"],
    ["COMPLETED_WITH_WARNINGS", "warnings", "viewWarnings", "warning"],
    ["NEEDS_TRANSCRIPT", "needsTranscript", "transcribe", "warning"],
    ["PAUSED_QUOTA", "pausedQuota", "resume", "warning"],
    ["PAUSED_AUTH", "pausedAuth", "signIn", "warning"],
    ["PAUSED_NETWORK", "pausedNetwork", "resume", "warning"],
    ["PAUSED_SLEEP", "pausedSleep", "resume", "warning"],
    ["AWAITING_APPROVAL", "awaitingApproval", "openPreview", "warning"],
    ["PERSONAL_CONTENT_CONFLICT", "conflict", "askAssistant", "danger"],
    ["NEEDS_REVIEW", "needsReview", "askAssistant", "danger"],
    ["STOPPED", "needsReview", "askAssistant", "danger"],
  ])("%s → %s with one fix", (status, kind, fix, tone) => {
    const outcome = outcomeOf(finished("failed", status));
    expect(outcome).toMatchObject({ kind, fix, tone, status });
  });

  it("covers every status of the outcome table", () => {
    expect(OUTCOME_STATUSES.sort()).toEqual(
      [
        "AWAITING_APPROVAL",
        "COMPLETED",
        "COMPLETED_WITH_WARNINGS",
        "NEEDS_REVIEW",
        "NEEDS_TRANSCRIPT",
        "PAUSED_AUTH",
        "PAUSED_NETWORK",
        "PAUSED_QUOTA",
        "PAUSED_SLEEP",
        "PERSONAL_CONTENT_CONFLICT",
        "STOPPED",
      ].sort(),
    );
  });

  it("is null while the job runs and the sheet shows the stepper", () => {
    expect(outcomeOf(base)).toBeNull();
    expect(flowView(base)).toEqual({ view: "running", job: base });
    expect(flowView(null)).toEqual({ view: "setup" });
  });

  it("offers Resume only when a batch exists to resume", () => {
    expect(outcomeOf(finished("interrupted", "PAUSED_QUOTA"))?.resumable).toBe(true);
    expect(outcomeOf(finished("interrupted", "PAUSED_QUOTA", { batchId: null }))?.resumable).toBe(false);
    expect(outcomeOf(finished("succeeded", "COMPLETED"))?.resumable).toBe(false);
  });

  it("treats a job the student stopped as paused, not as an error", () => {
    expect(outcomeOf(finished("cancelled", null))).toMatchObject({ kind: "cancelled", status: "CANCELLED", fix: "resume", resumable: true });
  });

  it("never leaks an unknown status: it needs a closer look", () => {
    expect(outcomeOf(finished("failed", "SOMETHING_NEW"))).toMatchObject({ kind: "needsReview", status: "NEEDS_REVIEW" });
    expect(outcomeOf(finished("failed", null))).toMatchObject({ kind: "needsReview" });
    expect(outcomeOf(finished("succeeded", null))).toMatchObject({ kind: "completed" });
  });
});

describe("stage timeline", () => {
  const now = new Date("2026-11-26T10:03:30Z");
  it("shows elapsed time per stage from progress notifications, never a percentage", () => {
    const timeline = stageTimeline({ ...base, stage: "GENERATING" }, [
      { stage: "CHECKING", startedAt: "2026-11-26T10:00:00Z", finishedAt: "2026-11-26T10:00:40Z" },
      { stage: "GENERATING", startedAt: "2026-11-26T10:00:40Z", finishedAt: null },
    ], now);
    expect(timeline.current).toBe("GENERATING");
    expect(timeline.timings.CHECKING).toEqual({ startedAt: "2026-11-26T10:00:00Z", finishedAt: "2026-11-26T10:00:40Z" });
    expect(timeline.totalSeconds).toBe(210);
    expect(Object.values(timeline).some((value) => typeof value === "number" && value > 0 && value <= 1)).toBe(false);
  });

  it("is done once the job succeeded, and stops the clock at the finish", () => {
    const timeline = stageTimeline(finished("succeeded", "COMPLETED"), [], now);
    expect(timeline.current).toBe("done");
    expect(timeline.totalSeconds).toBe(480);
  });

  it("times the current stage from the job record when the app opened mid-job", () => {
    const timeline = stageTimeline({ ...base, stage: "BUILDING_WORD", stageStartedAt: "2026-11-26T10:02:00Z" }, [], now);
    expect(timeline.current).toBe("BUILDING_WORD");
    expect(timeline.timings).toEqual({ BUILDING_WORD: { startedAt: "2026-11-26T10:02:00Z", finishedAt: null } });
  });
});

describe("sources and parameters", () => {
  it("builds the checklist from notes.plan in the requested order", async () => {
    const { rpc } = mockApp("default");
    const { data } = await rpc.call("notes.plan", { course: "STAT", lessons: "7,6" });
    const rows = sourceChecklist(data, ["STAT-L07", "STAT-L06"]);
    expect(rows[0]).toEqual({ lectureId: "STAT-L07", lessonNumber: 7, transcript: true, slides: ["Lecture 7 - Hypothesis testing.pdf"], notebookSection: "Lezione 7" });
    expect(rows[1]?.lessonNumber).toBe(6);
    expect(sourceChecklist(null, ["STAT-L03"])[0]).toMatchObject({ transcript: false, slides: [], notebookSection: null });
  });

  it("reads warnings as strings or {message} objects, without duplicates", () => {
    expect(warningsOf({ warnings: ["Slide 14 skipped.", { message: "Check two formulas." }, 3, "Slide 14 skipped."] }, null)).toEqual([
      "Slide 14 skipped.",
      "Check two formulas.",
    ]);
  });

  it("maps the sheet options onto notes.run flags", () => {
    expect(runParams("STAT", [5, 6, 7], DEFAULT_OPTIONS)).toEqual({ course: "STAT", lessons: "5,6,7", quality: "standard" });
    expect(
      runParams("STAT", [7], { quality: "approfondito", instructions: " Show every step ", imageLimit: 2, focusLecturer: true, contextLessons: "4, 5" }),
    ).toEqual({ course: "STAT", lessons: "7", quality: "approfondito", instructions: "Show every step", imageLimit: 2, speakerFocus: "professor", contextLessons: "4,5" });
    expect(normalizeLessonList("2–4 6")).toBe("2-4,6");
    expect(normalizeLessonList("lesson four")).toBeNull();
  });

  it("keeps codes for Copy details only", () => {
    expect(jobDetails(finished("failed", "NEEDS_REVIEW", { exitCode: 4 }))).toContain("status: NEEDS_REVIEW\nbatch: b1\nexit code: 4");
  });
});

describe("mock notes job", () => {
  it("walks the four stages with real elapsed times, then completes", async () => {
    const { app } = mockApp("default", { simulate: true });
    startSync(app);
    const jobId = await app.actions.jobs.runNotes({ course: "STAT", lessons: "7" });
    await new Promise((resolve) => setTimeout(resolve, 80));
    const state = app.store.getState().jobs;
    expect(state.byId[jobId]?.result?.status).toBe("COMPLETED");
    expect(state.stages[jobId]?.map((timing) => timing.stage)).toEqual(["CHECKING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"]);
    app.rpc.close();
  });

  it("stops where the scenario pauses it, and Resume finishes the same batch", async () => {
    const { app } = mockApp("notes-quota", { simulate: true });
    startSync(app);
    const jobId = await app.actions.jobs.runNotes({ course: "STAT", lessons: "7" });
    await new Promise((resolve) => setTimeout(resolve, 60));
    const paused = app.store.getState().jobs.byId[jobId];
    expect(paused?.result?.status).toBe("PAUSED_QUOTA");
    expect(app.store.getState().jobs.stages[jobId]?.at(-1)?.stage).toBe("GENERATING");
    const resumed = await app.actions.jobs.runNotes({ course: "STAT", lessons: "7", resume: paused?.batchId ?? "" });
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(app.store.getState().jobs.byId[resumed]?.result?.status).toBe("COMPLETED");
    app.rpc.close();
  });

  it("cancels a running job and keeps it resumable", async () => {
    const { app } = mockApp("default", { simulate: true });
    startSync(app);
    const jobId = await app.actions.jobs.runNotes({ course: "STAT", lessons: "7" });
    await new Promise((resolve) => setTimeout(resolve, 5));
    await app.actions.jobs.cancel(jobId);
    await new Promise((resolve) => setTimeout(resolve, 60));
    const job = app.store.getState().jobs.byId[jobId];
    expect(job?.state).toBe("cancelled");
    expect(job && outcomeOf(job)?.kind).toBe("cancelled");
    app.rpc.close();
  });
});
