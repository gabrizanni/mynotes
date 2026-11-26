import { expect, test, type Page } from "@playwright/test";

/**
 * Course and lesson flows against the mock. `stage=1` makes each
 * simulated notes stage last about a second, so a whole job runs in a few seconds.
 */
test.use({ locale: "en-GB" });

const sheet = (page: Page) => page.getByRole("dialog", { name: /^Notes for/ });

test("Generate notes walks the stages, then offers Word and Undo", async ({ page }) => {
  await page.goto("/?lang=en&scenario=generate&stage=1#/lessons/STAT-L07");
  await page.getByRole("button", { name: "Generate notes" }).click();
  const dialog = sheet(page);
  await expect(dialog.getByText("Lecture 7 - Hypothesis testing.pdf")).toBeVisible();
  await expect(dialog.getByText("Sends the transcript and selected slides to Claude Code.")).toBeVisible();
  await dialog.getByRole("radio", { name: /Thorough/ }).click();
  await expect(dialog.getByRole("radio", { name: /Thorough/ })).toHaveAttribute("aria-checked", "true");
  await dialog.getByRole("button", { name: "Generate notes" }).click();

  const steps = dialog.getByRole("list", { name: "Notes progress" });
  await expect(steps.locator('[aria-current="step"]')).toHaveText(/Writing notes/, { timeout: 5_000 });
  await expect(dialog.getByText(/%/)).toHaveCount(0);
  await expect(dialog.getByText("Lesson 7 is in your course notes.")).toBeVisible({ timeout: 10_000 });
  await expect(dialog.getByRole("button", { name: "Open in Word" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Undo" })).toBeVisible();
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(page.getByRole("status").getByText("Lesson 7 is in your course notes.")).toBeVisible();
  await expect(page.getByRole("status").getByRole("button", { name: "Undo" })).toBeVisible();
});

test("a paused job says why, keeps progress and resumes to the end", async ({ page }) => {
  await page.goto("/?lang=en&scenario=notes-quota&stage=1#/lessons/STAT-L06");
  await page.getByRole("button", { name: "See details" }).click();
  const dialog = sheet(page);
  await expect(dialog.getByText("Claude Code's usage limit is reached. Progress is saved.")).toBeVisible();
  await dialog.getByRole("button", { name: "Resume" }).click();
  await expect(dialog.getByRole("list", { name: "Notes progress" })).toBeVisible();
  await expect(dialog.getByText("Lesson 6 is in your course notes.")).toBeVisible({ timeout: 10_000 });
});

test("Stop keeps progress and Resume finishes the same job", async ({ page }) => {
  await page.goto("/?lang=en&scenario=generate&stage=2#/lessons/STAT-L07");
  await page.getByRole("button", { name: "Generate notes" }).click();
  const dialog = sheet(page);
  await dialog.getByRole("button", { name: "Generate notes" }).click();
  await expect(dialog.getByRole("button", { name: "Stop" })).toBeEnabled();
  await dialog.getByRole("button", { name: "Stop" }).click();
  await expect(dialog.getByText("Notes stopped. Progress is saved.")).toBeVisible();
  await dialog.getByRole("button", { name: "Resume" }).click();
  await expect(dialog.getByText("Lesson 7 is in your course notes.")).toBeVisible({ timeout: 15_000 });
});

test("Keep working closes the sheet and the rail follows the job", async ({ page }) => {
  await page.goto("/?lang=en&scenario=generate&stage=3#/lessons/STAT-L07");
  await page.getByRole("button", { name: "Generate notes" }).click();
  const dialog = sheet(page);
  await dialog.getByRole("button", { name: "Generate notes" }).click();
  await dialog.getByRole("button", { name: "Keep working" }).click();
  await expect(page.getByRole("status").getByText("Notes keep going in the background.")).toBeVisible();
  await page.getByRole("button", { name: "Show progress" }).click();
  await expect(sheet(page).getByRole("list", { name: "Notes progress" })).toBeVisible();
});

test("several lessons go into one notes job from the course", async ({ page }) => {
  await page.goto("/?lang=en&scenario=course&simulate=0#/courses/STAT/lessons");
  for (const n of [4, 5, 6]) await page.getByRole("checkbox", { name: `Select lesson ${n}` }).check();
  await expect(page.getByText("3 lessons selected")).toBeVisible();
  await page.getByRole("button", { name: "Generate notes for 3 lessons" }).click();
  await expect(page.getByRole("dialog", { name: "Notes for Lessons 4–6 · Statistics" })).toBeVisible();
});

test("transcript search counts matches, steps with arrows, and timestamps seek", async ({ page }) => {
  await page.goto("/?lang=en&scenario=generate&simulate=0#/lessons/STAT-L07");
  await expect(page.getByText(/^Good morning\./)).toBeVisible();
  await page.keyboard.press("Control+f");
  const search = page.getByRole("searchbox", { name: "Search transcript" });
  await expect(search).toBeFocused();
  await search.fill("null");
  await expect(page.getByText("1 of 10")).toBeVisible();
  await search.press("ArrowDown");
  await expect(page.getByText("2 of 10")).toBeVisible();
  await search.press("ArrowUp");
  await search.press("ArrowUp");
  await expect(page.getByText("10 of 10")).toBeVisible();
  await page.getByRole("button", { name: "Play from 00:48" }).click();
  await expect(page.getByRole("slider", { name: "Position" })).toHaveValue("48");
});

test("Space plays and pauses, arrows jump 15 seconds", async ({ page }) => {
  await page.goto("/?lang=en&scenario=generate&simulate=0#/lessons/STAT-L07");
  await expect(page.getByRole("heading", { level: 1, name: "Lesson 7" })).toBeVisible();
  await page.locator("body").click({ position: { x: 600, y: 760 } });
  await page.keyboard.press("ArrowRight");
  await page.keyboard.press("ArrowRight");
  await expect(page.getByRole("slider", { name: "Position" })).toHaveValue("30");
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("slider", { name: "Position" })).toHaveValue("15");
  await page.keyboard.press(" ");
  await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
  await page.keyboard.press(" ");
  await expect(page.getByRole("button", { name: "Play", exact: true })).toBeVisible();
});

test("Undo last update asks first, then confirms", async ({ page }) => {
  await page.goto("/?lang=en&scenario=course&simulate=0#/courses/STAT/notes");
  await page.getByRole("button", { name: "Undo last update" }).click();
  const confirm = page.getByRole("alertdialog", { name: "Undo last update?" });
  await expect(confirm.getByText("Your current Word is saved as a safety copy first.")).toBeVisible();
  await confirm.getByRole("button", { name: "Undo update" }).click();
  await expect(page.getByRole("status").getByText("Update undone. A safety copy is saved.")).toBeVisible();
});

test("History lists saved copies and restores after a confirmation", async ({ page }) => {
  await page.goto("/?lang=en&scenario=course&simulate=0#/courses/STAT/notes");
  await page.getByRole("button", { name: "History" }).click();
  const drawer = page.getByRole("dialog", { name: "History · Statistics" });
  await expect(drawer.getByText("Before Lesson 6 was added")).toBeVisible();
  await drawer.getByRole("button", { name: /^Restore the copy from Tue 24 Nov/ }).click();
  await page.getByRole("alertdialog", { name: "Restore this copy?" }).getByRole("button", { name: "Restore copy" }).click();
  await expect(page.getByRole("status").getByText("Copy restored. Your previous Word is saved.")).toBeVisible();
});

test("course tabs are links with their own address", async ({ page }) => {
  await page.goto("/?lang=en&scenario=course&simulate=0#/courses");
  await page.getByRole("link", { name: /Statistics/ }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "Statistics" })).toBeVisible();
  const tabs = page.getByRole("navigation", { name: "Statistics sections" });
  await tabs.getByRole("link", { name: "Style" }).click();
  await expect(page).toHaveURL(/#\/courses\/STAT\/style$/);
  await expect(tabs.getByRole("link", { name: "Style" })).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("radiogroup", { name: "Detail" })).toBeVisible();
});

test("a conflict outcome hands over to the assistant with the message prefilled, unsent", async ({ page }) => {
  await page.goto("/?lang=en&scenario=notes-conflict#/lessons/STAT-L06");
  await page.getByRole("button", { name: "See details" }).click();
  await sheet(page).getByRole("button", { name: "Ask the assistant to merge" }).click();
  await expect(sheet(page)).toBeHidden();
  const message = page.getByRole("textbox", { name: "Message" });
  await expect(message).toHaveValue(/^My edits in the Statistics \(STAT\) course notes overlap the new notes for Lesson 6/);
  await expect(message).toBeFocused();
});
