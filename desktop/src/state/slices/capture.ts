/** Recorder snapshot (`capture.dashboard`, pushed by `capture.changed`). */
import type { CaptureDashboard, CaptureSettingsPatch, LessonInput, LessonPatch, MethodParams } from "../../rpc";
import { runJob } from "../../rpc/jobs";
import { failed, idle, loading, ready, type Load } from "../load";
import { attempt } from "../result";
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
        await ctx.rpc.call("capture.stop");
      } finally {
        ctx.set({ pendingAction: null });
      }
    },
    // `capture.changed` after each change; patches below only make the UI answer at once.
    addLesson: (lesson: LessonInput) => attempt(async () => (await ctx.rpc.call("capture.addLesson", { lesson })).data),
    editLesson: (event: string, patch: LessonPatch) => attempt(async () => (await ctx.rpc.call("capture.editLesson", { event, patch })).data),
    resetLesson: (event: string) => attempt(async () => (await ctx.rpc.call("capture.resetLesson", { event })).data),
    // Job lane (docs/SERVE.md): each resolves when `job.finished` carries the command's answer.
    // An answer with another exit code or status is a failure the screen reports, never "done".
    syncCalendars: (source?: string) =>
      attempt(async () => {
        const { exitCode, data } = await runJob(ctx.rpc, "capture.sync", source ? { source } : {});
        if (exitCode !== 0) throw new Error(data.message ?? data.status ?? "capture sync failed");
        return data;
      }),
    connectCalendar: (source: MethodParams<"capture.connect">["source"]) =>
      attempt(async () => {
        const { data } = await runJob(ctx.rpc, "capture.connect", { source });
        if (data.status !== "CONNECTED") throw new Error(data.code ?? data.status);
        return data;
      }),
    removeCalendar: (source: string) =>
      attempt(async () => {
        const { data } = await ctx.rpc.call("capture.unsubscribe", { source });
        const dashboard = ctx.get().dashboard;
        if (dashboard) ctx.set({ dashboard: { ...dashboard, sources: dashboard.sources.filter((item) => item.sourceID !== source) } });
        return data;
      }),
    setImportMode: (source: string, mode: "mapped" | "all_timed") =>
      attempt(async () => {
        const { data } = await ctx.rpc.call("capture.setImportMode", { source, mode });
        const dashboard = ctx.get().dashboard;
        if (dashboard) ctx.set({ dashboard: { ...dashboard, sources: dashboard.sources.map((item) => (item.sourceID === source ? { ...item, importMode: mode } : item)) } });
        return data;
      }),
    configure: (config: CaptureSettingsPatch) =>
      attempt(async () => {
        const { data } = await ctx.rpc.call("capture.configure", { config });
        const dashboard = ctx.get().dashboard;
        if (dashboard) ctx.set({ dashboard: { ...dashboard, ...config } });
        return data;
      }),
    /** Recorder on/off (`capture enable|disable`, job lane: it installs or removes an OS service). */
    setEnabled: (enabled: boolean) =>
      attempt(async () => {
        const { exitCode, data } = await runJob(ctx.rpc, enabled ? "capture.enable" : "capture.disable", undefined);
        if (exitCode !== 0) throw new Error(data.message ?? data.status ?? "capture enable failed");
        const dashboard = ctx.get().dashboard;
        if (dashboard) ctx.set({ dashboard: { ...dashboard, enabled } });
        return data;
      }),
    /** Automatic recording of scheduled lessons (`capture resume|pause`). */
    setAutomatic: (automatic: boolean) =>
      attempt(async () => {
        const { data } = await ctx.rpc.call(automatic ? "capture.resume" : "capture.pause");
        const dashboard = ctx.get().dashboard;
        if (dashboard) ctx.set({ dashboard: { ...dashboard, paused: !automatic } });
        return data;
      }),
    testMicrophone: () => attempt(async () => (await ctx.rpc.call("capture.test")).data),
    recoverAudio: () => attempt(async () => (await ctx.rpc.call("capture.recover")).data),
  };
}
