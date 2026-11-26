/**
 * Jobs at the wire boundary. The server sends job records (`id`, `method`, `params`, upper-case
 * `state`, epoch-second times, `result: {exitCode, data}`, docs/SERVE.md "Jobs"); features read
 * the app's `Job` (`kind`, lower-case `state`, ISO times, course and lessons, `result.status`).
 * Everything that turns one into the other lives here.
 */
import type { RpcClient } from "./client";
import { RpcCode, RpcError } from "./errors";
import { jobResults, type Job, type JobMethod, type JobRecord, type JobResult, type MethodParams, type NotificationParams } from "./schema";

export type JobFinished = NotificationParams<"job.finished">;
export type JobStarted = NotificationParams<"job.started">;

const KIND_BY_METHOD: Readonly<Record<string, string>> = {
  "notes.run": "notes",
  "lesson.process": "lesson",
  "transcription.run": "transcription",
  "provider.login": "login",
  "setup.run": "setup",
  "speech.installModel": "setup",
  // Not a notes run: publishing approves one that already ran.
  "notes.publish": "publish",
};

/** How long `runJob` waits by default; LibreOffice renders take longer. */
const JOB_TIMEOUT_MS: Readonly<Record<JobMethod, number>> = {
  "capture.connect": 120_000,
  "capture.sync": 120_000,
  "capture.enable": 60_000,
  "capture.disable": 60_000,
  "notes.publish": 300_000,
};

const STATE_BY_SERVER: Readonly<Record<string, string>> = {
  RUNNING: "running",
  COMPLETED: "succeeded",
  FAILED: "failed",
  CANCELLED: "cancelled",
  INTERRUPTED: "interrupted",
};

type Bag = Readonly<Record<string, unknown>>;

function isBag(value: unknown): value is Bag {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const text = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null);

export function jobKind(method: string): string {
  return KIND_BY_METHOD[method] ?? method.split(".")[0] ?? method;
}

export function jobState(state: string): string {
  return STATE_BY_SERVER[state] ?? state.toLowerCase();
}

/** Epoch seconds (the server) or an ISO string (progress lines) → ISO; null when absent. */
export function isoTime(value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) return new Date(value * 1000).toISOString();
  if (typeof value === "string" && !Number.isNaN(Date.parse(value))) return new Date(Date.parse(value)).toISOString();
  return null;
}

/** Lessons a job covers: `lectureId`, or `course` + `lessons: "6,7"` (notes.run). */
function lectureIdsOf(params: Bag): string[] {
  const single = text(params.lectureId);
  if (single) return [single];
  const course = text(params.course);
  const lessons = text(params.lessons);
  if (!course || !lessons) return [];
  return lessons
    .split(",")
    .map((part) => part.trim())
    .filter((part) => /^\d+$/.test(part))
    .map((number) => `${course}-L${number.padStart(2, "0")}`);
}

/** The command's own answer (`result.data`) when it carries a `status`: what outcomes read. */
export function jobOutcome(result: unknown): { status: string; [key: string]: unknown } | null {
  if (!isBag(result) || !isBag(result.data)) return null;
  const status = text(result.data.status);
  return status ? { ...result.data, status } : null;
}

function batchIdOf(params: Bag, record: { lastProgress?: unknown; result?: unknown; resumeHint?: unknown }): string | null {
  const progress = isBag(record.lastProgress) ? text(record.lastProgress.batch_id) : null;
  const data = isBag(record.result) && isBag(record.result.data) ? text(record.result.data.batch_id) : null;
  const hint = isBag(record.resumeHint) && isBag(record.resumeHint.params) ? text(record.resumeHint.params.resume) : null;
  return progress ?? data ?? hint ?? text(params.resume);
}

