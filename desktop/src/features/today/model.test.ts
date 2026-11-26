import { describe, expect, it } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";
import type { Job } from "../../rpc";
import { deriveToday, type TodayModel } from "./model";

async function modelFor(scenario: string, iso: string): Promise<TodayModel> {
  const now = new Date(iso);
  const { app } = mockApp(scenario, { now });
  await loadAll(app);
  const state = app.store.getState();
  const jobs = state.jobs.order.map((id) => state.jobs.byId[id]).filter((job): job is Job => job !== undefined);
  return deriveToday({
    dashboard: state.capture.dashboard,
    queue: state.queue.status,
    library: state.library.library,
    config: state.config.config,
    jobs,
    now,
  });
}

describe("Today view-model", () => {
  it("defaults to Record now with the next lesson in the hero", async () => {
    const model = await modelFor("default", "2026-11-26T09:42:00Z");
    expect(model.hero.action).toEqual({ kind: "record" });
    expect(model.hero.mode).toBe("next");
    expect(model.hero.lesson?.courseName).toBe("Microeconomics");
    expect(model.hero.recorderChip.status).toBe("SCHEDULED");
    expect(model.lessons.map((lesson) => lesson.phase)).toEqual(["past", "next", "later"]);
    expect(model.lessons[0]?.chip).toEqual({ kind: "transcription", status: "RUNNING" });
  });

  it("puts Stop first while a recording is live", async () => {
    const model = await modelFor("recording", "2026-11-26T08:47:00Z");
    expect(model.hero.action).toEqual({ kind: "stop" });
    expect(model.hero.mode).toBe("live");
    expect(model.hero.lesson?.courseCode).toBe("STAT");
    expect(model.hero.startedAt?.toISOString()).toBe("2026-11-26T07:58:40.000Z");
    expect(model.lessons.find((lesson) => lesson.phase === "live")?.lessonNumber).toBe(7);
  });

  it("turns a blocked microphone into the Fix action", async () => {
    const model = await modelFor("error", "2026-11-26T07:51:00Z");
    expect(model.hero.action).toEqual({ kind: "fix", problem: "microphone" });
    expect(model.hero.recorderChip.status).toBe("MIC_BLOCKED");
    expect(model.setup.find((item) => item.id === "microphone")?.done).toBe(false);
  });

  it("offers Review for notes awaiting approval and does not repeat them below", async () => {
    const model = await modelFor("approval", "2026-11-26T09:42:00Z");
    expect(model.hero.action).toMatchObject({ kind: "review", lesson: { courseCode: "STAT", lessonNumber: 6 } });
    expect(model.needsYou.some((item) => item.reason === "notesApproval")).toBe(false);
  });

  it("offers Generate notes for a fresh transcript without notes", async () => {
    const model = await modelFor("generate", "2026-11-26T09:42:00Z");
    expect(model.hero.action).toMatchObject({ kind: "generate", lesson: { lectureId: "STAT-L07" } });
  });

  it("shows tomorrow's lesson and no timeline on an empty day", async () => {
    const model = await modelFor("empty", "2026-11-26T09:42:00Z");
    expect(model.lessons).toEqual([]);
    expect(model.hero.isToday).toBe(false);
    expect(model.hero.lesson?.courseCode).toBe("DIRCOM");
  });

  it("lists what needs the student, most severe first, capped at five", async () => {
    const model = await modelFor("default", "2026-11-26T09:42:00Z");
    expect(model.needsYou.map((item) => item.reason)).toEqual(["notesPausedQuota", "transcriptionToCheck"]);
    expect(model.needsYouTotal).toBe(2);
    expect(model.setup.filter((item) => !item.done).map((item) => item.id)).toEqual(["profile"]);
  });
});
