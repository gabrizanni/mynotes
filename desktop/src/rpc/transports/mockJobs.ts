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
}

function isObject(value: Json | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const seconds = (date: Date) => date.getTime() / 1000;
const iso = (date: Date) => date.toISOString().replace(/\.\d{3}Z$/, "Z");

let pids = 41_000;

export function mockJobs(ctx: MockJobContext) {
  const list = (): Json[] => {
    const value = ctx.state.get("job.list");
    if (isObject(value) && Array.isArray(value.jobs)) return value.jobs;
    const jobs: Json[] = [];
    ctx.state.set("job.list", { jobs });
    return jobs;
  };

  return {
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
}

export type MockJobs = ReturnType<typeof mockJobs>;
