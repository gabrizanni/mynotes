import { describe, expect, it } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";
import type { Job } from "../../rpc";
import {
  applyProfile,
  courseCards,
  courseLessons,
  courseRecord,
  historyEntries,
  lessonAttachments,
  linkPreview,
  notesSummary,
  parseStamp,
  profileDraft,
  styleChanges,
  styleDraft,
  validateProfile,
  withPublicationMode,
  type CoursesInput,
} from "./model";

async function inputFor(scenario: string): Promise<CoursesInput> {
  const now = new Date("2026-11-26T09:42:00Z");
  const { app, rpc } = mockApp(scenario, { now });
  await loadAll(app);
  const { data: backups } = await rpc.call("notes.history", {});
  const state = app.store.getState();
  const jobs = state.jobs.order.map((id) => state.jobs.byId[id]).filter((job): job is Job => job !== undefined);
  return { config: state.config.config, dashboard: state.capture.dashboard, library: state.library.library, queue: state.queue.status, jobs, backups, now };
}

describe("course list", () => {
  it("shows next lesson and when the notes last grew", async () => {
    const cards = courseCards(await inputFor("course"));
    expect(cards.map((card) => card.code)).toEqual(["STAT", "MICRO", "CORPFIN", "DIRCOM"]);
    const stat = cards[0];
    expect(stat?.lessonCount).toBe(7);
    expect(stat?.nextLesson).toEqual({ start: new Date("2026-11-30T08:00:00Z"), lessonNumber: 8 });
    expect(stat?.notes.lessonNumber).toBe(6);
    expect(stat?.notes.updatedAt?.toISOString()).toBe("2026-11-24T10:58:12.000Z");
    expect(stat?.notes.lessons).toEqual([1, 2, 3, 4, 5, 6]);
  });
});

describe("lessons table", () => {
  it("lists lessons newest first with their three chips", async () => {
    const rows = courseLessons("STAT", await inputFor("course"));
    expect(rows.map((row) => row.number)).toEqual([7, 6, 5, 4, 3, 2, 1]);
    expect(rows[0]?.chips.transcript.status).toBe("RUNNING");
    expect(rows[0]?.canGenerate).toBe(false);
    expect(rows[2]?.chips.notes.status).toBe("COMPLETED");
    expect(rows[2]?.canGenerate).toBe(true);
  });
});

describe("notes history", () => {
  it("parses compact backup stamps", () => {
    expect(parseStamp("20261124T105805902144Z")?.toISOString()).toBe("2026-11-24T10:58:05.902Z");
    expect(parseStamp("2026-11-24T10:58:05Z")?.toISOString()).toBe("2026-11-24T10:58:05.000Z");
    expect(parseStamp("soon")).toBeNull();
  });

  it("names each saved copy by the update it came before, newest first", async () => {
    const { backups } = await inputFor("default");
    const entries = historyEntries(backups.filter((backup) => backup.course === "STAT"));
    expect(entries[0]).toMatchObject({ kind: "publication", lessonNumber: 6, lessonCount: 5 });
    expect(entries.at(-1)).toMatchObject({ lessonNumber: 2, lessonCount: 1 });
    expect(historyEntries([{ backup_id: "x", course: "STAT", reason: "before undo t1", created_at: "20261125T090000Z" }])[0]?.kind).toBe("undo");
  });

  it("can undo only when an update exists", () => {
    expect(notesSummary("DIRCOM", [], []).canUndo).toBe(false);
    expect(notesSummary("DIRCOM", [], [])).toMatchObject({ updatedAt: null, lessonNumber: null, lessons: [] });
  });
});

describe("profile and style forms", () => {
  it("round-trips the course record and keeps every backend field", async () => {
    const { config } = await inputFor("default");
    const course = courseRecord(config, "STAT");
    expect(course).not.toBeNull();
    if (!course) return;
    const draft = profileDraft(course);
    expect(draft).toMatchObject({ name: "Statistics", kind: "quantitative", lectureLanguage: "en", difficultTopics: ["Hypothesis testing", "Confidence intervals"] });
    const saved = applyProfile(course, { ...draft, name: " Statistics I ", examUnknown: true });
    expect(saved).toMatchObject({ code: "STAT", name: "Statistics I", assessment: "", files: course.files, lesson_sources: course.lesson_sources });
    expect(validateProfile({ ...draft, name: "  " })).toBe("nameRequired");
    expect(withPublicationMode(course, true).publication).toEqual({ mode: "review" });
  });

  it("sends only the style fields that changed", async () => {
    const { rpc } = mockApp("default");
    const { data } = await rpc.call("style.show", { course: "STAT" });
    const saved = styleDraft(data.effective);
    expect(saved).toMatchObject({ writing_style: "clear_study", image_limit: 4, question_count: 4 });
    expect(styleChanges(saved, { ...saved, writing_style: "concise", recap: false })).toEqual({ writing_style: "concise", recap: false });
  });
});

describe("files", () => {
  it("summarizes a re-link preview in counts", async () => {
    const { rpc } = mockApp("default");
    const { data } = await rpc.call("course.link", { code: "STAT", path: "~/Documents/University/Statistics", dryRun: true });
    expect(linkPreview(data)).toEqual({
      status: "ready",
      folder: "~/Documents/University/Statistics",
      pdfs: 14,
      recordings: 2,
      transcripts: 0,
      notebook: { name: "Appunti personali.docx", isNew: true },
      handout: "STAT_COURSE_NOTES.docx",
      choices: [],
    });
  });

  it("lists per-lesson attachments by lesson", async () => {
    const { config } = await inputFor("default");
    expect(lessonAttachments(courseRecord(config, "STAT")).map((item) => [item.lessonNumber, item.name])).toEqual([
      [6, "Lecture 6 - Sampling distributions.pdf"],
      [6, "Problem set 3.pdf"],
      [7, "Lecture 7 - Hypothesis testing.pdf"],
    ]);
  });
});
