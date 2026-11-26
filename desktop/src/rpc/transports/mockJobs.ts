/**
 * Job records and notifications in the server's shapes (serve_jobs.py), for every mock job:
 * `{jobId, job}` answers, records with `id`/`method`/`params`, upper-case states and
 * epoch-second times kept in `job.list`, and `job.started` / `job.progress` / `job.finished`.
 */
import type { Json } from "./fixtures";

export type JsonObject = { [key: string]: Json };

export interface MockJobContext {
  state: Map<string, Json>;
  emit: (method: string, params: Json) => void;
  now: () => Date;
  /** A timer that runs even when the mock is frozen (`simulate: false`), cleared on close. */
  soon: (ms: number, run: () => void) => void;
  /** How long a quick job runs before `job.finished`. */
  quickMs: number;
}

/** How a quick job ends: the command's `{exitCode, data}`, or the error of a `mynotes: …` failure. */
export type QuickOutcome = { exitCode: number; data: Json } | { exitCode: number; error: JsonObject };

function isObject(value: Json | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const seconds = (date: Date) => date.getTime() / 1000;
const iso = (date: Date) => date.toISOString().replace(/\.\d{3}Z$/, "Z");

let pids = 41_000;
let quickJobs = 0;

export function mockJobs(ctx: MockJobContext) {
  const list = (): Json[] => {
    const value = ctx.state.get("job.list");
    if (isObject(value) && Array.isArray(value.jobs)) return value.jobs;
    const jobs: Json[] = [];
    ctx.state.set("job.list", { jobs });
    return jobs;
  };

  const api = {
    find: (id: string): JsonObject | undefined => list().find((job): job is JsonObject => isObject(job) && job.id === id),
    all: (): JsonObject[] => list().filter(isObject),

    /** Records a RUNNING job, announces it after `delayMs` (or now) and returns `{jobId, job}`. */
    start(id: string, method: string, params: JsonObject, announce: (run: () => void) => void): { jobId: string; job: JsonObject } {
      pids += 1;
      const job: JsonObject = { id, method, params, state: "RUNNING", pid: pids, startedAt: seconds(ctx.now()), finishedAt: null, exitCode: null, cancelRequested: false };
      list().unshift(job);
      announce(() => ctx.emit("job.started", { jobId: id, method, params, pid: pids }));
      return { jobId: id, job: structuredClone(job) };
    },

    /**
     * A short job-lane call (`capture.connect`, `.sync`, `.enable`, `.disable`, `notes.publish`):
     * answers `{jobId, job}`, sends `job.started` at once, then `job.finished` after `quickMs` with
     * what `settle` computes then. Its default answer is the `<method>.result.json` fixture.
     */
    quick(method: string, params: JsonObject, settle: () => QuickOutcome): { jobId: string; job: JsonObject } {
      quickJobs += 1;
      const ref = api.start(`mock-${method.replace(/\./g, "-")}-${quickJobs}`, method, params, (run) => run());
      ctx.soon(ctx.quickMs, () => {
        const job = api.find(ref.jobId);
        if (!job) return;
        const outcome = settle();
        if ("error" in outcome) api.fail(job, outcome.exitCode, outcome.error);
        else api.finish(job, outcome.exitCode === 0 ? "COMPLETED" : "FAILED", outcome.exitCode, outcome.data);
      });
      return ref;
    },

    /** The `{exitCode, data}` a job method's command answers, from `<method>.result.json`. */
    result(method: string, fallback: JsonObject): { exitCode: number; data: Json } {
      const value = ctx.state.get(`${method}.result`);
      if (isObject(value) && typeof value.exitCode === "number" && "data" in value) return structuredClone({ exitCode: value.exitCode, data: value.data ?? null });
      return { exitCode: 0, data: fallback };
    },

    /** Ends a job whose command printed no JSON: FAILED with the error, as serve_jobs records it. */
    fail(job: JsonObject, exitCode: number, error: JsonObject) {
      const id = typeof job.id === "string" ? job.id : "";
      job.state = "FAILED";
      job.finishedAt = seconds(ctx.now());
      job.exitCode = exitCode;
      job.error = error;
      ctx.emit("job.finished", { jobId: id, state: "FAILED", exitCode, error });
    },

    progress(job: JsonObject, stage: string, extra: JsonObject = {}) {
      const id = typeof job.id === "string" ? job.id : "";
      const payload: JsonObject = { jobId: id, stage, at: iso(ctx.now()), ...extra };
      job.lastProgress = payload;
      ctx.emit("job.progress", payload);
    },

    /** Ends a job like serve_jobs._finish: COMPLETED on exit 0, else FAILED, CANCELLED or INTERRUPTED. */
    finish(job: JsonObject, state: "COMPLETED" | "FAILED" | "CANCELLED" | "INTERRUPTED", exitCode: number, data: Json | null) {
      const id = typeof job.id === "string" ? job.id : "";
      job.state = state;
      job.finishedAt = seconds(ctx.now());
      job.exitCode = exitCode;
      job.result = data === null ? null : { exitCode, data };
      const event: JsonObject = { jobId: id, state, exitCode, result: job.result };
      if (state === "CANCELLED" || state === "INTERRUPTED") {
        const params = isObject(job.params) ? job.params : {};
        const progress = isObject(job.lastProgress) ? job.lastProgress : {};
        const batch = typeof progress.batch_id === "string" ? progress.batch_id : null;
        job.resumeHint = { method: typeof job.method === "string" ? job.method : "", params: batch ? { ...params, resume: batch } : params };
        event.resumeHint = job.resumeHint;
      }
      ctx.emit("job.finished", event);
    },
  };
  return api;
}

export type MockJobs = ReturnType<typeof mockJobs>;
