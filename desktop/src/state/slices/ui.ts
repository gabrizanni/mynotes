/** Window-level UI: route, language, theme, panels and toasts. */
import type { ThemePreference } from "../../design/theme";
import type { Lang } from "../../i18n/lang";
import { DEFAULT_ROUTE, type Route } from "../routes";
import type { SliceContext } from "../types";

export type ToastTone = "neutral" | "success" | "warning" | "danger";

export interface Toast {
  readonly id: string;
  readonly message: string;
  readonly tone: ToastTone;
  readonly action?: { readonly label: string; readonly run: () => void };
}

export interface UiState {
  readonly route: Route;
  readonly lang: Lang;
  readonly theme: ThemePreference;
  readonly assistantOpen: boolean;
  readonly toasts: readonly Toast[];
}

export const uiInitial: UiState = { route: DEFAULT_ROUTE, lang: "en", theme: "system", assistantOpen: false, toasts: [] };

let toastCounter = 0;

export function uiActions(ctx: SliceContext<"ui">) {
  return {
    /** Update the route in state only; `app/useHashRoute` mirrors it into the URL. */
    navigate: (route: Route) => {
      ctx.set({ route });
    },
    setLang: (lang: Lang) => {
      ctx.set({ lang });
    },
    setTheme: (theme: ThemePreference) => {
      ctx.set({ theme });
    },
    setAssistantOpen: (assistantOpen: boolean) => {
      ctx.set({ assistantOpen });
    },
    toggleAssistant: () => {
      ctx.set((state) => ({ assistantOpen: !state.assistantOpen }));
    },
    /** Toasts stay 6 s (UX-SPEC §4); returns the id so callers can dismiss early. */
    toast: (toast: Omit<Toast, "id">): string => {
      toastCounter += 1;
      const id = `toast-${toastCounter}`;
      ctx.set((state) => ({ toasts: [...state.toasts.slice(-2), { ...toast, id }] }));
      return id;
    },
    dismissToast: (id: string) => {
      ctx.set((state) => ({ toasts: state.toasts.filter((toast) => toast.id !== id) }));
    },
  };
}
