import { describe, expect, it } from "vitest";

import { WELCOME_STEPS } from "../../state/routes";
import {
  AUTO_ADVANCE_MS,
  DEFAULT_STYLE,
  EMPTY_RECORD,
  LOGIN_TIMEOUT_MS,
  NETWORK_WAIT_SECONDS,
  NO_BACKEND,
  STEPS,
  applySaved,
  backgroundText,
  calendarProblem,
  classifyImport,
  coursePayload,
  defaultCourse,
  defaultPeriod,
  draftChecklist,
  folderPreview,
  isRecordShortcut,
  keyIntent,
  lectureIdFor,
  markStep,
  megabytes,
  micPhase,
  mustWait,
  nextStepId,
  pendingOps,
  prevStepId,
  readySummary,
  redirectFor,
  resumeStep,
  schedulePreview,
  segmentStates,
  setupView,
  shouldOpenOnboarding,
  signInPhase,
  stepState,
  validateCalendarUrl,
  validateCourse,
  type BackendProgress,
  type OnboardingRecord,
  type SetupDetail,
  type StepId,
} from "./model";

const labels = { goalLabel: (goal: string) => goal, goalsPrefix: "Goals:" };
const statistics = { ...defaultCourse("en"), name: "Statistics" };

function through(step: StepId, base: OnboardingRecord = EMPTY_RECORD): OnboardingRecord {
  let record: OnboardingRecord = { ...base, language: "en", provider: "claude", course: statistics, style: DEFAULT_STYLE };
  for (const id of WELCOME_STEPS) {
    if (id === step) break;
    record = markStep(record, id, "done");
  }
  return record;
}

const status = (patch: Partial<SetupDetail>): SetupDetail => ({
  state: "running",
  stage: "components",
  title: "LibreOffice",
  bytesDone: 212_000_000,
  bytesTotal: 300_000_000,
  lastDataAt: "2026-11-26T09:41:56Z",
  error: null,
  overallDone: 624_000_000,
  overallTotal: 1_006_000_000,
  components: [],
  ...patch,
});
const NOW = new Date("2026-11-26T09:42:00Z");

describe("step machine", () => {
  it("lists the 13 steps in route order, three segments, words not numbers", () => {
    expect(STEPS.map((step) => step.id)).toEqual([...WELCOME_STEPS]);
    expect(STEPS.filter((step) => step.required).map((step) => step.id)).toEqual(["language", "assistant", "privacy", "course", "style", "files"]);
  });

  it("moves forward and back through every step", () => {
    WELCOME_STEPS.forEach((id, index) => {
      expect(nextStepId(id)).toBe(WELCOME_STEPS[index + 1] ?? null);
      expect(prevStepId(id)).toBe(index === 0 ? null : WELCOME_STEPS[index - 1]);
    });
  });

  it("only skippable steps can be skipped", () => {
    for (const step of STEPS) {
      const marked = markStep(EMPTY_RECORD, step.id, "skipped");
      expect(marked.marks[step.id]).toBe(step.skippable ? "skipped" : undefined);
    }
    expect(STEPS.filter((step) => step.skippable).map((step) => step.id)).toEqual(["sign-in", "studies", "schedule", "microphone", "automatic", "first-notes"]);
  });

  it("marking twice keeps the same object", () => {
    const once = markStep(EMPTY_RECORD, "language", "done");
    expect(markStep(once, "language", "done")).toBe(once);
  });

  it("segments light up in order and all finish on the last step", () => {
    expect(segmentStates("language")).toEqual({ setup: "current", course: "upcoming", recording: "upcoming" });
    expect(segmentStates("style")).toEqual({ setup: "done", course: "current", recording: "upcoming" });
    expect(segmentStates("microphone")).toEqual({ setup: "done", course: "done", recording: "current" });
    expect(segmentStates("ready")).toEqual({ setup: "done", course: "done", recording: "done" });
  });
});

