/**
 * Screenshot scenes. Each scene renders once per language and theme, saved as
 * tests/shots/<name>-<lang>-<theme>.png. To add a screen: add a scene here (route hash,
 * fixture scenario, fixed clock) and its fixture patches in tests/fixtures/rpc/.
 */
import type { Page } from "@playwright/test";

export interface Scene {
  name: string;
  /** Path before the hash: "/" or "/__gallery". */
  path: string;
  hash: string;
  scenario: string;
  /** Fixed clock (UTC); fixtures describe Thursday 26 November 2026 in Europe/Rome. */
  time: string;
  fullPage?: boolean;
  /** Interaction before the shot (open a dialog, a popover, the palette). */
  act?: (page: Page) => Promise<void>;
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

// >>> onboarding (feat/desktop-onboarding) >>>
// Rendered by onboarding.shots.ts as tests/shots/onboarding-<step>-<state>-<lang>-<theme>.png.
// `seed` writes the app's onboarding.json (local storage) before load; `act` drives the page.
export { ONBOARDING_SCENES, type OnboardingScene } from "./onboarding.scenes";
// <<< onboarding (feat/desktop-onboarding) <<<
// BEGIN workspace screens (Calendar, Library, Activity, Settings, palette)
const SETTINGS_SECTIONS = ["assistant", "transcription", "recording", "calendars", "general", "profile", "diagnostics"] as const;

export const WORKSPACE_SCENES: Scene[] = [
  { name: "calendar-week", path: "/", hash: "#/calendar", scenario: "workspace", time: "2026-11-26T09:42:00Z" },
  {
    name: "calendar-popover",
    path: "/",
    hash: "#/calendar",
    scenario: "workspace",
    time: "2026-11-26T09:42:00Z",
    act: async (page) => {
      await page.getByRole("button", { name: /^Research methods seminar/ }).click();
      await page.getByRole("dialog").first().waitFor();
    },
  },
  { name: "calendar-month", path: "/", hash: "#/calendar?view=month", scenario: "workspace", time: "2026-11-26T09:42:00Z" },
  { name: "library", path: "/", hash: "#/library", scenario: "workspace", time: "2026-11-26T09:42:00Z" },
  {
    name: "library-detail",
    path: "/",
    hash: "#/library",
    scenario: "workspace",
    time: "2026-11-26T09:42:00Z",
    act: async (page) => {
      await page.getByRole("button", { name: /Recording 19 Nov 14:02/ }).first().click();
    },
  },
  {
    name: "library-import",
    path: "/",
    hash: "#/library",
    scenario: "workspace",
    time: "2026-11-26T09:42:00Z",
    act: async (page) => {
      const chooser = page.waitForEvent("filechooser");
      await page.getByRole("button", { name: /^(Import audio|Importa audio)$/ }).click();
      const files = ["Statistics 17 Nov.m4a", "Statistics review session.mp3", "STAT-L06 copy.m4a"];
      await (await chooser).setFiles(files.map((name) => ({ name, mimeType: "audio/mp4", buffer: Buffer.from("demo") })));
      await page.getByRole("dialog").getByRole("combobox").first().waitFor();
    },
  },
  { name: "activity", path: "/", hash: "#/activity", scenario: "workspace", time: "2026-11-26T09:42:00Z" },
  { name: "activity-paused", path: "/", hash: "#/activity", scenario: "paused", time: "2026-11-26T09:42:00Z" },
  ...SETTINGS_SECTIONS.map((section) => ({
    name: `settings-${section}`,
    path: "/",
    hash: `#/settings/${section}`,
    scenario: "workspace",
    time: "2026-11-26T09:42:00Z",
  })),
  {
    name: "palette",
    path: "/",
    hash: "#/today",
    scenario: "default",
    time: "2026-11-26T09:42:00Z",
    act: async (page) => {
      await page.keyboard.press("Control+k");
      await page.getByRole("dialog").waitFor();
      await page.keyboard.type("stat");
    },
  },
];

SCENES.push(...WORKSPACE_SCENES);
// END workspace screens
