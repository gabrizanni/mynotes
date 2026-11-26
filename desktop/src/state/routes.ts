/**
 * Routes are a discriminated union, encoded in the URL hash (`#/courses/STAT/notes`) so deep
 * links survive reloads inside Tauri's custom protocol. No router library.
 *
 * To add a route: add a variant here, handle it in `parseRoute` and `routeToHash`, then render
 * it in `app/Routes.tsx`. The switch there is exhaustive, so `tsc` points at every gap.
 */

export const COURSE_TABS = ["lessons", "notes", "style", "profile", "files"] as const;
export type CourseTab = (typeof COURSE_TABS)[number];

export const SETTINGS_SECTIONS = ["assistant", "transcription", "recording", "calendars", "general", "profile", "diagnostics"] as const;
export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];

/** Onboarding steps, named by what they do (UX-SPEC §2), never numbered in the UI. */
export const WELCOME_STEPS = [
  "language",
  "assistant",
  "sign-in",
  "privacy",
  "studies",
  "course",
  "style",
  "files",
  "schedule",
  "microphone",
  "automatic",
  "first-notes",
  "ready",
] as const;
export type WelcomeStep = (typeof WELCOME_STEPS)[number];

export type CalendarView = "week" | "month";

export type Route =
  | { name: "today" }
  | { name: "calendar"; view: CalendarView; date: string | null }
  | { name: "courses" }
  | { name: "course"; code: string; tab: CourseTab }
  | { name: "library"; course: string | null; q: string; unassigned: boolean }
  | { name: "activity" }
  | { name: "lesson"; id: string }
  | { name: "settings"; section: SettingsSection }
  | { name: "welcome"; step: WelcomeStep }
  | { name: "gallery" };

export type RouteName = Route["name"];

/** Sidebar sections (⌘/Ctrl 1–5). */
export const SECTIONS = ["today", "calendar", "courses", "library", "activity"] as const;
export type Section = (typeof SECTIONS)[number];

function oneOf<T extends string>(values: readonly T[], value: string | undefined, fallback: T): T {
  return values.find((candidate) => candidate === value) ?? fallback;
}

export const DEFAULT_ROUTE: Route = { name: "today" };

export function parseRoute(hash: string, pathname = "/"): Route {
  if (pathname.replace(/\/+$/, "") === "/__gallery") return { name: "gallery" };
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?");
  const parts = path.split("/").filter(Boolean).map(decodeURIComponent);
  const params = new URLSearchParams(query);
  const [head, second, third] = parts;
  switch (head) {
    case undefined:
    case "today":
      return { name: "today" };
    case "calendar":
      return { name: "calendar", view: params.get("view") === "month" ? "month" : "week", date: params.get("d") };
    case "courses":
      return second ? { name: "course", code: second, tab: oneOf(COURSE_TABS, third, "lessons") } : { name: "courses" };
    case "library":
      return { name: "library", course: params.get("course"), q: params.get("q") ?? "", unassigned: params.get("unassigned") === "1" };
    case "activity":
      return { name: "activity" };
    case "lessons":
      return second ? { name: "lesson", id: second } : DEFAULT_ROUTE;
    case "settings":
      return { name: "settings", section: oneOf(SETTINGS_SECTIONS, second, "assistant") };
    case "welcome":
      return { name: "welcome", step: oneOf(WELCOME_STEPS, second, "language") };
    case "__gallery":
      return { name: "gallery" };
    default:
      return DEFAULT_ROUTE;
  }
}

function withQuery(path: string, entries: Array<[string, string | null | false]>): string {
  const params = new URLSearchParams();
  for (const [key, value] of entries) if (value) params.set(key, value);
  const query = params.toString();
  return query ? `${path}?${query}` : path;
}

export function routeToHash(route: Route): string {
  switch (route.name) {
    case "today":
      return "#/today";
    case "calendar":
      return `#${withQuery("/calendar", [["view", route.view === "month" ? "month" : null], ["d", route.date]])}`;
    case "courses":
      return "#/courses";
    case "course":
      return `#/courses/${encodeURIComponent(route.code)}/${route.tab}`;
    case "library":
      return `#${withQuery("/library", [["course", route.course], ["q", route.q || null], ["unassigned", route.unassigned && "1"]])}`;
    case "activity":
      return "#/activity";
    case "lesson":
      return `#/lessons/${encodeURIComponent(route.id)}`;
    case "settings":
      return `#/settings/${route.section}`;
    case "welcome":
      return `#/welcome/${route.step}`;
    case "gallery":
      return "#/__gallery";
  }
}

/** Which sidebar item lights up for a route. */
export function sectionOf(route: Route): Section | null {
  switch (route.name) {
    case "today":
    case "calendar":
    case "courses":
    case "library":
    case "activity":
      return route.name;
    case "course":
      return "courses";
    case "lesson":
      return "library";
    case "settings":
    case "welcome":
    case "gallery":
      return null;
  }
}

export function routeForSection(section: Section): Route {
  switch (section) {
    case "today":
      return { name: "today" };
    case "calendar":
      return { name: "calendar", view: "week", date: null };
    case "courses":
      return { name: "courses" };
    case "library":
      return { name: "library", course: null, q: "", unassigned: false };
    case "activity":
      return { name: "activity" };
  }
}
