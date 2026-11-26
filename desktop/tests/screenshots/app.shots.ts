import { expect, test } from "@playwright/test";

import { LANGS, SCENES, THEMES } from "./scenes";

for (const { lang, locale } of LANGS) {
  test.describe(`${lang}`, () => {
    test.use({ locale });
    for (const theme of THEMES) {
      for (const scene of SCENES) {
        test(`${scene.name}-${lang}-${theme}`, async ({ page }) => {
          const errors: string[] = [];
          page.on("pageerror", (error) => errors.push(error.message));
          page.on("console", (message) => {
            if (message.type() === "error") errors.push(message.text());
          });
          await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
          await page.clock.setFixedTime(new Date(scene.time));
          await page.goto(`${scene.path}?lang=${lang}&scenario=${scene.scenario}&simulate=0${scene.hash}`);
          await expect(page.locator("h1").first()).toBeVisible();
          if (scene.name.startsWith("today")) await expect(page.locator("section[aria-labelledby]").first()).toBeVisible();
          if (scene.act) await scene.act(page); // dialogs, popovers, palette
          await page.evaluate(() => document.fonts.ready.then(() => undefined));
          await page.waitForTimeout(400);
          await page.screenshot({ path: `tests/shots/${scene.name}-${lang}-${theme}.png`, fullPage: scene.fullPage ?? false });
          expect(errors, "console and page errors").toEqual([]);
        });
      }
    }
  });
}
