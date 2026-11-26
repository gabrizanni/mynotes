import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "../i18n";
import type { BackendStatus } from "../rpc/native";
import { BackendFailure } from "./BackendFailure";
import { failureDetails, MAX_DETAIL_LINES, shownFailure, type FailedStatus } from "./backendFailure";

const crashLoop: FailedStatus = {
  state: "failed",
  code: "CRASH_LOOP",
  message: "MyNotes stopped 5 times in 120 s; last: exit status 1",
  crashes: 5,
  stderrTail: ["Traceback (most recent call last):", "ModuleNotFoundError: No module named 'mynotes'"],
};

describe("backend failure screen", () => {
  it("replaces the app only when the supervisor gave up for a reason setup cannot fix", () => {
    expect(shownFailure(crashLoop)).toBe(crashLoop);
    expect(shownFailure({ ...crashLoop, code: "PROTOCOL_MISMATCH" })).not.toBeNull();
    expect(shownFailure({ ...crashLoop, code: "NOT_INSTALLED" })).toBeNull(); // Setup shows that one.
    const ready: BackendStatus = { state: "ready", pid: 1, serverPid: 2, protocol: 1, version: "0.4", generation: 1 };
    expect(shownFailure(ready)).toBeNull();
    expect(shownFailure(null)).toBeNull();
  });

  it("copies the code, the message and the last stderr lines", () => {
    const many = { ...crashLoop, stderrTail: Array.from({ length: 60 }, (_, n) => `line ${n}`) };
    const text = failureDetails(many, { appVersion: "0.1.0", platform: "Linux x86_64" });
    expect(text.split("\n").slice(0, 3)).toEqual(["MyNotes Desktop 0.1.0 (Linux x86_64)", "Backend: CRASH_LOOP, 5 stop(s)", crashLoop.message]);
    expect(text).toContain("line 59");
    expect(text).not.toContain("line 19\n");
    expect(text.split("\n").filter((line) => line.startsWith("line "))).toHaveLength(MAX_DETAIL_LINES);
  });

  it("shows the stderr tail and offers Try again and Copy details", async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn(() => Promise.resolve());
    const copy = vi.fn((_text: string) => Promise.resolve());
    render(
      <I18nProvider lang="en">
        <BackendFailure status={crashLoop} onRetry={onRetry} copy={copy} />
      </I18nProvider>,
    );
    expect(screen.getByRole("heading", { name: "MyNotes stopped working" })).toBeTruthy();
    expect(screen.getByText(/stopped 5 times in a row/)).toBeTruthy();
    expect(screen.getByText(/ModuleNotFoundError/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Copy details" }));
    expect(copy.mock.calls[0]?.[0]).toContain("ModuleNotFoundError: No module named 'mynotes'");
    expect(await screen.findByRole("status")).toHaveProperty("textContent", "Details copied");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("keeps the details on screen when the clipboard is blocked", async () => {
    const user = userEvent.setup();
    render(
      <I18nProvider lang="it">
        <BackendFailure status={crashLoop} onRetry={() => Promise.resolve()} copy={() => Promise.reject(new Error("blocked"))} />
      </I18nProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Copia dettagli" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/a mano/);
    expect(screen.getByText(/ModuleNotFoundError/)).toBeTruthy();
  });
});
