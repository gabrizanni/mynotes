/**
 * Theme switch: follow the OS by default, with a manual override stored per device.
 *
 * `system` removes `data-theme` so tokens.css follows `prefers-color-scheme`; `light` and
 * `dark` pin the theme through `<html data-theme>`.
 */
export type ThemePreference = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "mynotes.theme";

export function isThemePreference(value: unknown): value is ThemePreference {
  return value === "system" || value === "light" || value === "dark";
}

export function readStoredTheme(storage: Pick<Storage, "getItem"> | undefined): ThemePreference {
  try {
    const value = storage?.getItem(THEME_STORAGE_KEY);
    return isThemePreference(value) ? value : "system";
  } catch {
    return "system";
  }
}

export function storeTheme(storage: Pick<Storage, "setItem"> | undefined, preference: ThemePreference): void {
  try {
    storage?.setItem(THEME_STORAGE_KEY, preference);
  } catch {
    // Private mode or blocked storage: the choice simply lasts for this session.
  }
}

export function applyTheme(root: HTMLElement, preference: ThemePreference): void {
  if (preference === "system") {
    delete root.dataset.theme;
  } else {
    root.dataset.theme = preference;
  }
}

export function resolveTheme(preference: ThemePreference, prefersDark: boolean): ResolvedTheme {
  if (preference === "system") return prefersDark ? "dark" : "light";
  return preference;
}

/** Subscribe to OS theme changes; returns an unsubscribe function. */
export function watchSystemTheme(onChange: (prefersDark: boolean) => void): () => void {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => undefined;
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  const listener = (event: MediaQueryListEvent) => onChange(event.matches);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

export function systemPrefersDark(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? window.matchMedia("(prefers-color-scheme: dark)").matches
    : false;
}
