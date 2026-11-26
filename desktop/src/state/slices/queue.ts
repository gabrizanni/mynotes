/** Transcription queue (`queue.status`, pushed by `queue.changed`). */
import type { QueueStatus } from "../../rpc";
import { failed, idle, loading, ready, type Load } from "../load";
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
  };
}
