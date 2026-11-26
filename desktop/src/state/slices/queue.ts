/** Transcription queue (`queue.status`, pushed by `queue.changed`). */
import type { QueueJobAction, QueueStatus } from "../../rpc";
import { failed, idle, loading, ready, type Load } from "../load";
import { attempt } from "../result";
import type { SliceContext } from "../types";

export interface QueueState {
  readonly load: Load;
  readonly status: QueueStatus | null;
}

export const queueInitial: QueueState = { load: idle, status: null };

export function queueActions(ctx: SliceContext<"queue">) {
  return {
    apply: (status: QueueStatus) => {
      ctx.set({ status, load: ready });
    },
    refresh: async () => {
      ctx.set({ load: ctx.get().status ? ctx.get().load : loading });
      try {
        const { data } = await ctx.rpc.call("queue.status");
        ctx.set({ status: data, load: ready });
      } catch (error) {
        ctx.set({ load: failed(error) });
      }
    },
    // BEGIN workspace screens: Activity controls. `queue.changed` follows each call; the local
    // patch only makes the controls answer at once.
    setPaused: (paused: boolean) =>
      attempt(async () => {
        const { data } = await ctx.rpc.call(paused ? "queue.pause" : "queue.resume");
        const status = ctx.get().status;
        if (status) ctx.set({ status: { ...status, paused } });
        return data;
      }),
    setParallel: (parallel: number) =>
      attempt(async () => {
        const { data } = await ctx.rpc.call("queue.configure", { parallel });
        const status = ctx.get().status;
        if (status) ctx.set({ status: { ...status, parallel } });
        return data;
      }),
    /** Transcribe every ready recording, or only the given ones. */
    enqueue: (recordingIds?: readonly string[]) =>
      attempt(async () => (await ctx.rpc.call("queue.enqueue", recordingIds ? { readyRecording: [...recordingIds] } : { allUntranscribed: true })).data),
    clearWaiting: () =>
      attempt(async () => {
        const { data } = await ctx.rpc.call("queue.clear");
        const status = ctx.get().status;
        if (status) ctx.set({ status: { ...status, jobs: status.jobs.filter((job) => job.status !== "QUEUED") } });
        return data;
      }),
    jobAction: (jobId: string, action: QueueJobAction) =>
      attempt(async () => {
        const { data } = await ctx.rpc.call("queue.job", { jobId, action });
        const status = ctx.get().status;
        if (status && action === "remove") ctx.set({ status: { ...status, jobs: status.jobs.filter((job) => job.id !== jobId) } });
        if (status && action === "cancel") ctx.set({ status: { ...status, jobs: status.jobs.map((job) => (job.id === jobId ? { ...job, status: "CANCELLING" } : job)) } });
        return data;
      }),
    finishSaving: (recording: string) => attempt(async () => (await ctx.rpc.call("queue.finish", { recording })).data),
    // END workspace screens
  };
}