/** A server job record as the app's Job. */
export function toJob(record: JobRecord): Job {
  const params: Bag = isBag(record.params) ? record.params : {};
  const progress = isBag(record.lastProgress) ? record.lastProgress : null;
  const startedAt = isoTime(record.startedAt) ?? new Date(0).toISOString();
  return {
    jobId: record.id,
    kind: jobKind(record.method),
    state: jobState(record.state),
    stage: progress ? text(progress.stage) : null,
    detail: progress ? text(progress.detail) : null,
    startedAt,
    finishedAt: isoTime(record.finishedAt),
    stageStartedAt: (progress ? isoTime(progress.at) : null) ?? startedAt,
    courseCode: text(params.course) ?? text(params.lectureId)?.replace(/-L\d+$/, "") ?? null,
    lectureIds: lectureIdsOf(params),
    batchId: batchIdOf(params, record),
    exitCode: record.exitCode ?? null,
    result: jobOutcome(record.result),
  };
}

/** `job.started` carries the method and params only: the job starts running now. */
export function startedJob(event: JobStarted, now: Date): Job {
  return toJob({ id: event.jobId, method: event.method, params: event.params ?? {}, state: "RUNNING", startedAt: now.getTime() / 1000 });
}

/** The fields `job.finished` changes on a Job. */
export function finishedFields(event: JobFinished, at: string): Pick<Job, "state" | "exitCode" | "result" | "finishedAt"> & { batchId?: string } {
  const batch = batchIdOf({}, event);
  return {
    state: jobState(event.state),
    exitCode: event.exitCode ?? null,
    result: jobOutcome(event.result),
    finishedAt: at,
    ...(batch ? { batchId: batch } : {}),
  };
}

/**
 * Starts a job method and resolves with its `job.finished` notification, or `null` after
 * `timeoutMs` (the job keeps running on the server). The listener is attached before the call,
 * so a job that ends before its `{jobId}` answer arrives is not missed.
 */
export async function untilJobFinished(rpc: RpcClient, start: () => Promise<{ jobId: string }>, timeoutMs = 120_000): Promise<JobFinished | null> {
  const seen = new Map<string, JobFinished>();
  let wanted: string | null = null;
  let settle: (event: JobFinished | null) => void = () => undefined;
  const done = new Promise<JobFinished | null>((resolve) => {
    settle = resolve;
  });
  const off = rpc.on("job.finished", (event) => {
    if (event.jobId === wanted) settle(event);
    else seen.set(event.jobId, event);
  });
  const timer = setTimeout(() => settle(null), timeoutMs);
  try {
    const { jobId } = await start();
    wanted = jobId;
    const early = seen.get(jobId);
    if (early) settle(early);
    return await done;
  } finally {
    clearTimeout(timer);
    off();
  }
}

/**
 * The command's answer from a `job.finished` event, as a direct call would return it: the
 * `{exitCode, data}` result (exit codes other than 0 can still carry data, as with direct
 * calls), else the command's error as an RpcError (`-32001` with the CLI's message, …).
 */
export function jobAnswer<M extends JobMethod>(method: M, finished: JobFinished | null): JobResult<M> {
  if (!finished) throw new RpcError(method, RpcCode.Timeout, `${method} did not finish in time; it keeps running`);
  if (finished.result) {
    const parsed = jobResults[method].safeParse(finished.result);
    if (!parsed.success) throw new RpcError(method, RpcCode.InvalidResult, "The backend answered in an unexpected shape", parsed.error.issues);
    return parsed.data as JobResult<M>;
  }
  const error = finished.error;
  if (isBag(error) && typeof error.code === "number" && typeof error.message === "string") {
    throw new RpcError(method, error.code, error.message, error.data);
  }
  throw new RpcError(method, RpcCode.InternalError, `${method} ended ${finished.state} without a result`);
}

/**
 * Starts a job-lane method (docs/SERVE.md "Jobs": `{jobId}` at once, then `job.progress`, then
 * `job.finished`) and resolves with its command's `{exitCode, data}`, so a screen awaits it
 * like a direct call and keeps the same loading, success and error states.
 */
export async function runJob<M extends JobMethod>(rpc: RpcClient, method: M, params: MethodParams<M>, timeoutMs: number = JOB_TIMEOUT_MS[method]): Promise<JobResult<M>> {
  // The client's rest-parameter overloads do not resolve for a generic method name.
  const start = rpc.call.bind(rpc) as (name: M, params: MethodParams<M>) => Promise<{ jobId: string }>;
  return jobAnswer(method, await untilJobFinished(rpc, () => start(method, params), timeoutMs));
}
