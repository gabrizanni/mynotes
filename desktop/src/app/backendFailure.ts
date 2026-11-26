/**
 * When the window shows the backend-failure screen instead of the app, and what "Copy details"
 * copies. A `failed` status with `NOT_INSTALLED` belongs to setup (onboarding), not here.
 */
import type { BackendStatus } from "../rpc/native";

export type FailedStatus = Extract<BackendStatus, { state: "failed" }>;

/** Shown by setup instead: the runtime is simply not installed yet. */
const SETUP_CODES = new Set(["NOT_INSTALLED"]);
/** Lines of the backend's stderr shown and copied (the supervisor keeps its last lines). */
export const MAX_DETAIL_LINES = 40;

export function shownFailure(status: BackendStatus | null): FailedStatus | null {
  if (!status || status.state !== "failed" || SETUP_CODES.has(status.code)) return null;
  return status;
}

export function detailLines(status: FailedStatus): string[] {
  return status.stderrTail.slice(-MAX_DETAIL_LINES);
}

/** Plain text for whoever helps: code, message, crash count, then the last stderr lines. */
export function failureDetails(status: FailedStatus, context: { appVersion: string; platform: string }): string {
  const lines = detailLines(status);
  return [
    `MyNotes Desktop ${context.appVersion} (${context.platform})`,
    `Backend: ${status.code}, ${status.crashes} stop(s)`,
    status.message,
    "",
    ...(lines.length ? lines : ["(no stderr lines)"]),
  ].join("\n");
}
