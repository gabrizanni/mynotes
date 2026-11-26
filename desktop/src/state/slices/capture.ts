/** Recorder snapshot (`capture.dashboard`, pushed by `capture.changed`). */
import type { CaptureDashboard, MethodParams } from "../../rpc";
import { failed, idle, loading, ready, type Load } from "../load";
import type { SliceContext } from "../types";

export interface CaptureState {
  readonly load: Load;
  readonly dashboard: CaptureDashboard | null;
  /** A start/stop request is waiting for the recorder's acknowledgement. */
  readonly pendingAction: "start" | "stop" | null;
}

export const captureInitial: CaptureState = { load: idle, dashboard: null, pendingAction: null };

export function captureActions(ctx: SliceContext<"capture">) {
  return {
    apply: (dashboard: CaptureDashboard) => {
      ctx.set({ dashboard, load: ready });
    },
    refresh: async () => {
      ctx.set({ load: ctx.get().dashboard ? ctx.get().load : loading });
      try {
        const { data } = await ctx.rpc.call("capture.dashboard");
        ctx.set({ dashboard: data, load: ready });
      } catch (error) {
        ctx.set({ load: failed(error) });
      }
    },
    start: async (params: MethodParams<"capture.start">) => {
      ctx.set({ pendingAction: "start" });
      try {
        await ctx.rpc.call("capture.start", params);
      } finally {
        ctx.set({ pendingAction: null });
      }
    },
    stop: async () => {
      ctx.set({ pendingAction: "stop" });
      try {
        await ctx.rpc.call("capture.stop", { waitSeconds: 0 });
      } finally {
        ctx.set({ pendingAction: null });
      }
    },
  };
}
