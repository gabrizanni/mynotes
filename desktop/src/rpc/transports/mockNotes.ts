/**
 * Mock behaviour for the Generate notes flow and lesson detail.
 * Plugged into the mock transport (mock.ts); every handler mirrors what `mynotes serve` does:
 *
 * - `notes.run` starts a job and walks it through CHECKING → GENERATING → BUILDING_WORD →
 *   UPDATING_WORD with `job.progress`, then `job.finished`. How it ends comes from the fixture
 *   `notes.run.json` › `simulation` (`outcome`, `stopAt`, `warnings`), patched per scenario.
 *   `resume` continues a paused batch and always completes.
 * - `job.cancel` stops a running job (`cancelled`, progress kept, batch resumable).
 * - `transcription.run` transcribes for a while, then adds the transcript to the library.
 * - `notes.plan` and `library.transcript` answer for the lessons actually asked.
 * - `notes.publish` is a job, as on the server: `job.finished` carries `notes.publish.result.json`.
 */
import type { Json } from "./fixtures";
import type { JsonObject, MockJobs } from "./mockJobs";

export interface NotesMockContext {
  state: Map<string, Json>;
  emit: (method: string, params: Json) => void;
  jobs: MockJobs;
  simulate: boolean;
  stageMs: number;
  latencyMs: number;
  now: () => Date;
}

const STAGES = ["CHECKING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"] as const;

