/**
 * Follow a notes job after the sheet closes: the toast that ends it (with Undo for a finished
 * update) appears wherever the student is, because the subscription lives on the RPC client,
 * not on a mounted screen.
 */
import type { Messages } from "../../../i18n";
import type { RpcClient } from "../../../rpc";
import { finishedFields, jobState } from "../../../rpc/jobs";
import type { AppActions } from "../../../state";
import { outcomeOf } from "./model";

export interface WatchContext {
  rpc: RpcClient;
  actions: AppActions;
  t: Messages;
  course: string;
  /** "Lesson 7" / "Lessons 5–7". */
  lessons: string;
  count: number;
  /** Lesson page to open from the toast. */
  lectureId: string;
  assistant: string;
  /** True while the sheet shows this job, so the toast would repeat it. */
  isShown: () => boolean;
}

export function undoLastUpdate(ctx: Pick<WatchContext, "rpc" | "actions" | "t" | "course">): void {
  const { rpc, actions, t, course } = ctx;
  rpc.call("notes.undo", { course }).then(
    () => actions.ui.toast({ message: t.lesson.outcome.undone, tone: "neutral" }),
    () => actions.ui.toast({ message: t.lesson.outcome.undoError, tone: "danger" }),
  );
}

export function completedToast(ctx: Pick<WatchContext, "rpc" | "actions" | "t" | "course" | "lessons" | "count">): void {
  ctx.actions.ui.toast({
    message: ctx.t.lesson.outcome.completed(ctx.lessons, ctx.count),
    tone: "success",
    action: { label: ctx.t.lesson.outcome.undo, run: () => undoLastUpdate(ctx) },
  });
}

export function watchNotesJob(jobId: string, ctx: WatchContext): () => void {
  const off = ctx.rpc.on("job.finished", (event) => {
    if (event.jobId !== jobId) return;
    off();
    const outcome = outcomeOf({ jobId, kind: "notes", startedAt: "", ...finishedFields(event, new Date().toISOString()) });
    if (!outcome) return;
    const open = () => ctx.actions.ui.navigate({ name: "lesson", id: ctx.lectureId });
    const o = ctx.t.lesson.outcome;
    if (outcome.kind === "completed" || outcome.kind === "warnings") {
      completedToast(ctx);
      return;
    }
    if (ctx.isShown()) return;
    const messages: Record<string, string> = {
      needsTranscript: o.needsTranscript(ctx.lessons, ctx.count),
      pausedQuota: o.pausedQuota(ctx.assistant),
      pausedAuth: o.pausedAuth(ctx.assistant),
      pausedNetwork: o.pausedNetwork,
      pausedSleep: o.pausedSleep,
      awaitingApproval: o.awaitingApproval,
      conflict: o.conflict(ctx.lessons, ctx.count),
      needsReview: o.needsReview,
      cancelled: o.cancelled,
    };
    ctx.actions.ui.toast({
      message: messages[outcome.kind] ?? o.needsReview,
      tone: outcome.tone === "danger" ? "danger" : "warning",
      action: { label: ctx.t.lesson.rail.seeDetails, run: open },
    });
  });
  return off;
}

/** "Generate when ready": run `start` once every transcription job succeeded. */
export function afterTranscriptions(rpc: RpcClient, jobIds: readonly string[], start: () => void, onFail: () => void): () => void {
  const pending = new Set(jobIds);
  const off = rpc.on("job.finished", (event) => {
    if (!pending.has(event.jobId)) return;
    if (jobState(event.state) !== "succeeded") {
      off();
      onFail();
      return;
    }
    pending.delete(event.jobId);
    if (pending.size === 0) {
      off();
      start();
    }
  });
  return off;
}
