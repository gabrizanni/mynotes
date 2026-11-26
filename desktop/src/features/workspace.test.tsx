/** Page-level tests for the workspace screens (Calendar, Library, Activity, Settings, palette). */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadAll, mockApp } from "../../tests/unit/helpers";
import { I18nProvider } from "../i18n";
import { StoreProvider, type AppStore } from "../state";
import type { SettingsSection } from "../state/routes";
import { ActivityPage } from "./activity";
import { CalendarPage } from "./calendar";
import { LibraryPage } from "./library";
import { CommandPalette } from "./palette";
import { SettingsPage } from "./settings";

const NOW = "2026-11-26T09:42:00Z";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW));
});
afterEach(() => {
  vi.useRealTimers();
});

async function setup(scenario: string, ui: (app: AppStore) => React.ReactNode, lang: "en" | "it" = "en") {
  const { app } = mockApp(scenario, { now: new Date(NOW) });
  await loadAll(app);
  render(
    <StoreProvider value={app}>
      <I18nProvider lang={lang}>{ui(app)}</I18nProvider>
    </StoreProvider>,
  );
  return { app, user: userEvent.setup({ advanceTimers: vi.advanceTimersByTime }) };
}

function primaryButtons(): HTMLElement[] {
  return screen.queryAllByRole("button").filter((button) => button.className.includes("primary"));
}

describe("Calendar", () => {
  it("shows the week with clash labels and opens a lesson's details", async () => {
    const { user } = await setup("workspace", () => <CalendarPage route={{ name: "calendar", view: "week", date: null }} zone="Europe/Rome" />);
    expect(screen.getByRole("heading", { level: 1, name: "Calendar" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2 }).textContent).toMatch(/23\s?–\s?29.*2026/);
    const clash = screen.getByRole("button", { name: /^Diritto commerciale, Lesson 3, .*Time clash/ });
    await user.click(clash);
    const popover = screen.getByRole("dialog", { name: "Diritto commerciale, details" });
    expect(within(popover).getByText(/Overlaps Corporate Finance, 09:30–11:00\./)).toBeTruthy();
    expect(primaryButtons()).toHaveLength(0);
  });

  it("restores a lesson edited here", async () => {
    const { app, user } = await setup("workspace", () => <CalendarPage route={{ name: "calendar", view: "week", date: null }} zone="Europe/Rome" />);
    const reset = vi.spyOn(app.actions.capture, "resetLesson");
    await user.click(screen.getByRole("button", { name: /^Corporate Finance, Lesson 4, .*Edited here/ }));
    await user.click(screen.getByRole("button", { name: "Restore from calendar" }));
    expect(reset).toHaveBeenCalledWith("uni-timetable:CORPFIN-L04");
  });

  it("lays out 42 days in the month and speaks Italian", async () => {
    await setup("workspace", () => <CalendarPage route={{ name: "calendar", view: "month", date: "2026-11-26" }} zone="Europe/Rome" />, "it");
    expect(screen.getAllByRole("button", { name: /^Mostra la settimana di / })).toHaveLength(42);
    expect(screen.getByRole("heading", { level: 2, name: "Novembre 2026" })).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/CONFLICT|NEEDS_REVIEW|UNKNOWN_COURSE/);
  });
});

