/**
 * "Copy report" for Check setup: doctor checks plus versions, with home folders and calendar
 * links removed so the report can be pasted anywhere (UX-SPEC §3 Settings). Pure; tested.
 */
import type { CaptureDashboard, DoctorReport } from "../../rpc";

/**
 * Home folders, on every platform: `/home/<name>`, any `/<Capitalized>/<name>` root (macOS user
 * folders, mounted volumes) and `C:\<Capitalized>\<name>`. Over-redacting a path is harmless;
 * leaking a name is not.
 */
const HOME_PATHS: ReadonlyArray<[RegExp, string]> = [
  [/(^|[^\w~])\/home\/[^/\s"'`]+/g, "$1~"],
  [/(^|[^\w~])\/[A-Z][A-Za-z]+\/[^/\s"'`]+/g, "$1~"],
  [/\b[A-Za-z]:\\[A-Z][A-Za-z]+\\[^\\\s"'`]+/g, "~"],
];

/** Keep the scheme and host, drop the path and query (private calendar tokens live there). */
const URLS = /\b(https?|webcal):\/\/([^/\s"'`?#]+)[^\s"'`]*/gi;

export function redactText(text: string): string {
  let out = text.replace(URLS, (_match, scheme: string, host: string) => `${scheme}://${host}/…`);
  for (const [pattern, replacement] of HOME_PATHS) out = out.replace(pattern, replacement);
  return out;
}

/** Deep copy with every string redacted (keys stay as they are). */
export function redact<T>(value: T): T {
  if (typeof value === "string") return redactText(value) as T;
  if (Array.isArray(value)) return value.map((item: unknown) => redact(item)) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) out[key] = redact(item);
    return out as T;
  }
  return value;
}

export interface ReportInput {
  doctor: DoctorReport | null;
  dashboard: CaptureDashboard | null;
  appVersion: string;
  platform: string;
  now: Date;
}

export function buildReport({ doctor, dashboard, appVersion, platform, now }: ReportInput): string {
  const report = {
    generatedAt: now.toISOString(),
    app: { name: "MyNotes Desktop", version: appVersion, platform },
    doctor,
    recorder: dashboard
      ? {
          enabled: dashboard.enabled,
          paused: dashboard.paused,
          running: dashboard.running,
          microphone: dashboard.runtime.microphonePermission ?? null,
          calendars: dashboard.sources.map((source) => ({
            label: source.label,
            host: source.host,
            timezone: source.timezone,
            lastSuccess: source.lastSuccess ?? null,
            code: source.code ?? null,
            error: source.error ?? null,
          })),
        }
      : null,
  };
  return JSON.stringify(redact(report), null, 2);
}

/** Order checks the way a student reads them; unknown checks go last. */
export const CHECK_ORDER = ["installation", "agent", "transcription", "word", "capture"];

export function sortedChecks(doctor: DoctorReport | null): Array<[string, DoctorReport["checks"][string]]> {
  const entries = Object.entries(doctor?.checks ?? {});
  const rank = (name: string) => {
    const index = CHECK_ORDER.indexOf(name);
    return index === -1 ? CHECK_ORDER.length : index;
  };
  return entries.sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}
