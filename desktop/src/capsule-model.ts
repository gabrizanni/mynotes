/**
 * Recording capsule view-model (window `capsule`, see src-tauri/src/capsule.rs).
 *
 * The capsule reads the capture dashboard from two wire shapes: the `capture.dashboard` result
 * (`{exitCode, data}`) and the `capture.changed` notification (a watch Snapshot,
 * `{topic, hash, data}`). Both unwrap to the dashboard, checked with the app's schema.
 */
import type { CapsuleState } from "./components/RecordingCapsule";
import { captureDashboardSchema, type CaptureDashboard } from "./rpc/schema";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The dashboard inside a snapshot or `{exitCode, data}` envelope, or `null`. */
export function dashboardFrom(value: unknown): CaptureDashboard | null {
  let inner = value;
  if (isRecord(inner) && "topic" in inner && "hash" in inner) inner = inner.data;
  if (isRecord(inner) && "exitCode" in inner && "data" in inner) inner = inner.data;
  const parsed = captureDashboardSchema.safeParse(inner);
  return parsed.success ? parsed.data : null;
}

/** `m:ss`, or `h:mm:ss` from one hour. */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

/** What the capsule shows; `null` when nothing records (Rust hides the window then). */
export function capsuleState(dashboard: CaptureDashboard | null, now: Date): CapsuleState | null {
  const runtime = dashboard?.runtime;
  if (!runtime) return null;
  if (runtime.recording) {
    const session = runtime.currentSession ?? null;
    const startedAt = session?.actualStart ? Date.parse(session.actualStart) : Number.NaN;
    const elapsed = Number.isNaN(startedAt) ? "0:00" : formatElapsed((now.getTime() - startedAt) / 1000);
    const event = session?.event;
    const fallback = event?.courseCode ? `${event.courseCode}${event.lessonNumber ? ` · ${event.lessonNumber}` : ""}` : "";
    return { kind: "recording", elapsed, title: event?.title ?? runtime.title ?? fallback };
  }
  if (runtime.exporting) return { kind: "saving" };
  return null;
}
