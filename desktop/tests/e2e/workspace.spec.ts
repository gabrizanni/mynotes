import { expect, test } from "@playwright/test";

test.use({ locale: "en-GB" });

const NOW = new Date("2026-11-26T09:42:00Z");

test("calendar: open a clash, then schedule a lesson from Add", async ({ page }) => {
  await page.clock.setFixedTime(NOW);
  await page.goto("/?lang=en&scenario=workspace&simulate=0#/calendar");
  await page.getByRole("button", { name: /^Diritto commerciale, Lesson 3, .*Time clash/ }).click();
  await expect(page.getByRole("dialog", { name: "Diritto commerciale, details" })).toContainText("Overlaps Corporate Finance");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Diritto commerciale, details" })).toBeHidden();

  await page.getByRole("button", { name: "Add" }).click();
  await page.getByRole("menuitem", { name: /Schedule lesson/ }).click();
  const dialog = page.getByRole("dialog", { name: "Schedule a lesson" });
  await dialog.getByLabel("Lesson number").fill("9");
  await dialog.getByRole("button", { name: "Schedule lesson" }).click();
  await expect(page.getByRole("status").getByText("Lesson scheduled.")).toBeVisible();

  await page.getByRole("radio", { name: "Month" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "November 2026" })).toBeVisible();
});

test("library: import dropped audio with a destination per file", async ({ page }) => {
  await page.clock.setFixedTime(NOW);
  await page.goto("/?lang=en&scenario=workspace&simulate=0#/library");
  const chooser = page.waitForEvent("filechooser");
  await page.getByRole("button", { name: "Import audio" }).click();
  await (await chooser).setFiles([
    { name: "Statistics 17 Nov.m4a", mimeType: "audio/mp4", buffer: Buffer.from("demo") },
    { name: "Statistics review session.mp3", mimeType: "audio/mpeg", buffer: Buffer.from("demo") },
  ]);
  const dialog = page.getByRole("dialog", { name: "Import audio" });
  await expect(dialog.getByRole("combobox", { name: "Destination for Statistics 17 Nov.m4a" })).toHaveValue("lesson:STAT-L05");
  await expect(dialog).toContainText("Already in your library. Skipped.");
  await dialog.getByRole("combobox", { name: "Destination for Statistics review session.mp3" }).selectOption("course:STAT");
  await dialog.getByRole("button", { name: "Import 2 recordings" }).click();
  await expect(page.getByRole("status").getByText("2 recordings imported.")).toBeVisible();
});

test("activity: pause, change parallel jobs and resume", async ({ page }) => {
  await page.clock.setFixedTime(NOW);
  await page.goto("/?lang=en&scenario=workspace&simulate=0#/activity");
  await page.getByRole("button", { name: "Pause all" }).click();
  await expect(page.getByText(/^Paused · 0 waiting, 1 finishing/)).toBeVisible();
  await page.getByRole("radio", { name: "3" }).click();
  await expect(page.getByRole("radio", { name: "3" })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "Resume all" }).click();
  await expect(page.getByText(/^Paused ·/)).toBeHidden();
});

test("settings: sections, a fix button, and a copied report without home folders", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.clock.setFixedTime(NOW);
  await page.goto("/?lang=en&scenario=workspace&simulate=0#/settings/assistant");
  await expect(page.getByRole("heading", { level: 2, name: "AI assistant" })).toBeVisible();
  await page.getByRole("link", { name: "Check setup" }).click();
  await expect(page.getByText("Word rendering isn't verified.")).toBeVisible();
  await page.getByRole("button", { name: "Copy report" }).click();
  await expect(page.getByRole("status").getByText("Report copied. Paste it where you ask for help.")).toBeVisible();
  const report = await page.evaluate(() => navigator.clipboard.readText());
  expect(report).toContain('"version"');
  expect(report).not.toMatch(/\/home\/[^~]/);
});

test("palette: Ctrl+K finds a course's notes; Ctrl+, opens settings", async ({ page }) => {
  await page.clock.setFixedTime(NOW);
  await page.goto("/?lang=en&simulate=0#/today");
  // Wait for the data, like a student would: shortcuts attach once the window is up.
  await expect(page.getByRole("heading", { level: 2, name: "Needs you" })).toBeVisible();
  await page.keyboard.press("Control+k");
  await page.getByRole("combobox", { name: "Type a command or a course" }).fill("micro");
  await expect(page.getByRole("option", { name: "Open course notes · Microeconomics" })).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("combobox")).toBeHidden();
  await page.keyboard.press("Control+,");
  await expect(page.getByRole("heading", { level: 1, name: "Settings" })).toBeVisible();
});
