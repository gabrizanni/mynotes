import { isMac } from "../../app/platform";
import { isTauriRuntime } from "../../rpc/transports/tauri";

/** Windows-only setup rows (speech model download and check, UX-SPEC §3 Transcription). */
export function isWindows(): boolean {
  return typeof navigator !== "undefined" && /Windows/i.test(navigator.userAgent);
}

/** The global Record now shortcut as the OS writes it (UX-SPEC §3 Keyboard). */
export function recordShortcut(): string {
  return isMac() ? "⌥⇧R" : "Alt+Shift+R";
}

/** Folder actions exist only inside the app window: Rust validates the path (`paths.rs`). */
export function canReveal(): boolean {
  return isTauriRuntime();
}

export async function revealPath(path: string): Promise<boolean> {
  if (!isTauriRuntime()) return false;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    await invoke("reveal_path", { path });
    return true;
  } catch {
    return false;
  }
}