describe("resume", () => {
  it("opens the first step on a fresh install", () => {
    expect(resumeStep(EMPTY_RECORD, NO_BACKEND)).toBe("language");
    expect(shouldOpenOnboarding(EMPTY_RECORD, NO_BACKEND)).toBe(true);
  });

  it("opens the first incomplete required step, even past skipped optional ones", () => {
    const afterAssistant = markStep(markStep(EMPTY_RECORD, "language", "done"), "assistant", "done");
    expect(resumeStep(afterAssistant, NO_BACKEND)).toBe("privacy");
    const skippedSignIn = markStep(afterAssistant, "sign-in", "skipped");
    expect(resumeStep(skippedSignIn, NO_BACKEND)).toBe("privacy");
    const studiesSkipped = markStep(markStep(skippedSignIn, "privacy", "done"), "studies", "skipped");
    expect(resumeStep(studiesSkipped, NO_BACKEND)).toBe("course");
  });

  it("goes back to an earlier required step left open", () => {
    const gap = markStep(markStep(markStep(EMPTY_RECORD, "language", "done"), "assistant", "done"), "course", "done");
    expect(resumeStep(gap, NO_BACKEND)).toBe("privacy");
  });

  it("continues after the furthest visited step once required ones are done", () => {
    expect(resumeStep(through("schedule"), NO_BACKEND)).toBe("schedule");
    const skipped = markStep(through("schedule"), "schedule", "skipped");
    expect(resumeStep(skipped, NO_BACKEND)).toBe("microphone");
    expect(resumeStep(markStep(through("ready"), "ready", "done"), NO_BACKEND)).toBe("ready");
  });

  it("reads completion from config groups and doctor", () => {
    const backend: BackendProgress = { complete: false, completedGroups: ["providers", "courses", "style", "paths"], agentReady: true };
    expect(stepState(EMPTY_RECORD, backend, "assistant")).toBe("done");
    expect(stepState(EMPTY_RECORD, backend, "sign-in")).toBe("done");
    expect(stepState(EMPTY_RECORD, backend, "privacy")).toBe("todo");
    const local = markStep(EMPTY_RECORD, "language", "done");
    expect(resumeStep(local, backend)).toBe("privacy");
  });

  it("an existing install is complete: launch stays on Today", () => {
    const backend: BackendProgress = { complete: true, completedGroups: [], agentReady: false };
    expect(shouldOpenOnboarding(EMPTY_RECORD, backend)).toBe(false);
    expect(stepState(EMPTY_RECORD, backend, "files")).toBe("done");
  });

  it("a finished record never resumes", () => {
    const done = { ...through("ready"), finished: true };
    expect(resumeStep(done, NO_BACKEND)).toBeNull();
    expect(shouldOpenOnboarding(done, NO_BACKEND)).toBe(false);
  });

  it("redirects deep links that miss their prerequisites", () => {
    expect(redirectFor("sign-in", EMPTY_RECORD, NO_BACKEND, false)).toBe("language");
    expect(redirectFor("style", through("course"), NO_BACKEND, false)).toBeNull();
    expect(redirectFor("files", { ...EMPTY_RECORD, provider: "claude" }, NO_BACKEND, false)).toBe("language");
    expect(redirectFor("files", EMPTY_RECORD, NO_BACKEND, true)).toBeNull();
    expect(redirectFor("language", EMPTY_RECORD, NO_BACKEND, false)).toBeNull();
  });
});

