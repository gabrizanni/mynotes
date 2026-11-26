/** Loading state shared by every slice that mirrors a backend snapshot. */
export type LoadStatus = "idle" | "loading" | "ready" | "error";

export interface Load {
  readonly status: LoadStatus;
  /** Human-safe message; codes and raw details stay in logs and "Copy details". */
  readonly error: string | null;
}

export const idle: Load = { status: "idle", error: null };
export const loading: Load = { status: "loading", error: null };
export const ready: Load = { status: "ready", error: null };
export const failed = (error: unknown): Load => ({
  status: "error",
  error: error instanceof Error ? error.message : String(error),
});