describe("Library", () => {
  it("filters to Needs a course and shows the details with Choose a course", async () => {
    const { user } = await setup("workspace", () => <LibraryPage route={{ name: "library", course: null, q: "", unassigned: false }} />);
    expect(primaryButtons().map((button) => button.textContent)).toEqual(["Import audio"]);
    await user.click(screen.getByRole("button", { name: /Needs a course/, pressed: false }));
    const rows = screen.getAllByRole("listitem").filter((item) => item.closest("ul")?.getAttribute("role") === "list");
    expect(rows.some((row) => row.textContent?.includes("Recording 19 Nov 14:02"))).toBe(true);
    expect(screen.getByRole("heading", { level: 2, name: "Recording 19 Nov 14:02" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add to course" })).toBeTruthy();
  });

  it("searches without accents and offers to clear filters", async () => {
    const { user } = await setup("workspace", () => <LibraryPage route={{ name: "library", course: null, q: "", unassigned: false }} />);
    await user.type(screen.getByRole("searchbox", { name: "Search recordings and transcripts" }), "nothing here");
    expect(screen.getByText("Nothing matches.")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.queryByText("Nothing matches.")).toBeNull();
  });
});

describe("Activity", () => {
  it("pauses the queue and shows the paused banner", async () => {
    const { user } = await setup("paused", () => <ActivityPage />);
    expect(screen.getByText("Paused · 3 waiting, 1 finishing")).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "In progress" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Resume all" }));
    expect(screen.queryByText(/Paused ·/)).toBeNull();
    expect(screen.getByRole("button", { name: "Pause all" })).toBeTruthy();
  });

  it("transcribes every ready recording with the one primary button", async () => {
    const { app, user } = await setup("workspace", () => <ActivityPage />);
    const enqueue = vi.spyOn(app.actions.queue, "enqueue");
    expect(primaryButtons().map((button) => button.textContent)).toEqual(["Transcribe all"]);
    await user.click(screen.getByRole("button", { name: "Transcribe all" }));
    expect(enqueue).toHaveBeenCalledWith();
    expect(screen.getByRole("radio", { name: "2", checked: true })).toBeTruthy();
  });
});

describe("Settings", () => {
  it.each<[SettingsSection, string]>([
    ["assistant", "AI assistant"],
    ["transcription", "Transcription"],
    ["recording", "Recording"],
    ["calendars", "Calendars"],
    ["general", "General"],
    ["profile", "About you"],
    ["diagnostics", "Check setup"],
  ])("renders the %s section with at most one primary action", async (section, title) => {
    await setup("workspace", () => <SettingsPage route={{ name: "settings", section }} />);
    expect(await screen.findByRole("heading", { level: 2, name: title })).toBeTruthy();
    expect(screen.getByRole("link", { name: title }).getAttribute("aria-current")).toBe("page");
    expect(primaryButtons().length).toBeLessThanOrEqual(1);
  });

  it("shows the Google calendar problem with its fix", async () => {
    await setup("workspace", () => <SettingsPage route={{ name: "settings", section: "calendars" }} />);
    expect(screen.getByText("Google won't share this calendar publicly.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Paste your private iCal address" })).toBeTruthy();
  });

  it("lists setup checks with a fix for each problem", async () => {
    await setup("workspace", () => <SettingsPage route={{ name: "settings", section: "diagnostics" }} />);
    expect(await screen.findByText("Word rendering isn't verified.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Verify Word" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy report" })).toBeTruthy();
  });
});

describe("Command palette", () => {
  it("opens with Ctrl+K, filters, and navigates on Enter", async () => {
    const { app, user } = await setup("default", () => <CommandPalette />);
    await user.keyboard("{Control>}k{/Control}");
    const field = screen.getByRole("combobox", { name: "Type a command or a course" });
    await user.type(field, "activity");
    await user.keyboard("{Enter}");
    expect(app.store.getState().ui.route).toEqual({ name: "activity" });
  });

  it("asks which course before undoing, then confirms", async () => {
    const { app, user } = await setup("default", () => <CommandPalette />);
    const undo = vi.spyOn(app.actions.jobs, "undoLastUpdate");
    await user.keyboard("{Control>}k{/Control}");
    await user.type(screen.getByRole("combobox"), "undo");
    await user.keyboard("{Enter}");
    await user.type(screen.getByRole("combobox"), "statistics");
    await user.keyboard("{Enter}");
    await user.click(await screen.findByRole("button", { name: "Undo update" }));
    expect(undo).toHaveBeenCalledWith("STAT");
  });
});