describe("draft queue", () => {
  it("queues nothing before answers are final", () => {
    expect(pendingOps(EMPTY_RECORD, labels)).toEqual([]);
    expect(pendingOps({ ...EMPTY_RECORD, language: "it" }, labels)).toEqual([]);
  });

  it("saves language, profile and course in order, each once", () => {
    let record = markStep(through("files"), "studies", "done");
    record = { ...record, studies: { background: "Economics, second year", goals: ["exam"] } };
    const ops = pendingOps(record, labels);
    expect(ops.map((op) => op.id)).toEqual(["language", "profile", "course"]);
    const [language, profile, course] = ops;
    expect(profile).toMatchObject({ background: "Economics, second year\n\nGoals: exam." });
    expect(course).toMatchObject({ mode: "add", code: null });
    record = applySaved(record, language!);
    record = applySaved(record, profile!);
    record = applySaved(record, course!, "STAT");
    expect(record.courseCode).toBe("STAT");
    expect(pendingOps(record, labels)).toEqual([]);
  });

  it("a skipped profile still acknowledges the step (profile --skip)", () => {
    const record = markStep(through("course"), "studies", "skipped");
    expect(pendingOps(record, labels).find((op) => op.id === "profile")).toMatchObject({ background: null });
  });

  it("waits for the style before adding the course", () => {
    const record = markStep(through("style"), "course", "done");
    expect(pendingOps(record, labels).map((op) => op.id)).not.toContain("course");
  });

  it("an answer changed after saving becomes an edit", () => {
    let record = through("files");
    const course = pendingOps(record, labels).find((op) => op.id === "course");
    record = applySaved(record, course!, "STAT");
    record = { ...record, style: { ...DEFAULT_STYLE, detail: "detailed" } };
    expect(pendingOps(record, labels).find((op) => op.id === "course")).toMatchObject({ mode: "edit", code: "STAT" });
  });

  it("maps the style to backend fields", () => {
    const payload = coursePayload({ ...statistics, examUnknown: true, examFormat: "written", anythingElse: " Lots of proofs " }, { ...DEFAULT_STYLE, images: "none", recapLanguage: "it" });
    expect(payload.settingsJson.style).toEqual({
      writing_style: "clear_study",
      list_density: "balanced",
      image_policy: "none",
      image_limit: null,
      question_count: 5,
      recap: true,
      recap_language: "it",
    });
    expect(payload.settingsJson.assessment).toBe("");
    expect(payload.contextAnswer).toBe("Lots of proofs");
    expect(coursePayload(statistics, DEFAULT_STYLE).contextAnswer).toBeUndefined();
  });

  it("writes goals alone when there is no text", () => {
    expect(backgroundText({ background: " ", goals: ["exam", "work"] }, labels.goalLabel, "Goals:")).toBe("Goals: exam, work.");
    expect(backgroundText({ background: "Law", goals: [] }, labels.goalLabel, "Goals:")).toBe("Law");
  });

  it("lists the checklist with the running operation", () => {
    const record = through("files");
    expect(draftChecklist(record, labels, "language")).toEqual([
      { id: "language", state: "running" },
      { id: "profile", state: "waiting" },
      { id: "course", state: "waiting" },
    ]);
  });
});

describe("setup progress", () => {
  it("shows measured percent, stage bytes and data age while running", () => {
    const view = setupView(status({}), NOW);
    expect(view).toMatchObject({ phase: "running", percent: 62, stageTitle: "LibreOffice", secondsSinceData: 4 });
    expect(megabytes(view.bytesDone ?? 0)).toBe(212);
  });

  it("never invents a percent", () => {
    expect(setupView(status({ overallDone: null, overallTotal: null }), NOW).percent).toBeNull();
    expect(setupView(status({ overallDone: 1_006_000_000 }), NOW).percent).toBe(99);
  });

  it(`switches to waiting for network after ${NETWORK_WAIT_SECONDS} s without data`, () => {
    expect(setupView(status({ lastDataAt: "2026-11-26T09:41:01Z" }), NOW).phase).toBe("running");
    expect(setupView(status({ lastDataAt: "2026-11-26T09:41:00Z" }), NOW).phase).toBe("waiting");
  });

  it("maps failed, paused, complete and idle", () => {
    expect(setupView(status({ state: "failed", error: "Download interrupted" }), NOW)).toMatchObject({ phase: "failed", error: "Download interrupted" });
    expect(setupView(status({ state: "paused" }), NOW).phase).toBe("failed");
    expect(setupView(status({ state: "complete" }), NOW)).toMatchObject({ phase: "complete", percent: 100 });
    expect(setupView(status({ state: "idle" }), NOW).phase).toBe("idle");
    expect(setupView(null, NOW).phase).toBe("idle");
  });

  it("holds backend steps until setup and the queue are done", () => {
    const running = setupView(status({}), NOW);
    const complete = setupView(status({ state: "complete" }), NOW);
    expect(mustWait("course", running, 3)).toBe(false);
    expect(mustWait("files", running, 0)).toBe(true);
    expect(mustWait("files", complete, 1)).toBe(true);
    expect(mustWait("files", complete, 0)).toBe(false);
    expect(mustWait("ready", running, 0)).toBe(false);
  });
});

describe("sign-in", () => {
  it("maps each provider state", () => {
    expect(signInPhase(null, null, 0)).toBe("checking");
    for (const state of ["missing", "incompatible", "login_required", "unverified"] as const) expect(signInPhase(state, null, 0)).toBe(state);
    expect(signInPhase("ready", null, 0)).toBe("ready");
    expect(signInPhase("something_new", null, 0)).toBe("login_required");
  });

  it("waits while the browser sign-in runs, then times out after 5 minutes", () => {
    expect(signInPhase("login_required", 1000, 1000 + LOGIN_TIMEOUT_MS - 1)).toBe("waiting");
    expect(signInPhase("login_required", 1000, 1000 + LOGIN_TIMEOUT_MS)).toBe("timeout");
    expect(signInPhase("ready", 1000, 1000 + LOGIN_TIMEOUT_MS)).toBe("ready");
    expect(AUTO_ADVANCE_MS).toBe(800);
  });
});

