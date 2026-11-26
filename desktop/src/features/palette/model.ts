/**
 * Command palette (⌘/Ctrl K, UX-SPEC §3 Keyboard): the command list for the current state and
 * a forgiving filter. Pure; unit-tested in model.test.ts.
 */
import type { Messages } from "../../i18n";
import { routeForSection, SECTIONS, SETTINGS_SECTIONS, type Route } from "../../state/routes";
import { normalize } from "../library/model";

export type PaletteAction =
  | { kind: "navigate"; route: Route }
  | { kind: "record" }
  | { kind: "stop" }
  | { kind: "import" }
  | { kind: "theme"; theme: "light" | "dark" }
  | { kind: "pickUndo" }
  | { kind: "undo"; course: string; name: string };

export type PaletteGroup = "actions" | "navigate" | "courses" | "undo";

export interface Command {
  id: string;
  group: PaletteGroup;
  label: string;
  /** Extra words that find the command ("notes", "word", the course code…). */
  keywords: string;
  /** Shortcut hint, e.g. "Ctrl+1". */
  shortcut?: string;
  action: PaletteAction;
}

export interface PaletteInput {
  t: Messages;
  courses: ReadonlyArray<{ code: string; name: string }>;
  recording: boolean;
  resolvedTheme: "light" | "dark";
  shortcut: (key: string) => string;
  /** `undo`: second step, pick the course whose last update to undo. */
  mode: "all" | "undo";
}

export function buildCommands({ t, courses, recording, resolvedTheme, shortcut, mode }: PaletteInput): Command[] {
  if (mode === "undo") {
    return courses.map((course) => ({
      id: `undo:${course.code}`,
      group: "undo",
      label: t.palette.undoFor(course.name),
      keywords: course.code,
      action: { kind: "undo", course: course.code, name: course.name },
    }));
  }
  const sectionLabels: Record<(typeof SECTIONS)[number], string> = {
    today: t.nav.today,
    calendar: t.nav.calendar,
    courses: t.nav.courses,
    library: t.nav.library,
    activity: t.nav.activity,
  };
  const actions: Command[] = [
    recording
      ? { id: "stop", group: "actions", label: t.palette.stopRecording, keywords: "capture recorder", action: { kind: "stop" } }
      : { id: "record", group: "actions", label: t.palette.recordNow, keywords: "capture recorder microphone", shortcut: "Alt+Shift+R", action: { kind: "record" } },
    { id: "import", group: "actions", label: t.palette.importAudio, keywords: "audio file library", action: { kind: "import" } },
    { id: "undo", group: "actions", label: t.palette.undoLastUpdate, keywords: "word notes restore", action: { kind: "pickUndo" } },
    {
      id: "theme",
      group: "actions",
      label: resolvedTheme === "dark" ? t.palette.themeLight : t.palette.themeDark,
      keywords: "theme dark light appearance",
      action: { kind: "theme", theme: resolvedTheme === "dark" ? "light" : "dark" },
    },
  ];
  const navigate: Command[] = [
    ...SECTIONS.map((section, index) => ({
      id: `go:${section}`,
      group: "navigate" as const,
      label: t.palette.goTo(sectionLabels[section]),
      keywords: section,
      shortcut: shortcut(String(index + 1)),
      action: { kind: "navigate" as const, route: routeForSection(section) },
    })),
    ...SETTINGS_SECTIONS.map((section): Command => ({
      id: `settings:${section}`,
      group: "navigate",
      label: t.palette.settingsSection(t.settings.sections[section]),
      keywords: `settings preferences ${section}`,
      shortcut: section === "assistant" ? shortcut(",") : undefined,
      action: { kind: "navigate", route: { name: "settings", section } },
    })),
  ];
  const notes: Command[] = courses.map((course) => ({
    id: `notes:${course.code}`,
    group: "courses",
    label: t.palette.openCourseNotes(course.name),
    keywords: `${course.code} word notes`,
    action: { kind: "navigate", route: { name: "course", code: course.code, tab: "notes" } },
  }));
  return [...actions, ...navigate, ...notes];
}

/**
 * Every typed word must appear in the label or keywords (accents ignored). Labels that start
 * with the query come first, then words that start with it, then the rest in list order.
 */
export function filterCommands(commands: readonly Command[], query: string): Command[] {
  const words = normalize(query).split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...commands];
  const scored: Array<{ command: Command; score: number; index: number }> = [];
  commands.forEach((command, index) => {
    const label = normalize(command.label);
    const haystack = `${label} ${normalize(command.keywords)}`;
    if (!words.every((word) => haystack.includes(word))) return;
    const first = words[0] ?? "";
    const score = label.startsWith(first) ? 0 : label.split(/[\s·]+/).some((part) => part.startsWith(first)) ? 1 : 2;
    scored.push({ command, score, index });
  });
  return scored.sort((a, b) => a.score - b.score || a.index - b.index).map((entry) => entry.command);
}
