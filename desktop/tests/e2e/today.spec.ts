import { expect, test } from "@playwright/test";

test.use({ locale: "en-GB" });

test("Record now turns the hero into a live recording, Stop saves it", async ({ page }) => {
  await page.clock.setFixedTime(new Date("2026-11-26T09:42:00Z"));
  await page.goto("/?lang=en&scenario=default#/today");
  const hero = page.getByRole("region", { name: "Microeconomics" });
  await hero.getByRole("button", { name: "Record now" }).click();
  await expect(page.getByRole("button", { name: "Stop recording" })).toBeVisible();
  await page.getByRole("button", { name: "Stop recording" }).click();
  await expect(page.getByRole("status").getByText("Saving audio…")).toBeVisible();
});

test("keyboard shortcuts move between sections and open the assistant", async ({ page }) => {
  await page.goto("/?lang=en&simulate=0#/today");
  await expect(page.getByRole("heading", { level: 1, name: "Today" })).toBeVisible();
  await page.keyboard.press("Control+5");
  await expect(page.getByRole("heading", { level: 1, name: "Activity" })).toBeVisible();
  await page.keyboard.press("Control+j");
  await expect(page.getByRole("complementary", { name: "AI assistant" })).toBeVisible();
});
