/**
 * Course and lesson screenshots (feat/desktop-lessons): LESSON_SCENES from scenes.ts, each in
 * EN/IT × light/dark → tests/shots/<name>-<lang>-<theme>.png. Static scenes freeze the mock;
 * `live` scenes run its timers on Playwright's fake clock so a notes job really walks its stages.
 */
import { expect, test, type Page } from "@playwright/test";

import { en } from "../../src/i18n/en";
import { it as italian } from "../../src/i18n/it";
import { LANGS, LESSON_SCENES, THEMES, type FlowScene, type SceneStep } from "./scenes";

const catalogs = { en, it: italian } as const;
const STAGE_SECONDS = 160;

async function settle(page: Page, live: boolean) {
  if (live) await page.clock.runFor(400);
  await page.waitForTimeout(150);
}

async function runStep(page: Page, step: SceneStep, scene: FlowScene, lang: "en" | "it", theme: string) {
  const t = catalogs[lang];
  if ("shot" in step) {
    await page.evaluate(() => document.fonts.ready.then(() => undefined));
    await page.waitForTimeout(250);
    await page.screenshot({ path: `tests/shots/${step.shot}-${lang}-${theme}.png` });
    return;
  }
  if ("advance" in step) {
    // Small slices so React renders between timer bursts, as a real clock would.
    for (let left = step.advance; left > 0; left -= 5_000) await page.clock.runFor(Math.min(5_000, left));
    await settle(page, true);
    return;
  }
  if ("press" in step) {
    await page.keyboard.press(step.press);
    await settle(page, scene.live === true);
    return;
  }
  if ("fill" in step) {
    await page.getByRole("searchbox", { name: step.fill(t) }).fill(step.value);
    await settle(page, scene.live === true);
    return;
  }
  const scope = step.inDialog ? page.locator("dialog[open]").last() : page;
  const target = scope.getByRole(step.role ?? "button", { name: step.click(t), exact: true }).first();
  await target.click();
  await settle(page, scene.live === true);
}

for (const { lang, locale } of LANGS) {
  test.describe(`${lang}`, () => {
    test.use({ locale });
    for (const theme of THEMES) {
      for (const scene of LESSON_SCENES) {
        test(`${scene.name}-${lang}-${theme}`, async ({ page }) => {
          const errors: string[] = [];
          page.on("pageerror", (error) => errors.push(error.message));
          page.on("console", (message) => {
            if (message.type() === "error") errors.push(message.text());
          });
          await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
          if (scene.live) await page.clock.install({ time: new Date(scene.time) });
          else await page.clock.setFixedTime(new Date(scene.time));
          const mock = scene.live ? `simulate=1&stage=${STAGE_SECONDS}` : "simulate=0";
          await page.goto(`/?lang=${lang}&scenario=${scene.scenario}&${mock}${scene.hash}`);
          if (scene.live) for (let i = 0; i < 6; i += 1) await page.clock.runFor(250);
          await expect(page.locator("h1").first()).toBeVisible();
          await settle(page, scene.live === true);
          const steps = scene.steps ?? [{ shot: scene.name }];
          for (const step of steps) await runStep(page, step, scene, lang, theme);
          expect(errors, "console and page errors").toEqual([]);
        });
      }
    }
  });
}
