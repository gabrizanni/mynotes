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

// >>> onboarding (feat/desktop-onboarding) >>>
// Rendered by onboarding.shots.ts as tests/shots/onboarding-<step>-<state>-<lang>-<theme>.png.
// `seed` writes the app's onboarding.json (local storage) before load; `act` drives the page.
export { ONBOARDING_SCENES, type OnboardingScene } from "./onboarding.scenes";
// <<< onboarding (feat/desktop-onboarding) <<<
