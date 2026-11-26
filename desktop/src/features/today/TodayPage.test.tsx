import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";
import { I18nProvider } from "../../i18n";
import { StoreProvider } from "../../state";
import { TodayPage } from "./TodayPage";

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
});
afterEach(() => {
  vi.useRealTimers();
});

async function renderToday(scenario: string, iso: string, lang: "en" | "it" = "en") {
  vi.setSystemTime(new Date(iso));
  const { app } = mockApp(scenario, { now: new Date(iso) });
  await loadAll(app);
  render(
    <StoreProvider value={app}>
      <I18nProvider lang={lang}>
        <TodayPage />
      </I18nProvider>
    </StoreProvider>,
  );
  return app;
}

describe("TodayPage", () => {
  it("renders one h1 and exactly one primary action per state", async () => {
    await renderToday("default", "2026-11-26T09:42:00Z");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    const hero = screen.getByRole("region", { name: "Microeconomics" });
    expect(within(hero).getByRole("button", { name: "Record now" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "Needs you" })).toBeTruthy();
  });

  it.each([
    ["recording", "2026-11-26T08:47:00Z", "Stop recording"],
    ["error", "2026-11-26T07:51:00Z", "Open Privacy settings"],
    ["approval", "2026-11-26T09:42:00Z", "Review notes"],
    ["generate", "2026-11-26T09:42:00Z", "Generate notes"],
  ])("hero action for %s", async (scenario, iso, label) => {
    await renderToday(scenario, iso);
    expect(screen.getByRole("button", { name: label })).toBeTruthy();
  });

  it("shows the empty day with an import action", async () => {
    await renderToday("empty", "2026-11-26T09:42:00Z");
    expect(screen.getByText("No lessons today.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Import a recording" })).toBeTruthy();
  });

  it("speaks Italian without leaking backend codes", async () => {
    await renderToday("default", "2026-11-26T09:42:00Z", "it");
    expect(screen.getByRole("heading", { level: 1, name: "Oggi" })).toBeTruthy();
    expect(screen.getByText("In pausa")).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/PAUSED_QUOTA|NEEDS_REVIEW|AWAITING_APPROVAL/);
  });

  it("sends Record now to the recorder", async () => {
    const app = await renderToday("default", "2026-11-26T09:42:00Z");
    const start = vi.spyOn(app.actions.capture, "start");
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "Record now" }));
    expect(start).toHaveBeenCalledWith(expect.objectContaining({ unassigned: true, durationMinutes: 180 }));
  });
});
