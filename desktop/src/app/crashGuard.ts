/**
 * The window must never be blank. Errors that reach it (uncaught errors, unhandled rejections,
 * a failed boot, a render error that makes React remove its tree) are:
 *
 * - collected in `window.__mynotesErrors` (at most 20), which the real-app smoke test prints;
 * - written to the app log (`plugin:log|log`, the log plugin's own command) inside the app;
 * - shown on a plain screen, built without React, whenever `#root` is left empty.
 *
 * Event-handler errors that leave the page rendered are collected and logged only.
 */
import { en } from "../i18n/en";
import { it } from "../i18n/it";

export interface CollectedError {
  kind: "error" | "rejection" | "boot" | "render";
  message: string;
  at: string;
}

declare global {
  interface Window {
    __mynotesErrors?: CollectedError[];
  }
}

export const MAX_COLLECTED = 20;
const MAX_MESSAGE = 500;
/** `LogLevel::Error` of tauri-plugin-log. */
const LOG_ERROR = 5;

export function describeError(value: unknown): string {
  const text = value instanceof Error ? `${value.name}: ${value.message}` : typeof value === "string" ? value : JSON.stringify(value) ?? String(value);
  return text.slice(0, MAX_MESSAGE);
}

export interface CrashGuard {
  /** Record an error; show the fallback screen if `#root` is empty. */
  report(kind: CollectedError["kind"], value: unknown): void;
}

export interface CrashGuardOptions {
  /** Writes one line to the app log; the default uses the Tauri log command when present. */
  log?: (message: string) => void;
}

function tauriLog(message: string): void {
  const internals = (window as { __TAURI_INTERNALS__?: { invoke?: (cmd: string, args: unknown) => Promise<unknown> } }).__TAURI_INTERNALS__;
  void internals?.invoke?.("plugin:log|log", { level: LOG_ERROR, message, location: "webview" })?.catch(() => undefined);
}

/** The fallback screen: plain DOM, so it works when React itself is what failed. */
export function renderFallback(root: HTMLElement, errors: readonly CollectedError[], lang: string): void {
  const m = (lang.startsWith("it") ? it : en).backend.crash;
  const doc = root.ownerDocument;
  const screen = doc.createElement("main");
  screen.setAttribute("role", "alert");
  screen.setAttribute("data-crash-screen", "");
  screen.style.cssText =
    "min-height:100vh;display:grid;place-items:center;padding:48px 32px;background:var(--color-bg-canvas,#fcfbf5);color:var(--color-text-primary,#1b1a17);font:var(--text-body,16px/24px system-ui)";
  const panel = doc.createElement("div");
  panel.style.cssText = "display:grid;gap:16px;width:min(720px,100%)";
  const title = doc.createElement("h1");
  title.textContent = m.title;
  title.style.cssText = "font:var(--text-h1,500 32px/40px serif);margin:0";
  const body = doc.createElement("p");
  body.textContent = m.body;
  const details = doc.createElement("pre");
  details.textContent = errors.map((error) => `${error.at} ${error.kind}: ${error.message}`).join("\n");
  details.style.cssText =
    "margin:0;padding:16px;max-height:280px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;border:1px solid var(--color-border-hairline,#ddd);border-radius:8px;font:var(--text-mono,13px/20px monospace);user-select:text";
  const reload = doc.createElement("button");
  reload.type = "button";
  reload.textContent = m.reload;
  reload.style.cssText = "justify-self:start;padding:10px 18px;border-radius:12px;border:1px solid var(--color-border-strong,#bbb);background:var(--color-action-primary,#f0d7ff);font:inherit;cursor:pointer";
  reload.addEventListener("click", () => doc.defaultView?.location.reload());
  panel.append(title, body, details, reload);
  screen.append(panel);
  root.replaceChildren(screen);
}

export function installCrashGuard(target: Window, root: HTMLElement | null, options: CrashGuardOptions = {}): CrashGuard {
  const log = options.log ?? tauriLog;
  const errors: CollectedError[] = (target.__mynotesErrors = target.__mynotesErrors ?? []);
  const lang = target.navigator?.language ?? "en";

  const report: CrashGuard["report"] = (kind, value) => {
    const entry: CollectedError = { kind, message: describeError(value), at: new Date().toISOString() };
    if (errors.length >= MAX_COLLECTED) errors.shift();
    errors.push(entry);
    log(`[Webview] ${kind} — ${entry.message}`);
    const blank = root && (root.childElementCount === 0 || kind === "boot" || kind === "render");
    if (root && blank) renderFallback(root, errors, lang);
  };

  target.addEventListener("error", (event: ErrorEvent) => report("error", event.error ?? event.message));
  target.addEventListener("unhandledrejection", (event: PromiseRejectionEvent) => report("rejection", event.reason));
  return { report };
}
