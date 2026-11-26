/** Modifier naming: ⌘ on macOS, Ctrl elsewhere (shortcut hints and handlers). */
export function isMac(): boolean {
  if (typeof navigator === "undefined") return false;
  return /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
}

export function shortcutLabel(key: string): string {
  return isMac() ? `⌘${key}` : `Ctrl+${key}`;
}
