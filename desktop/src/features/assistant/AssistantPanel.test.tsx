import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { createRpcClient } from "../../rpc/client";
import { createMockTransport } from "../../rpc/transports/mock";
import { I18nProvider } from "../../i18n";
import { createAppStore, initialAppState, StoreProvider, type AppStore } from "../../state";
import { AssistantPanel, openAssistantWith } from ".";
import { en } from "../../i18n/en";

function renderPanel(options: { scenario?: string; simulate?: boolean; lang?: "en" | "it" } = {}) {
  const rpc = createRpcClient(
    createMockTransport({ scenario: options.scenario ?? "default", simulate: options.simulate ?? true, latencyMs: 5, now: () => new Date("2026-11-26T09:42:00Z") }),
  );
  const app: AppStore = createAppStore(rpc, initialAppState({ assistantOpen: true }));
  render(
    <StoreProvider value={app}>
      <I18nProvider lang={options.lang ?? "en"}>
        <AssistantPanel />
      </I18nProvider>
    </StoreProvider>,
  );
  return { app, rpc };
}

describe("AssistantPanel", () => {
  it("shows suggestions when there is no conversation, and a suggestion fills the composer", async () => {
    const user = userEvent.setup();
    renderPanel({ simulate: false });
    const suggestion = await screen.findByRole("button", { name: "Quiz me on lessons 1–4" });
    expect(screen.getByText("Messages go to Claude Code (Anthropic).")).toBeTruthy();
    await user.click(suggestion);
    expect(screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Message" }).value).toBe("Quiz me on lessons 1–4");
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  });

  it("streams a reply with a tool row that expands to its command", async () => {
    const user = userEvent.setup();
    renderPanel();
    const input = await screen.findByRole("textbox", { name: "Message" });
    await user.type(input, "Prepare notes for lesson 3{Enter}");
    expect(await screen.findByRole("button", { name: "Stop" })).toBeTruthy();
    const log = screen.getByRole("log", { name: "Conversation" });
    const row = await within(log).findByRole("button", { name: /Prepared notes for STAT lesson 3 · 2 min/ }, { timeout: 3000 });
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeTruthy(), { timeout: 3000 });
    expect(within(log).getByText("STAT lesson 3")).toBeTruthy(); // **strong** span
    expect(within(log).getByText("mynotes notes STAT --lessons 3").closest("pre")).toBeTruthy(); // fenced code
    expect(row.getAttribute("aria-expanded")).toBe("false");
    await user.click(row);
    expect(row.getAttribute("aria-expanded")).toBe("true");
    expect(within(log).getByText("mynotes notes STAT --lessons 3 --json")).toBeTruthy();
    expect(log.querySelector("script, img, iframe")).toBeNull();
  });

  it("stops a running reply and keeps what was written", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.type(await screen.findByRole("textbox", { name: "Message" }), "Long task{Enter}");
    await screen.findByText(/I checked the sources for/, undefined, { timeout: 3000 });
    await user.click(screen.getByRole("button", { name: "Stop" }));
    expect(await screen.findByText(en.assistant.stopped)).toBeTruthy();
    expect(screen.getByText(/I checked the sources for/)).toBeTruthy();
  });

  it("restores the last conversation with its error as one problem and one fix", async () => {
    renderPanel({ scenario: "assistant-error", simulate: false });
    expect(await screen.findByText("You're signed out of Claude Code.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeTruthy();
    expect(screen.queryByText(/Please run \/login/)).toBeNull(); // backend text stays out of the UI
  });

  it("restores a reply that is still streaming in Italian", async () => {
    renderPanel({ scenario: "assistant-streaming-it", simulate: false, lang: "it" });
    expect(await screen.findByText("Al lavoro…")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Ferma" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /Preparo gli appunti per STAT, lezione 3/ })).toBeTruthy();
  });

  it("opens with a prefilled message from the Generate flow's conflict outcome", async () => {
    const rpc = createRpcClient(createMockTransport({ simulate: false, latencyMs: 0 }));
    const app = createAppStore(rpc, initialAppState());
    act(() => openAssistantWith(app.actions, en.assistant.handoff.personalContentConflict("Statistics (STAT)", "Lesson 3")));
    expect(app.store.getState().ui.assistantOpen).toBe(true);
    render(
      <StoreProvider value={app}>
        <I18nProvider lang="en">
          <AssistantPanel />
        </I18nProvider>
      </StoreProvider>,
    );
    const input = await screen.findByRole<HTMLTextAreaElement>("textbox", { name: "Message" });
    expect(input.value).toMatch(/^My edits in the Statistics \(STAT\) course notes overlap the new notes for Lesson 3/);
    expect(document.activeElement).toBe(input);
  });
});
