/**
 * Assistant panel scenes, saved as tests/shots/assistant-<state>-<lang>-<theme>.png.
 * The panel opens with Ctrl+J over Today; the saved conversation comes from the
 * `assistant.get@assistant-<state>[-it].json` fixtures (frozen mock, no timers).
 */
import { expect, test, type Page } from "@playwright/test";

import { LANGS, THEMES } from "./scenes";

interface AssistantScene {
  name: string;
  /** Fixture scenario; Italian shots use `<scenario>-it` when it has a conversation. */
  scenario: string | null;
  /** Tool rows to show expanded (`?assistantTools=`). */
  openTools?: string[];
  ready: (page: Page, lang: string) => Promise<void>;
}

const TIME = "2026-11-26T09:42:00Z";
const panel = (page: Page) => page.getByRole("complementary").filter({ has: page.getByRole("heading", { level: 2 }) }).last();

const SCENES: AssistantScene[] = [
  {
    name: "empty",
    scenario: null,
    ready: async (page) => {
      await expect(panel(page).getByRole("list").getByRole("button").first()).toBeVisible();
    },
  },
  {
    name: "streaming",
    scenario: "assistant-streaming",
    ready: async (page, lang) => {
      await expect(panel(page).getByText(lang === "it" ? "Al lavoro…" : "Working…")).toBeVisible();
    },
  },
  {
    name: "finished-tool-expanded",
    scenario: "assistant-finished",
    openTools: ["toolu_fixture_1"],
    ready: async (page) => {
      await expect(panel(page).getByText("mynotes notes STAT --lessons 3 --json")).toBeVisible();
    },
  },
  {
    name: "error",
    scenario: "assistant-error",
    ready: async (page) => {
      await expect(panel(page).getByRole("alert")).toBeVisible();
    },
  },
];

for (const { lang, locale } of LANGS) {
  test.describe(`assistant ${lang}`, () => {
    test.use({ locale });
    for (const theme of THEMES) {
      for (const scene of SCENES) {
        const name = `assistant-${scene.name}-${lang}-${theme}`;
        test(name, async ({ page }) => {
          const errors: string[] = [];
          page.on("pageerror", (error) => errors.push(error.message));
          page.on("console", (message) => {
            if (message.type() === "error") errors.push(message.text());
          });
          await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
          await page.clock.setFixedTime(new Date(TIME));
          const scenario = scene.scenario ? `${scene.scenario}${lang === "it" ? "-it" : ""}` : "default";
          const tools = scene.openTools ? `&assistantTools=${scene.openTools.join(",")}` : "";
          await page.goto(`/?lang=${lang}&scenario=${scenario}&simulate=0${tools}#/today`);
          await expect(page.locator("h1").first()).toBeVisible();
          await page.keyboard.press("Control+j");
          await scene.ready(page, lang);
          await page.evaluate(() => document.fonts.ready.then(() => undefined));
          await page.waitForTimeout(400);
          await page.screenshot({ path: `tests/shots/${name}.png` });
          expect(errors, "console and page errors").toEqual([]);
        });
      }
    }
  });
}
