import { render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";
import { I18nProvider } from "../../i18n";
import { COURSE_TABS, StoreProvider, type CourseTab } from "../../state";
import { LessonPage } from "../lesson";
import { CoursePage } from "./CoursePage";
import { CoursesPage } from "./CoursesPage";

const NOW = "2026-11-26T09:42:00Z";
let errors: unknown[] = [];

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
  errors = [];
  vi.spyOn(console, "error").mockImplementation((...args: unknown[]) => {
    errors.push(args);
  });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function renderWith(node: ReactNode, scenario = "course", lang: "en" | "it" = "en") {
  const { app } = mockApp(scenario, { now: new Date(NOW) });
  await loadAll(app);
  render(
    <StoreProvider value={app}>
      <I18nProvider lang={lang}>{node}</I18nProvider>
    </StoreProvider>,
  );
}

/** Primary buttons carry the `primary` variant class from Button.module.css. */
const primaries = () => document.querySelectorAll('button[class*="primary"]').length;

describe("course screens", () => {
  it("lists every course as one link card", async () => {
    await renderWith(<CoursesPage />);
    expect(await screen.findByRole("link", { name: /Statistics.*7 lessons/s })).toBeTruthy();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
  });

  it.each(COURSE_TABS)("renders the %s tab with one h1, at most one primary, no React errors", async (tab: CourseTab) => {
    await renderWith(<CoursePage route={{ name: "course", code: "STAT", tab }} />);
    expect(await screen.findByRole("heading", { level: 1, name: "Statistics" })).toBeTruthy();
    expect(screen.getByRole("link", { name: { lessons: "Lessons", notes: "Notes", style: "Style", profile: "Profile", files: "Files" }[tab] }).getAttribute("aria-current")).toBe("page");
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(primaries()).toBeLessThanOrEqual(1);
    expect(errors).toEqual([]);
  });

  it("speaks Italian on the Notes tab", async () => {
    await renderWith(<CoursePage route={{ name: "course", code: "STAT", tab: "notes" }} />, "course", "it");
    expect(await screen.findByRole("button", { name: "Apri in Word" })).toBeTruthy();
    expect(screen.getByText("Il tuo quaderno")).toBeTruthy();
  });
});

describe("lesson screen", () => {
  it.each([
    ["generate", "STAT-L07", "Generate notes"],
    ["default", "STAT-L07", null],
    ["course", "MICRO-L03", null],
    ["course", "CORPFIN-L02", "Transcribe this recording"],
  ])("%s %s shows its one primary action", async (scenario, id, primary) => {
    await renderWith(<LessonPage route={{ name: "lesson", id }} />, scenario);
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    if (primary) expect(screen.getByRole("button", { name: primary })).toBeTruthy();
    expect(primaries()).toBe(primary ? 1 : 0);
    expect(errors).toEqual([]);
  });
});
