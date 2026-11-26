/**
 * Onboarding screenshot scenes. Each scene opens one step with a
 * fixture scenario, a seeded onboarding.json and, when needed, a few clicks. File names:
 * tests/shots/onboarding-<step>-<state>-<lang>-<theme>.png.
 */
import type { Page } from "@playwright/test";

import {
  DEFAULT_STYLE,
  EMPTY_RECORD,
  applySaved,
  defaultCourse,
  markStep,
  pendingOps,
  type OnboardingRecord,
  type StepId,
} from "../../src/features/onboarding/model";
import { en } from "../../src/i18n/en";
import { it } from "../../src/i18n/it";
import { WELCOME_STEPS } from "../../src/state/routes";

export type ShotLang = "en" | "it";

export interface OnboardingScene {
  /** `<step>-<state>` */
  name: string;
  step: StepId;
  scenario: string;
  time: string;
  seed?: (lang: ShotLang) => OnboardingRecord;
  act?: (page: Page, lang: ShotLang) => Promise<void>;
}

const TIME = "2026-11-26T09:42:00Z";
const strings = { en, it } as const;
const labels = { goalLabel: (goal: string) => goal, goalsPrefix: "" };

function course(lang: ShotLang) {
  return {
    ...defaultCourse(lang),
    name: "Statistics",
    kind: "quantitative" as const,
    examFormat: lang === "it" ? "Scritto con esercizi" : "Written exam with exercises",
    hardTopics: lang === "it" ? ["Test d'ipotesi", "Regressione"] : ["Hypothesis tests", "Regression"],
  };
}

/** Every step before `step` done, answers filled in, and (by default) already saved. */
export function through(step: StepId, lang: ShotLang, patch: Partial<OnboardingRecord> = {}, saved = true): OnboardingRecord {
  let record: OnboardingRecord = { ...EMPTY_RECORD, language: lang, provider: "claude", course: course(lang), style: DEFAULT_STYLE, ...patch };
  for (const id of WELCOME_STEPS) {
    if (id === step) break;
    record = markStep(record, id, "done");
  }
  if (saved) for (const op of pendingOps(record, labels)) record = applySaved(record, op, "STAT");
  return record;
}

const early = (step: StepId) => (lang: ShotLang) => through(step, lang, { course: null, style: null });
const openSetup = async (page: Page) => {
  await page.locator("header button").first().click();
  await page.getByRole("dialog").waitFor();
};