describe("validation", () => {
  it("requires a course name of reasonable length", () => {
    expect(validateCourse(defaultCourse("en"))).toEqual({ name: "required" });
    expect(validateCourse({ ...statistics, name: "   " })).toEqual({ name: "required" });
    expect(validateCourse({ ...statistics, name: "x".repeat(81) })).toEqual({ name: "tooLong" });
    expect(validateCourse(statistics)).toEqual({});
  });

  it("accepts only HTTPS or webcal calendar links without credentials", () => {
    expect(validateCalendarUrl("")).toBe("empty");
    expect(validateCalendarUrl("not a link")).toBe("invalid");
    expect(validateCalendarUrl("http://example.org/a.ics")).toBe("scheme");
    expect(validateCalendarUrl("https://user:secret@example.org/a.ics")).toBe("credentials");
    expect(validateCalendarUrl("webcal://example.org/a.ics")).toBeNull();
    expect(validateCalendarUrl(" https://calendar.google.com/calendar/ical/x/private-y/basic.ics ")).toBeNull();
  });

  it("maps calendar codes to one fix, unknown codes to retry", () => {
    expect(calendarProblem("GOOGLE_FEED_UNAVAILABLE")).toEqual({ code: "GOOGLE_FEED_UNAVAILABLE", fix: "showGuide" });
    expect(calendarProblem("NETWORK_ERROR").fix).toBe("retry");
    expect(calendarProblem("INVALID_URL").fix).toBe("editLink");
    expect(calendarProblem("NEW_CODE")).toEqual({ code: "INTERNAL_ERROR", fix: "retry" });
  });

  it("proposes a period to the end of the semester", () => {
    expect(defaultPeriod(new Date(2026, 10, 26))).toEqual({ start: "2026-11-26", end: "2027-01-31" });
    expect(defaultPeriod(new Date(2027, 2, 2))).toEqual({ start: "2027-03-02", end: "2027-07-31" });
  });
});

describe("previews", () => {
  it("counts the calendar's lessons, clashes and courseless events", () => {
    const events = [
      { sourceID: "a", courseCode: "STAT", eligibility: "SCHEDULED" },
      { sourceID: "a", courseCode: "STAT", eligibility: "CONFLICT" },
      { sourceID: "a", courseCode: null, eligibility: "NEEDS_REVIEW" },
      { sourceID: "a", courseCode: null, eligibility: "CANCELLED" },
      { sourceID: "b", courseCode: null, eligibility: "SCHEDULED" },
    ];
    expect(schedulePreview(events, "a")).toEqual({ lessons: 3, clashes: 1, needCourse: 1 });
  });

  it("reads a ready folder preview", () => {
    const preview = folderPreview({
      status: "READY",
      note_files: { personal_notes: "~/Statistics/Appunti personali.docx", handout: "~/MyNotes/STAT_COURSE_NOTES.docx" },
      inventory: { file_counts: { materials: 14, recordings: 2 }, files: { personal_notes: [] } },
    });
    expect(preview).toEqual({
      status: "ready",
      materials: 14,
      recordings: 2,
      notebook: { name: "Appunti personali.docx", isNew: true },
      courseNotes: "STAT_COURSE_NOTES.docx",
      question: null,
    });
  });

  it("turns NEEDS_SELECTION into one question", () => {
    const preview = folderPreview({
      status: "NEEDS_SELECTION",
      proposed_files: { personal_notes: "C:\\Corsi\\Stat\\a.docx", handout: null },
      ambiguities: { notes_file: ["C:\\Corsi\\Stat\\a.docx", "C:\\Corsi\\Stat\\b.docx"] },
      inventory: { file_counts: {}, files: { personal_notes: ["C:\\Corsi\\Stat\\a.docx"] } },
    });
    expect(preview.status).toBe("selection");
    expect(preview.question).toEqual({ role: "notes_file", options: ["C:\\Corsi\\Stat\\a.docx", "C:\\Corsi\\Stat\\b.docx"] });
    expect(preview.notebook).toEqual({ name: "a.docx", isNew: false });
  });
});

