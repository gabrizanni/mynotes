import type { RpcClient } from "./client";
import type { NotificationParams } from "./schema";

export type JobFinished = NotificationParams<"job.finished">;

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
