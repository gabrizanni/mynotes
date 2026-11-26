/**
 * Desktop bridges used by the course and lesson screens: open or reveal a file (Rust checks the
 * path first, paths.rs), pick a folder, copy text. Outside Tauri (browser, tests) the mock has no
 * files to open, so these report what they would do and never throw.
 */
import { isTauriRuntime } from "../../rpc/transports/tauri";

export type BridgeResult = "done" | "preview";

async function invokeCommand(command: "open_path" | "reveal_path", path: string): Promise<BridgeResult> {
  if (!isTauriRuntime()) return "preview";
  const { invoke } = await import("@tauri-apps/api/core");
  await invoke(command, { path });
  return "done";
}

/** Opens a file with its default app (Word for .docx). Rejects when the path is refused. */
export function openPath(path: string): Promise<BridgeResult> {
  return invokeCommand("open_path", path);
}

/** Shows the file in Finder / Explorer / the file manager. */
export function revealPath(path: string): Promise<BridgeResult> {
  return invokeCommand("reveal_path", path);
}

/** A folder chosen in the native dialog; `null` when cancelled or outside the app. */
export async function pickFolder(title: string): Promise<string | null> {
  if (!isTauriRuntime()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const chosen = await open({ directory: true, multiple: false, title });
  return typeof chosen === "string" ? chosen : null;
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/** The provider is always named when data leaves the computer (UX-SPEC §5). */
export function assistantName(provider: string | null | undefined): string {
  switch (provider) {
    case "codex":
      return "Codex";
    case "agy":
    case "antigravity":
      return "Antigravity";
    default:
      return "Claude Code";
  }
}