describe("microphone, shortcut and first notes", () => {
  const base = { permission: "AUTHORIZED", requested: true, current: null, exporting: false, sessions: [], requestedAt: Date.parse("2026-11-26T09:40:00Z") };

  it("explains first, then records, saves and offers playback", () => {
    expect(micPhase({ ...base, requested: false }).phase).toBe("intro");
    expect(micPhase(base).phase).toBe("starting");
    expect(micPhase({ ...base, current: { id: "t", status: "RECORDING", isTest: true, actualStart: "2026-11-26T09:40:02Z" } })).toEqual({
      phase: "recording",
      audio: null,
      startedAt: "2026-11-26T09:40:02Z",
    });
    expect(micPhase({ ...base, exporting: true }).phase).toBe("saving");
    const sessions = [{ id: "t", status: "COMPLETED", isTest: true, outputPath: "/tmp/test.m4a", actualStart: "2026-11-26T09:40:02Z" }];
    expect(micPhase({ ...base, sessions })).toMatchObject({ phase: "recorded", audio: "/tmp/test.m4a" });
  });

  it("ignores ordinary recordings and shows the denied state first", () => {
    const sessions = [{ id: "r", status: "COMPLETED", isTest: false, outputPath: "/tmp/lesson.m4a", actualStart: "2026-11-26T09:40:02Z" }];
    expect(micPhase({ ...base, sessions }).phase).toBe("starting");
    expect(micPhase({ ...base, permission: "DENIED", sessions }).phase).toBe("denied");
  });

  it("detects ⌥⇧R by physical key", () => {
    expect(isRecordShortcut({ altKey: true, shiftKey: true, ctrlKey: false, metaKey: false, code: "KeyR" })).toBe(true);
    expect(isRecordShortcut({ altKey: true, shiftKey: false, ctrlKey: false, metaKey: false, code: "KeyR" })).toBe(false);
    expect(isRecordShortcut({ altKey: true, shiftKey: true, ctrlKey: true, metaKey: false, code: "KeyR" })).toBe(false);
  });

  it("classifies imports and builds lesson ids", () => {
    expect(classifyImport("Lesson 1.m4a")).toBe("audio");
    expect(classifyImport("lesson.TXT")).toBe("transcript");
    expect(classifyImport("slides.pdf")).toBeNull();
    expect(lectureIdFor("STAT")).toBe("STAT-L01");
    expect(lectureIdFor("STAT", 12)).toBe("STAT-L12");
  });

  it("summarizes done and finish-later steps", () => {
    const record = markStep(markStep(through("schedule"), "schedule", "skipped"), "sign-in", "done");
    const summary = readySummary(record, NO_BACKEND);
    expect(summary.find((item) => item.step === "files")?.state).toBe("done");
    expect(summary.find((item) => item.step === "schedule")?.state).toBe("later");
    expect(summary.find((item) => item.step === "microphone")?.state).toBe("later");
  });
});

describe("keyboard", () => {
  const body = { tagName: "BODY", insideDialog: false };

  it("Enter runs the primary action outside controls", () => {
    expect(keyIntent("Enter", body, { defaultPrevented: false, escapeBack: true })).toBe("primary");
    expect(keyIntent("Enter", { tagName: "BUTTON", insideDialog: false }, { defaultPrevented: false, escapeBack: true })).toBeNull();
    expect(keyIntent("Enter", { tagName: "TEXTAREA", insideDialog: false }, { defaultPrevented: false, escapeBack: true })).toBeNull();
    expect(keyIntent("Enter", body, { defaultPrevented: true, escapeBack: true })).toBeNull();
    expect(keyIntent("Enter", body, { defaultPrevented: false, escapeBack: true, composing: true })).toBeNull();
  });

  it("Esc goes back only where safe", () => {
    expect(keyIntent("Escape", body, { defaultPrevented: false, escapeBack: true })).toBe("back");
    expect(keyIntent("Escape", body, { defaultPrevented: false, escapeBack: false })).toBeNull();
    expect(keyIntent("Escape", { tagName: "INPUT", value: "Stat", insideDialog: false }, { defaultPrevented: false, escapeBack: true })).toBeNull();
    expect(keyIntent("Escape", { tagName: "INPUT", value: "", insideDialog: false }, { defaultPrevented: false, escapeBack: true })).toBe("back");
    expect(keyIntent("Escape", { tagName: "DIV", insideDialog: true }, { defaultPrevented: false, escapeBack: true })).toBeNull();
    expect(keyIntent("Tab", body, { defaultPrevented: false, escapeBack: true })).toBeNull();
  });
});
