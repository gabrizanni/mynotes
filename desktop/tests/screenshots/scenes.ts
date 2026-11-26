/**
 * Screenshot scenes. Each scene renders once per language and theme, saved as
 * tests/shots/<name>-<lang>-<theme>.png. To add a screen: add a scene here (route hash,
 * fixture scenario, fixed clock) and its fixture patches in tests/fixtures/rpc/.
 */
export interface Scene {
  name: string;
  /** Path before the hash: "/" or "/__gallery". */
  path: string;
  hash: string;
  scenario: string;
  /** Fixed clock (UTC); fixtures describe Thursday 26 November 2026 in Europe/Rome. */
  time: string;
  fullPage?: boolean;
}

export const SCENES: Scene[] = [
  { name: "gallery", path: "/__gallery", hash: "", scenario: "default", time: "2026-11-26T09:42:00Z", fullPage: true },
  { name: "today", path: "/", hash: "#/today", scenario: "default", time: "2026-11-26T09:42:00Z" },
  { name: "today-recording", path: "/", hash: "#/today", scenario: "recording", time: "2026-11-26T08:47:00Z" },
  { name: "today-error", path: "/", hash: "#/today", scenario: "error", time: "2026-11-26T07:51:00Z" },
  { name: "today-approval", path: "/", hash: "#/today", scenario: "approval", time: "2026-11-26T09:42:00Z" },
  { name: "today-generate", path: "/", hash: "#/today", scenario: "generate", time: "2026-11-26T09:42:00Z" },
  { name: "today-empty", path: "/", hash: "#/today", scenario: "empty", time: "2026-11-26T09:42:00Z" },
];

export const LANGS = [
  { lang: "en", locale: "en-GB" },
  { lang: "it", locale: "it-IT" },
] as const;

export const THEMES = ["light", "dark"] as const;

// ===== BEGIN feat/desktop-lessons: course and lesson scenes (run by lessons.shots.ts) =========
import type { Messages } from "../../src/i18n/en";

/** One interaction before or between screenshots. Labels come from the catalog, so EN and IT share steps. */
export type SceneStep =
  | { click: (t: Messages) => string; role?: "button" | "link" | "checkbox" | "menuitem" | "radio"; inDialog?: boolean }
  | { fill: (t: Messages) => string; value: string }
  | { press: string }
  /** Advance the fake clock (flow scenes only): timers, job stages and elapsed times move. */
  | { advance: number }
  | { shot: string };

export interface FlowScene {
  /** Used when the steps take no `shot`: one screenshot at the end. */
  name: string;
  hash: string;
  scenario: string;
  time: string;
  /** Run the mock's timers on a fake clock (`?simulate=1&stage=`), for live notes jobs. */
  live?: boolean;
  steps?: SceneStep[];
}

const T0 = "2026-11-26T09:42:00Z";
const details: SceneStep = { click: (t) => t.lesson.rail.seeDetails };
const outcome = (name: string, scenario: string, lesson = "STAT-L06", extra: SceneStep[] = []): FlowScene => ({
  name,
  hash: `#/lessons/${lesson}`,
  scenario,
  time: T0,
  steps: [details, ...extra, { shot: name }],
});

