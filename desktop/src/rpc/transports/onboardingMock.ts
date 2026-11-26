/**
 * Mock behaviour for the onboarding methods, merged into the
 * handlers of `createMockTransport`. Like the rest of the mock, timers only run when
 * `simulate` is on; with `simulate: false` every call answers its fixture (screenshots).
 *
 * - `setup.run` starts a byte-accurate download simulation that `setup.status` reports;
 * - `provider.login` turns the provider ready after a few seconds (`provider.authChanged`);
 * - `profile.save`, `course.add`, `course.link` update `config.get` (`config.changed`);
 * - `capture.connect` / `.unsubscribe` / `.enable` / `.disable` / `.pause` / `.resume` / `.test`
 *   update the dashboard (`capture.changed`), the microphone test lasts 60 s like the real recorder;
 * - `capture.connect`, `.sync`, `.enable` and `.disable` are jobs, as on the server: `{jobId, job}`,
 *   then `job.finished` with `<method>.result.json` (the dashboard changes when the job ends);
 * - `lesson.process` runs the stage sequence of a notes job (a `transcript` skips TRANSCRIBING).
 */
import type { Json } from "./fixtures";
import type { MockJobs, QuickOutcome } from "./mockJobs";

type JsonObject = { [key: string]: Json };

export interface OnboardingMockContext {
  state: Map<string, Json>;
  simulate: boolean;
  now: () => Date;
  stageMs: number;
  later: (ms: number, run: () => void) => void;
  emit: (method: string, params: Json) => void;
  jobs: MockJobs;
}

/** Component sizes close to the real payload (about 1 GB, UX-SPEC §2). */
const COMPONENTS = [
  { id: "runtime", title: "MyNotes and Recorder", bytes: 80_000_000 },
  { id: "speech", title: "Speech model", bytes: 626_000_000 },
  { id: "libreoffice", title: "LibreOffice", bytes: 300_000_000 },
] as const;
const TOTAL_BYTES = COMPONENTS.reduce((sum, component) => sum + component.bytes, 0);
const BYTES_PER_SECOND = 12_000_000;
const AFTER_DOWNLOAD = [
  { stage: "installation", seconds: 4 },
  { stage: "workspace", seconds: 2 },
  { stage: "verification", seconds: 4 },
] as const;
const MIC_TEST_MS = 60_000;
const LESSON_STAGES = ["TRANSCRIBING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"] as const;
const IMPORT_STAGES = ["PREPARING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"] as const;

