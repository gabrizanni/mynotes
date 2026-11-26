/**
 * Shell commands and events that are not `mynotes serve` methods (`src-tauri/src/commands.rs`,
 * `events.rs`, `actions.rs`). Every payload is zod-checked; unknown fields pass through.
 *
 * - `backend://status` / `getBackendStatus()`: the supervisor's lifecycle. `failed` carries
 *   `code` (`NOT_INSTALLED` → show setup, `PROTOCOL_MISMATCH`, `CRASH_LOOP`, `MISCONFIGURED`),
 *   `message` and `stderrTail` for the failure screen; `restartBackend()` is its "Try again".
 * - `app://action` / `takePendingAction()`: `record-dialog` from the tray, the global shortcut
 *   or a second launch (`mynotes-desktop --action record-dialog`, the Wayland key bind).
 * - `openPath()` / `revealPath()`: Rust checks the path (local, inside MyNotes folders, a
 *   document or media type) before the OS opens it; the webview has no opener permission.
 * - `openExternal()` / `openSystemSettings()`: an install guide in the browser (https, known
 *   hosts only) and the OS microphone or sound settings, both checked and opened by Rust.
 * - `playableUrl()`: Rust checks a recording (an audio file in a recording folder the backend
 *   reports) and adds only that file to the asset protocol scope, which starts empty.
 */
import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { z } from "zod";

import { toRpcError } from "./errors";

export const BACKEND_STATUS_EVENT = "backend://status";
export const APP_ACTION_EVENT = "app://action";

export const backendStatusSchema = z.discriminatedUnion("state", [
  z.looseObject({ state: z.literal("idle") }),
  z.looseObject({ state: z.literal("starting"), attempt: z.number().int() }),
  z.looseObject({
    state: z.literal("ready"),
    pid: z.number().int(),
    serverPid: z.number().int().nullable(),
    protocol: z.number().int(),
    version: z.string().nullable(),
    generation: z.number().int(),
  }),
  z.looseObject({ state: z.literal("restarting"), attempt: z.number().int(), delayMs: z.number(), reason: z.string() }),
  z.looseObject({
    state: z.literal("failed"),
    code: z.string(),
    message: z.string(),
    crashes: z.number().int(),
    stderrTail: z.array(z.string()),
  }),
  z.looseObject({ state: z.literal("stopped") }),
]);

export type BackendStatus = z.output<typeof backendStatusSchema>;

export const appActionSchema = z.looseObject({ action: z.enum(["record-dialog", "show"]) });
export type AppAction = z.output<typeof appActionSchema>["action"];

async function command<T>(name: string, schema: z.ZodType<T>, args?: Record<string, unknown>): Promise<T> {
  try {
    return schema.parse(await invoke(name, args));
  } catch (error) {
    throw toRpcError(name, error);
  }
}

function listenParsed<T>(event: string, schema: z.ZodType<T>, listener: (value: T) => void): () => void {
  let active = true;
  const pending = listen<unknown>(event, (message) => {
    const parsed = schema.safeParse(message.payload);
    if (active && parsed.success) listener(parsed.data);
  });
  return () => {
    active = false;
    pending.then((unlisten) => unlisten()).catch(() => undefined);
  };
}

export const getBackendStatus = () => command("backend_status", backendStatusSchema);
export const restartBackend = () => command("backend_restart", backendStatusSchema);
export const onBackendStatus = (listener: (status: BackendStatus) => void) =>
  listenParsed(BACKEND_STATUS_EVENT, backendStatusSchema, listener);

export const takePendingAction = () => command("take_pending_action", appActionSchema.shape.action.nullable());
export const onAppAction = (listener: (action: AppAction) => void) =>
  listenParsed(APP_ACTION_EVENT, appActionSchema, (payload) => listener(payload.action));

export const openPath = (path: string) => command("open_path", z.null(), { path });
export const revealPath = (path: string) => command("reveal_path", z.null(), { path });

/** The checked, canonical path of a recording the webview may play (`audio_source`). */
export const audioSource = (path: string) => command("audio_source", z.string(), { path });

/** An asset-protocol URL for a local recording, once Rust has allowed that one file. */
export async function playableUrl(path: string): Promise<string> {
  return convertFileSrc(await audioSource(path));
}

export const openExternal = (url: string) => command("open_external", z.null(), { url });
export const openSystemSettings = (pane: "microphone" | "privacy" | "sound") =>
  command("open_system_settings", z.null(), { pane });
