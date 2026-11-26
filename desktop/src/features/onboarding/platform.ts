/**
 * Window-side helpers the onboarding needs beyond RPC. Inside Tauri they call Rust commands;
 * in the browser mock they stand in with harmless defaults so every flow stays clickable.
 *
 * Rust side (src-tauri): `open_system_settings` (commands.rs, panes per OS in external.rs) and
 * `audio_source`, which allows one checked recording in the asset protocol for test playback.
 */
import { isTauriRuntime } from "../../rpc/transports/tauri";

export type SettingsPane = "microphone" | "privacy" | "sound";

/** Folder picker: the Tauri dialog plugin in the app, a sample path in the mock. */
export async function pickFolder(title: string, fallbackName: string): Promise<string | null> {
  if (!isTauriRuntime()) return `~/Documents/University/${fallbackName || "Course"}`;
  try {
    const { open } = await import("@tauri-apps/plugin-dialog");
    const chosen = await open({ directory: true, multiple: false, title });
    return typeof chosen === "string" ? chosen : null;
  } catch {
    return null;
  }
}

/** Path of a dropped file: Tauri gives real paths through its drag-drop event; the mock uses the name. */
export function droppedPath(file: File): string {
  const withPath = file as File & { path?: unknown };
  return typeof withPath.path === "string" && withPath.path ? withPath.path : `~/Downloads/${file.name}`;
}

export async function openPath(path: string): Promise<boolean> {
  if (!isTauriRuntime()) return false;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("open_path", { path });
    return true;
  } catch {
    return false;
  }
}

/** Opens the OS pane (macOS Privacy › Microphone, Windows Settings, GNOME/KDE sound). */
export async function openSystemSettings(pane: SettingsPane): Promise<boolean> {
  if (!isTauriRuntime()) return false;
  try {
    const { openSystemSettings: open } = await import("../../rpc/native");
    await open(pane);
    return true;
  } catch {
    return false;
  }
}

/** A URL the webview can play for a local recording (Tauri asset protocol), or null in the mock. */
export async function playableUrl(path: string): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  try {
    const native = await import("../../rpc/native");
    return await native.playableUrl(path);
  } catch {
    return null; // Refused by Rust (outside the recording folders) or not playable.
  }
}

export type OsFamily = "mac" | "windows" | "linux";

export function osFamily(): OsFamily {
  if (typeof navigator === "undefined") return "linux";
  const source = `${navigator.platform} ${navigator.userAgent}`;
  if (/Mac|iPhone|iPad/.test(source)) return "mac";
  if (/Win/.test(source)) return "windows";
  return "linux";
}

/** Official install guides; the browser opens them, never the app's webview. */
export const GUIDES = {
  claude: "https://docs.claude.com/en/docs/claude-code/setup",
  codex: "https://github.com/openai/codex",
  agy: "https://antigravity.google",
} as const;
