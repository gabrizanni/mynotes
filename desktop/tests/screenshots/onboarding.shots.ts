import { expect, test } from "@playwright/test";

import { STORAGE_KEY } from "../../src/features/onboarding/storage";
import { LANGS, ONBOARDING_SCENES, THEMES } from "./scenes";

for (const { lang, locale } of LANGS) {
  test.describe(`onboarding ${lang}`, () => {
    test.use({ locale });
    for (const theme of THEMES) {
      for (const scene of ONBOARDING_SCENES) {
        const name = `onboarding-${scene.name}-${lang}-${theme}`;
        test(name, async ({ page }) => {
          const errors: string[] = [];
          page.on("pageerror", (error) => errors.push(error.message));
          page.on("console", (message) => {
            if (message.type() === "error") errors.push(message.text());
          });
          await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
          await page.clock.setFixedTime(new Date(scene.time));
          const seed = scene.seed?.(lang) ?? null;
          await page.addInitScript(
            ([key, value]) => {
              if (value) window.localStorage.setItem(key, value);
              else window.localStorage.removeItem(key);
            },
            [STORAGE_KEY, seed ? JSON.stringify(seed) : ""] as const,
          );
          await page.goto(`/?lang=${lang}&scenario=${scene.scenario}&simulate=0#/welcome/${scene.step}`);
          await expect(page.locator("h1").first()).toBeVisible();
          await expect(page).toHaveURL(new RegExp(`#/welcome/${scene.step}$`));
          if (scene.act) await scene.act(page, lang);
          await page.evaluate(() => document.fonts.ready.then(() => undefined));
          await page.waitForTimeout(400);
          await page.screenshot({ path: `tests/shots/${name}.png` });
          expect(errors, "console and page errors").toEqual([]);
        });
      }
    }
  });
}