function isObject(value: Json | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function iso(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function params(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
}

function text(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/** Derive a course code the way a student would abbreviate it (STAT, MICRO, …). */
export function mockCourseCode(name: string, taken: readonly string[]): string {
  const letters = name
    .normalize("NFD")
    .replace(/[^A-Za-z0-9 ]/g, "")
    .toUpperCase();
  const words = letters.split(/\s+/).filter(Boolean);
  const base = (words.length > 1 ? words.map((word) => word.slice(0, 3)).join("") : (words[0] ?? "COURSE").slice(0, 5)) || "COURSE";
  let code = base;
  for (let n = 2; taken.includes(code); n += 1) code = `${base}${n}`;
  return code;
}

export function onboardingMockHandlers(ctx: OnboardingMockContext): Record<string, (params: unknown) => Json> {
  const { state, simulate, now, later, emit, jobs } = ctx;
  let setupStartedAt: number | null = null;
  let setupOffset = 0;
  let jobCounter = 0;

  const data = (method: string): JsonObject | null => {
    const value = state.get(method);
    if (!isObject(value)) return null;
    return isObject(value.data) ? value.data : null;
  };
  const ok = (value: JsonObject): Json => ({ exitCode: 0, data: value });
  const fixture = (method: string, fallback: Json): Json => structuredClone(state.get(method) ?? fallback);

  const config = () => data("config.get");
  const dashboard = () => data("capture.dashboard");
  const completeGroup = (...groups: string[]) => {
    const current = config();
    if (!current || !isObject(current.onboarding)) return;
    const done = Array.isArray(current.onboarding.completed_groups) ? current.onboarding.completed_groups.filter((g): g is string => typeof g === "string") : [];
    current.onboarding = { ...current.onboarding, completed_groups: [...new Set([...done, ...groups])] };
    emit("config.changed", current);
  };
  const emitDashboard = () => {
    const current = dashboard();
    if (current) emit("capture.changed", current);
  };

  const setupSnapshot = (): Json => {
    const base = state.get("setup.status");
    if (!simulate || setupStartedAt === null) return structuredClone(base ?? { state: "idle" });
    const elapsed = Math.max(0, (now().getTime() - setupStartedAt) / 1000);
    const downloaded = Math.min(TOTAL_BYTES, setupOffset + elapsed * BYTES_PER_SECOND);
    let start = 0;
    const components: JsonObject[] = COMPONENTS.map((component) => {
      const done = Math.max(0, Math.min(component.bytes, downloaded - start));
      start += component.bytes;
      return {
        id: component.id,
        title: component.title,
        state: done >= component.bytes ? "done" : done > 0 ? "running" : "waiting",
        bytesDone: Math.round(done),
        bytesTotal: component.bytes,
      };
    });
    const lastDataAt = iso(now());
    if (downloaded < TOTAL_BYTES) {
      const active = components.find((component) => component.state === "running") ?? components[0];
      return {
        state: "running",
        stage: "components",
        title: active?.title ?? null,
        bytesDone: active?.bytesDone ?? null,
        bytesTotal: active?.bytesTotal ?? null,
        lastDataAt,
        error: null,
        overallDone: Math.round(downloaded),
        overallTotal: TOTAL_BYTES,
        components,
      };
    }
    let after = (TOTAL_BYTES - setupOffset) / BYTES_PER_SECOND;
    for (const step of AFTER_DOWNLOAD) {
      after += step.seconds;
      if (elapsed < after) {
        return { state: "running", stage: step.stage, title: null, bytesDone: null, bytesTotal: null, lastDataAt, error: null, overallDone: TOTAL_BYTES, overallTotal: TOTAL_BYTES, components };
      }
    }
    return { state: "complete", stage: "ready", title: null, bytesDone: null, bytesTotal: null, lastDataAt, error: null, overallDone: TOTAL_BYTES, overallTotal: TOTAL_BYTES, components };
  };

  /** Audio is transcribed first; a transcript is imported instead (no TRANSCRIBING), as on the server. */
  const startLessonJob = (lectureId: string, source: { audio: string } | { transcript: string } | null): Json => {
    jobCounter += 1;
    const jobId = `mock-lesson-${jobCounter}`;
    const stages = source && "transcript" in source ? IMPORT_STAGES : LESSON_STAGES;
    const ref = jobs.start(jobId, "lesson.process", { lectureId, ...source }, (run) => later(10, run));
    const job = jobs.find(jobId);
    if (!job) return ref;
    job.lastProgress = { jobId, stage: stages[0], at: iso(now()) };
    stages.slice(1).forEach((stage, index) => {
      later(ctx.stageMs * (index + 1), () => jobs.progress(job, stage));
    });
    later(ctx.stageMs * stages.length, () => jobs.finish(job, "COMPLETED", 0, { status: "COMPLETED" }));
    return ref;
  };

  const jobParams = (raw: unknown): JsonObject => (isObject(raw as Json) ? structuredClone(raw as JsonObject) : {});

  /** `capture connect` once its download is done: the fixture answer, plus the imported events. */
  const connectCalendar = (raw: unknown): QuickOutcome => {
    const file = params(params(raw).source);
    const result = jobs.result("capture.connect", { status: "CONNECTED", sourceID: "timetable" });
    const board = dashboard();
    if (!isObject(result.data) || result.data.status !== "CONNECTED" || !board) return result;
    const sourceID = text(result.data.sourceID) ?? "timetable";
    const courses = Array.isArray(board.courses) ? board.courses.filter(isObject) : [];
    const course = courses[courses.length - 1];
    const code = course && typeof course.code === "string" ? course.code : null;
    const title = course && typeof course.name === "string" ? course.name : "Lesson";
    const timezone = text(file.timezone) ?? "Europe/Rome";
    const first = Date.UTC(2026, 11, 1, 8, 0);
    const events: JsonObject[] = [];
    for (let index = 0; index < 34; index += 1) {
      const startsAt = new Date(first + Math.floor(index / 2) * 7 * 86_400_000 + (index % 2) * 2 * 86_400_000);
      const endsAt = new Date(startsAt.getTime() + 90 * 60_000);
      const orphan = index >= 31;
      events.push({
        id: `${sourceID}:${index}`,
        sourceID,
        courseCode: orphan ? null : code,
        lessonNumber: orphan ? null : index + 1,
        title: orphan ? "Office hours" : title,
        startsAt: iso(startsAt),
        endsAt: iso(endsAt),
        timezone,
        eligibility: index === 9 || index === 20 ? "CONFLICT" : orphan ? "NEEDS_REVIEW" : "SCHEDULED",
        reason: "",
        localChanges: false,
        recordingEnabled: !orphan,
        sourceEligibility: "SCHEDULED",
        special: false,
      });
    }
    const existing = Array.isArray(board.events) ? board.events.filter((event) => isObject(event) && event.sourceID !== sourceID) : [];
    const sources = Array.isArray(board.sources) ? board.sources.filter((source) => isObject(source) && source.sourceID !== sourceID) : [];
    board.events = [...existing, ...events];
    board.sources = [
      ...sources,
      { sourceID, label: text(file.label) ?? "Timetable", host: "calendar", timezone, mappings: [], importMode: "all_timed", summary: { imported: 34, recognizedCourses: 1, pending: 3, excluded: 0 } },
    ];
    board.configured = true;
    emitDashboard();
    return result;
  };

  /** `capture enable|disable`: the recorder service on or off. */
  const setRecorder = (enabled: boolean): QuickOutcome => {
    const board = dashboard();
    if (board) {
      board.enabled = enabled;
      board.running = enabled;
      emitDashboard();
    }
    return jobs.result(enabled ? "capture.enable" : "capture.disable", { status: enabled ? "ENABLED" : "DISABLED", message: null });
  };

  return {
    "setup.run": () => {
      const base = state.get("setup.status");
      const resumable = isObject(base) && (base.state === "failed" || base.state === "paused") && typeof base.overallDone === "number";
      setupOffset = resumable && typeof base.overallDone === "number" ? base.overallDone : 0;
      if (simulate) setupStartedAt = now().getTime();
      // The shell's `setup_run` answers the first status (src/rpc/setup.ts).
      return setupSnapshot();
    },

    "setup.status": () => setupSnapshot(),

    "provider.login": (raw) => {
      const provider = text(params(raw).provider) ?? "claude";
      jobCounter += 1;
      const ref = jobs.start(`mock-login-${jobCounter}`, "provider.login", { provider }, (run) => later(10, run));
      later(4000, () => {
        const next: JsonObject = { provider, status: "ready", version: "2.3.1" };
        state.set("provider.status", { exitCode: 0, data: next });
        // serve._watch_login: `status` is the whole provider check.
        emit("provider.authChanged", { provider, jobId: ref.jobId, status: next });
        const job = jobs.find(ref.jobId);
        if (job) jobs.finish(job, "COMPLETED", 0, { status: "ready", provider });
      });
      return ref;
    },

    "provider.status": (raw): Json => {
      const current = state.get("provider.status");
      const provider = text(params(raw).provider) ?? "claude";
      const data = isObject(current) && isObject(current.data) ? structuredClone(current.data) : { status: "login_required", version: null };
      return { exitCode: 0, data: { ...data, provider } };
    },

    "preferences.saveModels": (raw) => {
      const request = params(raw);
      const models = params(request.models);
      const saved = fixture("preferences.get", { exitCode: 0, data: {} });
      if (isObject(saved) && isObject(saved.data)) saved.data.language = text(models.language) ?? "en";
      return saved;
    },

    "profile.save": () => {
      completeGroup("personal");
      return ok({ status: "SAVED" });
    },

    "course.add": (raw) => {
      const request = params(raw);
      const name = text(request.name) ?? "Course";
      const current = config();
      const courses = current && Array.isArray(current.courses) ? current.courses : [];
      const taken = courses.filter(isObject).map((course) => (typeof course.code === "string" ? course.code : ""));
      const code = mockCourseCode(name, taken);
      const course: JsonObject = { code, name, notes_language: text(request.notesLanguage) ?? "en" };
      if (current) current.courses = [...courses, course];
      const board = dashboard();
      if (board && Array.isArray(board.courses)) board.courses = [...board.courses, { code, name, contextPending: false }];
      completeGroup("courses", "style");
      emitDashboard();
      return ok({ status: "ADDED", code, course });
    },

    "course.link": (raw) => {
      const request = params(raw);
      const result = fixture("course.link", { exitCode: 0, data: { status: "READY" } });
      if (!isObject(result) || !isObject(result.data)) return result;
      const answer = result.data;
      const chosen = text(request.notesFile);
      if (answer.status === "NEEDS_SELECTION" && (chosen || request.notes || request.recordings)) {
        const proposed = isObject(answer.proposed_files) ? answer.proposed_files : {};
        answer.status = "READY";
        answer.note_files = chosen ? { ...proposed, personal_notes: chosen } : proposed;
        answer.ambiguities = null;
        result.exitCode = 0;
      }
      if (request.dryRun !== true && answer.status === "READY") {
        answer.status = "LINKED";
        answer.saved = true;
        answer.publication_mode = text(request.publicationMode) ?? "automatic";
        completeGroup("paths");
      }
      return result;
    },

    "capture.connect": (raw) => jobs.quick("capture.connect", jobParams(raw), () => connectCalendar(raw)),
    "capture.sync": (raw) => jobs.quick("capture.sync", jobParams(raw), () => jobs.result("capture.sync", { status: "SYNCED", message: null })),
    "capture.enable": (raw) => jobs.quick("capture.enable", jobParams(raw), () => setRecorder(true)),
    "capture.disable": (raw) => jobs.quick("capture.disable", jobParams(raw), () => setRecorder(false)),

    "capture.unsubscribe": (raw) => {
      const source = text(params(raw).source);
      const board = dashboard();
      if (board) {
        board.sources = Array.isArray(board.sources) ? board.sources.filter((item) => isObject(item) && item.sourceID !== source) : [];
        board.events = Array.isArray(board.events) ? board.events.filter((item) => isObject(item) && item.sourceID !== source) : [];
        emitDashboard();
      }
      return ok({ status: "UNSUBSCRIBED", sourceID: source });
    },

    "capture.pause": () => {
      const board = dashboard();
      if (board) {
        board.paused = true;
        emitDashboard();
      }
      return ok({ status: "PAUSED", activeRecordingUnaffected: true });
    },

    "capture.resume": () => {
      const board = dashboard();
      if (board) {
        board.paused = false;
        emitDashboard();
      }
      return ok({ status: "RESUMED", activeRecordingUnaffected: true });
    },

    "capture.test": () => {
      const requestID = `mock-test-${now().getTime().toString(16)}`;
      const board = dashboard();
      later(400, () => {
        if (!board || !isObject(board.runtime)) return;
        const session: JsonObject = {
          id: `session-${requestID}`,
          event: { id: `test:${requestID}`, courseCode: null, lessonNumber: null, title: "Microphone test" },
          status: "RECORDING",
          isTest: true,
          actualStart: iso(now()),
          outputPath: null,
        };
        board.runtime = { ...board.runtime, microphonePermission: "AUTHORIZED", recording: true, exporting: false, currentSession: session };
        emitDashboard();
        later(MIC_TEST_MS, () => {
          if (!isObject(board.runtime)) return;
          board.runtime = { ...board.runtime, recording: false, exporting: true, currentSession: { ...session, status: "EXPORTING" } };
          emitDashboard();
          later(1200, () => {
            if (!isObject(board.runtime)) return;
            const sessions = Array.isArray(board.runtime.sessions) ? board.runtime.sessions : [];
            const saved: JsonObject = { ...session, status: "COMPLETED", outputPath: "~/Documents/MyNotes/Recordings/Microphone test.m4a" };
            board.runtime = { ...board.runtime, exporting: false, currentSession: null, sessions: [...sessions, saved] };
            emitDashboard();
            emit("capture.requestCompleted", { requestID, status: "COMPLETED", message: null });
          });
        });
      });
      return ok({ status: "PENDING", requestID, message: null });
    },

    "lesson.process": (raw) => {
      const request = params(raw);
      const audio = text(request.audio);
      const transcript = text(request.transcript);
      return startLessonJob(text(request.lectureId) ?? "COURSE-L01", transcript ? { transcript } : audio ? { audio } : null);
    },
  };
}
