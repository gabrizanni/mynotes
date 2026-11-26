import { expect, test, type Page } from "@playwright/test";

import { markStep, type OnboardingRecord } from "../../src/features/onboarding/model";
import { STORAGE_KEY } from "../../src/features/onboarding/storage";
import { through } from "../screenshots/onboarding.scenes";

test.use({ locale: "en-GB" });

async function seed(page: Page, record: OnboardingRecord | null) {
  await page.addInitScript(
    ([key, value]) => {
      if (value) window.localStorage.setItem(key, value);
      else window.localStorage.removeItem(key);
    },
    [STORAGE_KEY, record ? JSON.stringify(record) : ""] as const,
  );
}

const heading = (page: Page, name: string) => page.getByRole("heading", { level: 1, name });

test("first launch walks all 13 steps, drafts answers and saves them once setup finishes", async ({ page }) => {
  test.setTimeout(60_000);
  await page.clock.install({ time: new Date("2026-11-26T09:00:00Z") });
  await seed(page, null);
  await page.goto("/?lang=en&scenario=first-run#/today");

  // Launch rule: an unfinished install opens the first step.
  await expect(heading(page, "Your lectures, turned into study notes.")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Setup progress" })).toContainText("Your course");
  await page.getByRole("button", { name: "Get started" }).click();

  await expect(heading(page, "Choose your AI assistant")).toBeVisible();
  await page.getByRole("radio", { name: /Claude Code/ }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("button", { name: /Setting up/ })).toBeVisible();

  await expect(heading(page, "Sign in to Claude Code")).toBeVisible();
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.clock.fastForward(5000);
  await expect(page.getByText("You're signed in to Claude Code.")).toBeVisible();
  await page.clock.fastForward(1000);

  await expect(heading(page, "What stays on this computer")).toBeVisible();
  await page.getByRole("button", { name: "I understand" }).click();

  await expect(heading(page, "What do you study?")).toBeVisible();
  await page.getByRole("button", { name: "Skip", exact: true }).click();

  await expect(heading(page, "Add your first course")).toBeVisible();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("Add a name to continue.");
  await page.getByLabel("Course name").fill("Statistics");
  await page.getByLabel("Course name").press("Enter");

  await expect(heading(page, "How should your notes read?")).toBeVisible();
  await page.getByRole("radio", { name: "Lists" }).click();
  await expect(page.getByRole("figure", { name: "Live preview" })).toContainText("Standard error");
  await page.getByRole("button", { name: "Use this style" }).click();

  // Faster than the download: the folder step waits, with a live checklist.
  await expect(heading(page, "Almost there")).toBeVisible();
  await expect(page.getByText("Save Statistics")).toBeVisible();
  await page.clock.fastForward(120_000);

  await expect(heading(page, "Where are your course files?")).toBeVisible();
  await expect(page.getByRole("button", { name: /Setup done/ })).toBeVisible();
  const saved = await page.evaluate((key) => JSON.parse(window.localStorage.getItem(key) ?? "{}") as OnboardingRecord, STORAGE_KEY);
  expect(saved.courseCode).toBeTruthy();
  expect(Object.keys(saved.saved).sort()).toEqual(["course", "language", "profile"]);

  await page.getByRole("button", { name: "Choose folder" }).click();
  await expect(page.getByText("14 slides and PDFs")).toBeVisible();
  await page.getByRole("switch", { name: "Review before updating Word" }).click();
  await page.getByRole("button", { name: "Connect", exact: true }).click();

  await expect(heading(page, "Add your class schedule")).toBeVisible();
  await page.getByRole("button", { name: "Skip for now" }).click();

  await expect(heading(page, "Let MyNotes hear your lectures")).toBeVisible();
  await page.getByRole("button", { name: "Test microphone" }).click();
  await expect(page.getByRole("timer")).toBeVisible();
  await page.clock.fastForward(61_000);
  await page.clock.fastForward(2_000);
  await expect(page.getByText("Do you hear yourself clearly?")).toBeVisible();
  await page.getByRole("button", { name: "Yes, I hear it" }).click();
  await expect(page.getByText("Play the test before you confirm.")).toBeVisible();
  await page.getByRole("button", { name: "Play test" }).click();
  await page.getByRole("button", { name: "Yes, I hear it" }).click();

  await expect(heading(page, "Record scheduled lessons automatically?")).toBeVisible();
  await page.keyboard.press("Alt+Shift+KeyR");
  await expect(page.getByText("Shortcut works.")).toBeVisible();
  await page.getByRole("button", { name: "Not now" }).click();

  await expect(heading(page, "Make your first notes")).toBeVisible();
  await page.getByRole("button", { name: "Finish" }).click();

  await expect(heading(page, "You're ready.")).toBeVisible();
  const summary = page.getByRole("region", { name: "Summary" });
  await expect(summary.getByRole("listitem").filter({ hasText: "Course folder" })).toContainText("Done");
  await expect(summary.getByRole("listitem").filter({ hasText: "Class schedule" })).toContainText("Finish later");
  await page.getByRole("button", { name: "Open MyNotes" }).click();
  await expect(heading(page, "Today")).toBeVisible();
});

test("launch resumes at the first incomplete required step", async ({ page }) => {
  let record = through("course", "en", { course: null, style: null });
  record = markStep(record, "sign-in", "skipped");
  await seed(page, record);
  await page.goto("/?lang=en&scenario=first-run&simulate=0#/today");
  await expect(heading(page, "Add your first course")).toBeVisible();
  await expect(page).toHaveURL(/#\/welcome\/course$/);
});

/** Product of the opacities of an element and its ancestors: what the eye actually gets. */
const visibleOpacity = (element: Element) => {
  let value = 1;
  for (let node: Element | null = element; node; node = node.parentElement) value *= Number(getComputedStyle(node).opacity);
  return value;
};

test("the first step is readable even when CSS animations never advance", async ({ page }) => {
  // WebKitGTK under Xvfb (the real-app smoke) can leave animations at their first frame.
  await page.addInitScript(() => {
    const style = document.createElement("style");
    style.textContent = "*, *::before, *::after { animation-play-state: paused !important; }";
    document.documentElement.appendChild(style);
  });
  await seed(page, null);
  await page.goto("/?lang=en&scenario=first-run#/today");
  const title = heading(page, "Your lectures, turned into study notes.");
  await expect(title).toBeAttached();
  expect(await title.evaluate(visibleOpacity)).toBe(1);
  expect(await page.evaluate(() => document.body.innerText.trim().length)).toBeGreaterThan(40);
});

test("an installed MyNotes opens Today, not onboarding", async ({ page }) => {
  await seed(page, null);
  await page.goto("/?lang=en&simulate=0#/today");
  await expect(heading(page, "Today")).toBeVisible();
});

test("Enter runs the primary action and Esc goes back", async ({ page }) => {
  await seed(page, null);
  await page.goto("/?lang=en&scenario=first-run&simulate=0#/welcome/language");
  // Focus lands on the heading once the step's effects (and key listeners) are in place.
  await expect(heading(page, "Your lectures, turned into study notes.")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(heading(page, "Choose your AI assistant")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(heading(page, "Your lectures, turned into study notes.")).toBeVisible();
});

test("a folder with two notebooks asks one question before connecting", async ({ page }) => {
  await seed(page, through("files", "en", { folder: { path: "~/Documents/University/Statistics", reviewFirst: false } }));
  await page.goto("/?lang=en&scenario=files-selection&simulate=0#/welcome/files");
  const question = page.getByRole("radiogroup", { name: "Which file is your notebook?" });
  await expect(question).toBeVisible();
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByText("Choose your notebook to continue.")).toBeVisible();
  await question.getByRole("radio", { name: /Appunti personali/ }).click();
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(heading(page, "Add your class schedule")).toBeVisible();
});

test("a public Google calendar link gets one fix: the private iCal address", async ({ page }) => {
  await seed(page, through("schedule", "en"));
  await page.goto("/?lang=en&scenario=schedule-error&simulate=0#/welcome/schedule");
  await page.getByLabel("Calendar link").fill("https://calendar.google.com/calendar/embed?src=classes");
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Google won't share this calendar publicly.");
  await page.getByRole("button", { name: "Paste your private iCal address" }).click();
  await expect(page.getByRole("radio", { name: "Google Calendar" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByText("Find your private iCal address")).toBeVisible();
});

test("connecting a calendar waits for its job, then previews the lessons", async ({ page }) => {
  await seed(page, through("schedule", "en"));
  await page.goto("/?lang=en#/welcome/schedule");
  await page.getByLabel("Calendar link").fill("https://timetable.example.edu/statistics.ics");
  await page.getByRole("button", { name: "Preview", exact: true }).click();
  // `capture.connect` is a job: the summary appears once `job.finished` carries CONNECTED.
  await expect(page.getByText("Found in your calendar")).toBeVisible();
  await expect(page.getByRole("button", { name: "Looks right" })).toBeVisible();
});

test("a TXT transcript makes the first notes without a recording", async ({ page }) => {
  await seed(page, through("first-notes", "en"));
  await page.goto("/?lang=en#/welcome/first-notes");
  await page.getByRole("radio", { name: /I have a recording/ }).click();
  await page.locator('input[type="file"]').setInputFiles({ name: "Lesson 1.txt", mimeType: "text/plain", buffer: Buffer.from("Today: sampling.\n") });
  await expect(page.getByText("Lesson 1.txt")).toBeVisible();
  await expect(page.getByText("Ready to use")).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.getByRole("button", { name: "Generate" }).click();
  await expect(heading(page, "You're ready.")).toBeVisible();
  await expect(page.getByText("Your first notes are being written.")).toBeVisible();
});

test("Back always works, even from the waiting screen", async ({ page }) => {
  await seed(page, through("files", "en", {}, false));
  await page.goto("/?lang=en&scenario=setup-running&simulate=0#/welcome/files");
  await expect(heading(page, "Almost there")).toBeVisible();
  await page.getByRole("button", { name: "Back" }).click();
  await expect(heading(page, "How should your notes read?")).toBeVisible();
});
