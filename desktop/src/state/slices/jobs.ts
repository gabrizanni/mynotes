/**
 * Long jobs (notes, transcription, setup) tracked by the server's job registry.
 * Fed by `job.list` and the `job.started` / `job.progress` / `job.finished` notifications, each
 * turned into the app's Job by rpc/jobs.ts.
 */
import type { Job, MethodParams, NotificationParams } from "../../rpc";
import { finishedFields, isoTime, startedJob, toJob } from "../../rpc/jobs";
import { failed, idle, loading, ready, type Load } from "../load";
import { attempt } from "../result";
import type { SliceContext } from "../types";

export interface StageTiming {
  readonly stage: string;
  readonly startedAt: string;
  readonly finishedAt: string | null;
}

export interface JobsState {
  readonly load: Load;
  readonly byId: Readonly<Record<string, Job>>;
  /** Newest first. */
  readonly order: readonly string[];
  /** Stage history per job, for "elapsed time per stage" (never a percentage). */
  readonly stages: Readonly<Record<string, readonly StageTiming[]>>;
}

export const jobsInitial: JobsState = { load: idle, byId: {}, order: [], stages: {} };

function closeLast(timings: readonly StageTiming[], at: string): StageTiming[] {
  return timings.map((timing, index) => (index === timings.length - 1 && timing.finishedAt === null ? { ...timing, finishedAt: at } : timing));
}

export function jobsActions(ctx: SliceContext<"jobs">) {
  const upsert = (job: Job) =>
    ctx.set((state) => ({
      byId: { ...state.byId, [job.jobId]: job },
      order: state.order.includes(job.jobId) ? state.order : [job.jobId, ...state.order],
      stages: state.stages[job.jobId]
        ? state.stages
        : { ...state.stages, [job.jobId]: job.stage ? [{ stage: job.stage, startedAt: job.stageStartedAt ?? job.startedAt, finishedAt: null }] : [] },
    }));

  return {
    refresh: async () => {
      ctx.set({ load: ctx.get().order.length ? ctx.get().load : loading });
      try {
        const { jobs } = await ctx.rpc.call("job.list");
        for (const record of jobs) upsert(toJob(record));
        ctx.set({ load: ready });
      } catch (error) {
        ctx.set({ load: failed(error) });
      }
    },
    started: (event: NotificationParams<"job.started">) => {
      // A job the list already holds (fetched after it started) keeps what the list said.
      if (ctx.get().byId[event.jobId]) return;
      upsert(startedJob(event, new Date()));
    },
    progress: (event: NotificationParams<"job.progress">) => {
      ctx.set((state) => {
        const job = state.byId[event.jobId];
        if (!job) return {};
        const at = isoTime(event.at) ?? new Date().toISOString();
        const history = state.stages[event.jobId] ?? [];
        const last = history[history.length - 1];
        const timings = last?.stage === event.stage ? history : [...closeLast(history, at), { stage: event.stage, startedAt: at, finishedAt: null }];
        return {
          byId: { ...state.byId, [job.jobId]: { ...job, stage: event.stage, detail: event.detail ?? null, stageStartedAt: at } },
          stages: { ...state.stages, [job.jobId]: timings },
        };
      });
    },
    finished: (event: NotificationParams<"job.finished">) => {
      ctx.set((state) => {
        const job = state.byId[event.jobId];
        if (!job) return {};
        const at = new Date().toISOString();
        return {
          byId: { ...state.byId, [job.jobId]: { ...job, ...finishedFields(event, at) } },
          stages: { ...state.stages, [job.jobId]: closeLast(state.stages[job.jobId] ?? [], at) },
        };
      });
    },
    runNotes: async (params: MethodParams<"notes.run">): Promise<string> => {
      const { jobId } = await ctx.rpc.call("notes.run", params);
      return jobId;
    },
    cancel: async (jobId: string) => {
      await ctx.rpc.call("job.cancel", { jobId });
    },
    undoLastUpdate: (course: string) => attempt(async () => (await ctx.rpc.call("notes.undo", { course })).data),
  };
}