function isObject(value: Json | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function iso(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

function asParams(params: unknown): Record<string, unknown> {
  return typeof params === "object" && params !== null ? (params as Record<string, unknown>) : {};
}

const str = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

export function createNotesMock(ctx: NotesMockContext) {
  const timersByJob = new Map<string, Set<ReturnType<typeof setTimeout>>>();
  let counter = 0;

  const later = (jobId: string, ms: number, run: () => void) => {
    if (!ctx.simulate) return;
    const set = timersByJob.get(jobId) ?? new Set();
    const timer = setTimeout(() => {
      set.delete(timer);
      run();
    }, ms);
    set.add(timer);
    timersByJob.set(jobId, set);
  };
  const stop = (jobId: string) => {
    for (const timer of timersByJob.get(jobId) ?? []) clearTimeout(timer);
    timersByJob.delete(jobId);
  };
  const simulation = (): { outcome: string; stopAt: string | null; warnings: Json[]; weights: number[] } => {
    const fixture = ctx.state.get("notes.run");
    const value = isObject(fixture) && isObject(fixture.simulation) ? fixture.simulation : {};
    return {
      outcome: str(value.outcome) ?? "COMPLETED",
      stopAt: str(value.stopAt),
      warnings: Array.isArray(value.warnings) ? value.warnings : [],
      weights: Array.isArray(value.weights) ? value.weights.map((weight) => (typeof weight === "number" && weight > 0 ? weight : 1)) : [],
    };
  };
  const runNotes = (params: unknown): Json => {
    const request = asParams(params) as JsonObject;
    counter += 1;
    const jobId = `mock-notes-${counter}`;
    const batch = str(request.resume) ?? `b-mock-${counter}`;
    const ref = ctx.jobs.start(jobId, "notes.run", structuredClone(request), (run) => later(jobId, ctx.latencyMs, run));
    const job = ctx.jobs.find(jobId);
    if (!job) return ref;
    // The first progress line is already in the record; the notification follows `job.started`.
    job.lastProgress = { jobId, stage: STAGES[0], at: iso(ctx.now()), batch_id: batch };
    later(jobId, ctx.latencyMs + 1, () => ctx.jobs.progress(job, STAGES[0], { batch_id: batch }));
    const fixture = simulation();
    const plan = str(request.resume) ? { ...fixture, outcome: "COMPLETED", stopAt: null, warnings: [] } : fixture;
    const stopIndex = plan.stopAt ? STAGES.findIndex((stage) => stage === plan.stopAt) : -1;
    const lastStage = stopIndex >= 0 ? stopIndex : STAGES.length - 1;
    // `weights` stretch stages relative to each other (writing takes longest), times stageMs.
    const startOf = (index: number) => STAGES.slice(0, index).reduce((sum, _stage, i) => sum + ctx.stageMs * (plan.weights[i] ?? 1), 0);
    for (let index = 1; index <= lastStage; index += 1) {
      const stage = STAGES[index] ?? "UPDATING_WORD";
      later(jobId, startOf(index), () => ctx.jobs.progress(job, stage, { batch_id: batch }));
    }
    later(jobId, startOf(lastStage + 1), () => {
      timersByJob.delete(jobId);
      const outcome = plan.outcome;
      const succeeded = ["COMPLETED", "COMPLETED_WITH_WARNINGS", "AWAITING_APPROVAL"].includes(outcome);
      const data: JsonObject = { status: outcome, batch_id: batch, ...(plan.warnings.length ? { warnings: plan.warnings } : {}) };
      // serve_jobs: exit 0 is COMPLETED; a paused or failed run exits non-zero and is FAILED.
      ctx.jobs.finish(job, succeeded ? "COMPLETED" : "FAILED", succeeded ? 0 : 2, data);
    });
    return ref;
  };

  const cancel = (params: unknown): Json => {
    const jobId = str(asParams(params).jobId) ?? "";
    const job = ctx.jobs.find(jobId);
    if (!job) return null;
    if (job.state === "RUNNING") {
      stop(jobId);
      job.cancelRequested = true;
      if (ctx.simulate) setTimeout(() => ctx.jobs.finish(job, "CANCELLED", 130, null), ctx.latencyMs);
      else ctx.jobs.finish(job, "CANCELLED", 130, null);
    }
    return structuredClone(job);
  };

  const transcribe = (params: unknown): Json => {
    const lectureId = str(asParams(params).lectureId) ?? "";
    counter += 1;
    const jobId = `mock-transcription-${counter}`;
    const ref = ctx.jobs.start(jobId, "transcription.run", { lectureId }, (run) => later(jobId, ctx.latencyMs, run));
    const job = ctx.jobs.find(jobId);
    if (!job) return ref;
    job.lastProgress = { jobId, stage: "TRANSCRIBING", at: iso(ctx.now()) };
    later(jobId, ctx.stageMs * 2, () => {
      timersByJob.delete(jobId);
      const library = ctx.state.get("library.list");
      const data = isObject(library) && isObject(library.data) ? library.data : null;
      const lessons = data && Array.isArray(data.lessons) ? data.lessons : [];
      const lesson = lessons.find((item): item is JsonObject => isObject(item) && item.id === lectureId);
      if (data && lesson) {
        const transcriptId = `t-${lectureId.toLowerCase()}`;
        lesson.transcript_ids = [transcriptId];
        const transcripts = Array.isArray(data.transcripts) ? data.transcripts : [];
        transcripts.push({ id: transcriptId, path: `~/Documents/MyNotes/workspace/transcripts/${lectureId}.txt`, title: `${lectureId}.txt`, status: "COMPLETED", course_code: lesson.course ?? null, recording_id: null, lecture_id: lectureId, created_at: iso(ctx.now()) });
        data.transcripts = transcripts;
        ctx.emit("library.changed", data);
      }
      ctx.jobs.finish(job, "COMPLETED", 0, { status: "COMPLETED" });
    });
    return ref;
  };

  const plan = (params: unknown): Json => {
    const request = asParams(params);
    const course = str(request.course) ?? "";
    const fixture = ctx.state.get("notes.plan");
    const data = isObject(fixture) && isObject(fixture.data) ? fixture.data : {};
    const known = Array.isArray(data.lessons) ? data.lessons.filter(isObject) : [];
    const library = ctx.state.get("library.list");
    const libraryLessons = isObject(library) && isObject(library.data) && Array.isArray(library.data.lessons) ? library.data.lessons.filter(isObject) : [];
    const lessons = (str(request.lessons) ?? "")
      .split(",")
      .filter(Boolean)
      .map((n): JsonObject => {
        const lectureId = `${course}-L${n.padStart(2, "0")}`;
        const entry = known.find((item) => item.lecture_id === lectureId);
        if (entry) return entry;
        const lesson = libraryLessons.find((item) => item.id === lectureId);
        const transcript = Array.isArray(lesson?.transcript_ids) && lesson.transcript_ids.length > 0;
        return { lecture_id: lectureId, transcript, slides: transcript ? [`Lecture ${Number(n)}.pdf`] : [], notebook_section: transcript ? `Lezione ${Number(n)}` : null };
      });
    const missing = lessons.some((lesson) => lesson.transcript !== true);
    return { exitCode: missing ? 2 : 0, data: { status: missing ? "NEEDS_TRANSCRIPT" : "READY", lessons } };
  };

  // The server reads the file it has on record for the lesson; the fixture's file stands in for it.
  const transcript = (params: unknown): Json => {
    const request = asParams(params);
    const name = str(request.lectureId)?.toUpperCase() ?? str(request.recordingId) ?? "";
    const fixture = structuredClone(ctx.state.get("library.transcript") ?? null);
    if (isObject(fixture) && isObject(fixture.data) && typeof fixture.data.path === "string") {
      fixture.data.path = fixture.data.path.replace(/[^/\\]+\.txt$/, `${name}.txt`);
    }
    return fixture;
  };

  const publish = (params: unknown): Json => {
    const request = asParams(params);
    const lecture = str(request.lecture)?.toUpperCase() ?? "";
    const candidate = str(request.candidate);
    return ctx.jobs.quick("notes.publish", candidate ? { lecture, candidate } : { lecture }, () => {
      const answer = ctx.jobs.result("notes.publish", { status: "PUBLISHED", lecture_id: lecture, output: null });
      if (isObject(answer.data)) answer.data.lecture_id = lecture;
      return answer;
    });
  };

  return {
    handlers: {
      "notes.run": runNotes,
      "notes.plan": plan,
      "job.cancel": cancel,
      "transcription.run": transcribe,
      "library.transcript": transcript,
      "notes.publish": publish,
    } satisfies Record<string, (params: unknown) => Json>,
    dispose: () => {
      for (const jobId of [...timersByJob.keys()]) stop(jobId);
    },
  };
}