export const LESSON_SCENES: FlowScene[] = [
  { name: "courses", hash: "#/courses", scenario: "course", time: T0 },
  { name: "courses-lessons", hash: "#/courses/STAT/lessons", scenario: "course", time: T0 },
  {
    name: "courses-lessons-selected",
    hash: "#/courses/STAT/lessons",
    scenario: "course",
    time: T0,
    steps: [
      { click: (t) => t.courses.lessons.selectRow(4), role: "checkbox" },
      { click: (t) => t.courses.lessons.selectRow(5), role: "checkbox" },
      { click: (t) => t.courses.lessons.selectRow(6), role: "checkbox" },
      { shot: "courses-lessons-selected" },
      { click: (t) => t.courses.lessons.generateFor(3) },
      { shot: "courses-generate-lessons" },
    ],
  },
  { name: "courses-notes", hash: "#/courses/STAT/notes", scenario: "course", time: T0 },
  { name: "courses-notes-history", hash: "#/courses/STAT/notes", scenario: "course", time: T0, steps: [{ click: (t) => t.courses.history }, { shot: "courses-notes-history" }] },
  { name: "courses-notes-undo", hash: "#/courses/STAT/notes", scenario: "course", time: T0, steps: [{ click: (t) => t.courses.undoLast }, { shot: "courses-notes-undo" }] },
  { name: "courses-style", hash: "#/courses/STAT/style", scenario: "course", time: T0 },
  { name: "courses-profile", hash: "#/courses/STAT/profile", scenario: "course", time: T0 },
  { name: "courses-files", hash: "#/courses/STAT/files", scenario: "course", time: T0 },
  { name: "courses-files-preview", hash: "#/courses/STAT/files", scenario: "course", time: T0, steps: [{ click: (t) => t.courses.files.chooseFolder }, { shot: "courses-files-preview" }] },

  { name: "lesson", hash: "#/lessons/STAT-L07", scenario: "generate", time: T0 },
  { name: "lesson-transcribing", hash: "#/lessons/STAT-L07", scenario: "default", time: T0 },
  { name: "lesson-no-audio", hash: "#/lessons/MICRO-L03", scenario: "course", time: T0 },
  { name: "lesson-sources", hash: "#/lessons/CORPFIN-L02", scenario: "course", time: T0 },
  {
    name: "lesson-search",
    hash: "#/lessons/STAT-L07",
    scenario: "generate",
    time: T0,
    steps: [{ press: "Control+f" }, { fill: (t) => t.lesson.search.label, value: "null" }, { press: "Enter" }, { shot: "lesson-search" }],
  },
  { name: "lesson-menu", hash: "#/lessons/STAT-L07", scenario: "generate", time: T0, steps: [{ click: (t) => t.lesson.menu.label }, { shot: "lesson-menu" }] },
  {
    name: "lesson-generate",
    hash: "#/lessons/STAT-L07",
    scenario: "generate",
    time: T0,
    steps: [{ click: (t) => t.lesson.generateNotes }, { shot: "lesson-generate" }, { click: (t) => t.lesson.generate.more, inDialog: true }, { shot: "lesson-generate-more" }],
  },
  {
    name: "lesson-stage",
    hash: "#/lessons/STAT-L07",
    scenario: "generate",
    time: T0,
    live: true,
    steps: [
      { click: (t) => t.lesson.generateNotes },
      { advance: 1_000 },
      { click: (t) => t.lesson.generate.start, inDialog: true },
      { advance: 30_000 },
      { shot: "lesson-stage-checking" },
      { advance: 218_000 },
      { shot: "lesson-stage-writing" },
      { advance: 234_000 },
      { shot: "lesson-stage-formatting" },
      { advance: 66_000 },
      { shot: "lesson-stage-updating" },
      { advance: 22_000 },
      { shot: "lesson-outcome-completed" },
    ],
  },
  outcome("lesson-outcome-warnings", "notes-warnings", "STAT-L06", [{ click: (t) => t.lesson.outcome.viewList, inDialog: true }]),
  outcome("lesson-outcome-transcript", "notes-transcript", "DIRCOM-L02"),
  outcome("lesson-outcome-quota", "notes-quota"),
  outcome("lesson-outcome-auth", "notes-auth"),
  outcome("lesson-outcome-network", "notes-network"),
  outcome("lesson-outcome-sleep", "notes-sleep"),
  outcome("lesson-outcome-conflict", "notes-conflict"),
  outcome("lesson-outcome-review", "notes-review"),
  outcome("lesson-outcome-stopped", "notes-stopped"),
  {
    name: "lesson-outcome-approval",
    hash: "#/lessons/STAT-L06",
    scenario: "approval",
    time: T0,
    steps: [{ click: (t) => t.lesson.rail.reviewNotes }, { shot: "lesson-outcome-approval" }, { click: (t) => t.lesson.outcome.openPreview, inDialog: true }, { shot: "lesson-outcome-approve" }],
  },
];
// ===== END feat/desktop-lessons ===============================================================