export const ONBOARDING_SCENES: OnboardingScene[] = [
  { name: "language-default", step: "language", scenario: "first-run", time: TIME },
  { name: "assistant-default", step: "assistant", scenario: "first-run", time: TIME, seed: early("assistant") },
  {
    name: "assistant-setup-running",
    step: "assistant",
    scenario: "setup-running",
    time: TIME,
    seed: (lang) => ({ ...early("assistant")(lang), provider: "claude" }),
    act: openSetup,
  },
  { name: "assistant-setup-waiting", step: "assistant", scenario: "setup-waiting", time: TIME, seed: (lang) => ({ ...early("assistant")(lang), provider: "claude" }), act: openSetup },
  { name: "assistant-setup-failed", step: "assistant", scenario: "setup-failed", time: TIME, seed: (lang) => ({ ...early("assistant")(lang), provider: "claude" }), act: openSetup },
  { name: "sign-in-login", step: "sign-in", scenario: "signin-login", time: TIME, seed: early("sign-in") },
  { name: "sign-in-missing", step: "sign-in", scenario: "signin-missing", time: TIME, seed: early("sign-in") },
  { name: "sign-in-incompatible", step: "sign-in", scenario: "signin-incompatible", time: TIME, seed: early("sign-in") },
  { name: "sign-in-unverified", step: "sign-in", scenario: "signin-unverified", time: TIME, seed: (lang) => ({ ...early("sign-in")(lang), provider: "agy" }) },
  { name: "sign-in-ready", step: "sign-in", scenario: "default", time: TIME, seed: early("sign-in") },
  { name: "privacy-default", step: "privacy", scenario: "setup-running", time: TIME, seed: early("privacy") },
  { name: "studies-default", step: "studies", scenario: "setup-running", time: TIME, seed: early("studies") },
  {
    name: "studies-filled",
    step: "studies",
    scenario: "setup-running",
    time: TIME,
    seed: (lang) => ({
      ...early("studies")(lang),
      studies: { background: lang === "it" ? "Economia, secondo anno. Lavoro part-time." : "Economics, second year. I work part-time.", goals: ["exam", "understanding"] },
    }),
  },
  { name: "course-default", step: "course", scenario: "setup-running", time: TIME, seed: early("course") },
  {
    name: "course-error",
    step: "course",
    scenario: "setup-running",
    time: TIME,
    seed: early("course"),
    act: async (page, lang) => {
      await page.getByRole("button", { name: strings[lang].common.continue, exact: true }).click();
      await page.getByRole("alert").waitFor();
    },
  },
  { name: "course-filled", step: "course", scenario: "setup-running", time: TIME, seed: (lang) => through("course", lang, { style: null }, false) },
  { name: "style-default", step: "style", scenario: "setup-running", time: TIME, seed: (lang) => through("style", lang, {}, false) },
  {
    name: "style-lists",
    step: "style",
    scenario: "default",
    time: TIME,
    seed: (lang) => through("style", lang, { style: { ...DEFAULT_STYLE, detail: "detailed", layout: "lists", images: "explained", recapLanguage: "it", questions: 3 } }, false),
  },
  { name: "files-wait", step: "files", scenario: "setup-running", time: TIME, seed: (lang) => through("files", lang, {}, false) },
  { name: "files-empty", step: "files", scenario: "default", time: TIME, seed: (lang) => through("files", lang) },
  {
    name: "files-preview",
    step: "files",
    scenario: "default",
    time: TIME,
    seed: (lang) => through("files", lang, { folder: { path: "~/Documents/University/Statistics", reviewFirst: true } }),
    act: async (page) => {
      await page.getByRole("region").filter({ has: page.getByRole("heading", { level: 2 }) }).first().waitFor();
    },
  },
  {
    name: "files-selection",
    step: "files",
    scenario: "files-selection",
    time: TIME,
    seed: (lang) => through("files", lang, { folder: { path: "~/Documents/University/Statistics", reviewFirst: false } }),
    act: async (page) => {
      await page.getByRole("radiogroup").waitFor();
    },
  },
  { name: "schedule-default", step: "schedule", scenario: "default", time: TIME, seed: (lang) => through("schedule", lang) },
  {
    name: "schedule-google",
    step: "schedule",
    scenario: "default",
    time: TIME,
    seed: (lang) => through("schedule", lang),
    act: async (page, lang) => {
      await page.getByRole("radio", { name: strings[lang].onboarding.schedule.tabs.google }).click();
    },
  },
  {
    name: "schedule-error",
    step: "schedule",
    scenario: "schedule-error",
    time: TIME,
    seed: (lang) => through("schedule", lang),
    act: async (page, lang) => {
      await page.locator("#calendar-url").fill("https://calendar.google.com/calendar/embed?src=classes");
      await page.getByRole("button", { name: strings[lang].onboarding.schedule.preview, exact: true }).click();
      await page.getByRole("alert").waitFor();
    },
  },
  {
    name: "schedule-preview",
    step: "schedule",
    scenario: "default",
    time: TIME,
    seed: (lang) => through("schedule", lang),
    act: async (page, lang) => {
      await page.locator("#calendar-url").fill("https://timetable.example.org/statistics.ics");
      await page.getByRole("button", { name: strings[lang].onboarding.schedule.preview, exact: true }).click();
      await page.getByRole("button", { name: strings[lang].onboarding.schedule.looksRight }).waitFor();
    },
  },
  { name: "microphone-intro", step: "microphone", scenario: "default", time: TIME, seed: (lang) => through("microphone", lang) },
  { name: "microphone-recording", step: "microphone", scenario: "mic-recording", time: TIME, seed: (lang) => through("microphone", lang, { micTestAt: Date.parse("2026-11-26T09:41:40Z") }) },
  {
    name: "microphone-playback",
    step: "microphone",
    scenario: "mic-recorded",
    time: TIME,
    seed: (lang) => through("microphone", lang, { micTestAt: Date.parse("2026-11-26T09:40:20Z") }),
    act: async (page, lang) => {
      await page.getByRole("button", { name: strings[lang].onboarding.microphone.play }).click();
    },
  },
  { name: "microphone-denied", step: "microphone", scenario: "mic-denied", time: TIME, seed: (lang) => through("microphone", lang) },
  { name: "automatic-default", step: "automatic", scenario: "default", time: TIME, seed: (lang) => through("automatic", lang) },
  {
    name: "automatic-shortcut",
    step: "automatic",
    scenario: "default",
    time: TIME,
    seed: (lang) => through("automatic", lang, { calendar: { url: "https://timetable.example.org/statistics.ics", sourceID: "timetable" } }),
    act: async (page) => {
      await page.keyboard.press("Alt+Shift+KeyR");
    },
  },
  { name: "first-notes-default", step: "first-notes", scenario: "default", time: TIME, seed: (lang) => through("first-notes", lang) },
  {
    name: "first-notes-file",
    step: "first-notes",
    scenario: "default",
    time: TIME,
    seed: (lang) => through("first-notes", lang),
    act: async (page, lang) => {
      await page.getByRole("radio", { name: new RegExp(strings[lang].onboarding.firstNotes.have.title) }).click();
      await page.locator("input[type=file]").setInputFiles({ name: "Statistics lesson 1.m4a", mimeType: "audio/mp4", buffer: Buffer.from("mock") });
    },
  },
  {
    name: "ready-default",
    step: "ready",
    scenario: "default",
    time: TIME,
    seed: (lang) => {
      let record = through("ready", lang, { calendar: { url: "https://timetable.example.org/statistics.ics", sourceID: "timetable" } });
      for (const step of ["studies", "automatic", "first-notes"] as const) record = markStep(record, step, "skipped");
      return record;
    },
  },
];
