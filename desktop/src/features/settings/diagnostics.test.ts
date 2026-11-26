import { describe, expect, it } from "vitest";

import { buildReport, redactText, redactUrl, sortedChecks } from "./diagnostics";

const SAMPLE_HOME = ["", "home", "student", "Documents", "MyNotes"].join("/");

describe("Copy report redaction", () => {
  it("removes home folders on Linux, macOS-style roots and Windows", () => {
    expect(redactText(`Workspace at ${SAMPLE_HOME}/workspace`)).toBe("Workspace at ~/Documents/MyNotes/workspace");
    expect(redactText(["", "Volumes", "Backup", "rec.m4a"].join("/"))).toBe("~/rec.m4a");
    expect(redactText("C:\\Users\\student\\Documents\\MyNotes")).toBe("~\\Documents\\MyNotes");
    // Already redacted paths and system folders stay readable.
    expect(redactText("~/Documents/MyNotes")).toBe("~/Documents/MyNotes");
    expect(redactText("/usr/bin/soffice")).toBe("/usr/bin/soffice");
  });

  it("keeps only the host of calendar links", () => {
    expect(redactText("https://calendar.google.com/calendar/ical/private-abc123/basic.ics")).toBe("https://calendar.google.com/…");
    expect(redactText("webcal://timetable.example.edu/feed?token=secret")).toBe("webcal://timetable.example.edu/…");
  });

  it("drops userinfo, with or without a password", () => {
    expect(redactText("https://user:secret@calendar.example/feed.ics")).toBe("https://calendar.example/…");
    expect(redactText("https://token123@calendar.example/feed.ics")).toBe("https://calendar.example/…");
    expect(redactText("webcal://user:secret@timetable.example.edu:8443/f.ics")).toBe("webcal://timetable.example.edu/…");
    expect(redactText("see https://user:secret@calendar.example/x")).not.toMatch(/user|secret/);
  });

  it("handles uppercase schemes, quoted links and broken links", () => {
    expect(redactText("HTTPS://Calendar.Example/ical/private-abc/basic.ics")).toBe("https://calendar.example/…");
    expect(redactText('{"url": "webcal://cal.example.org/feed?token=abc"}')).toBe('{"url": "webcal://cal.example.org/…"}');
    expect(redactText("'https://cal.example.org/a#frag'")).toBe("'https://cal.example.org/…'");
    expect(redactText("https://[broken/feed")).toBe("[url removed]");
    expect(redactUrl("https://exa mple.org")).toBe("[url removed]");
  });

  it("builds a JSON report with versions, checks and redacted calendars", () => {
    const report = buildReport({
      doctor: { ready: false, checks: { word: { status: "missing", action: `Install it in ${SAMPLE_HOME}` }, installation: { status: "ready" } } },
      dashboard: null,
      appVersion: "0.1.0",
      platform: "linux",
      now: new Date("2026-11-26T09:42:00Z"),
    });
    const parsed = JSON.parse(report) as { app: { version: string }; doctor: { checks: { word: { action: string } } } };
    expect(parsed.app.version).toBe("0.1.0");
    expect(parsed.doctor.checks.word.action).toBe("Install it in ~/Documents/MyNotes");
    expect(report).not.toContain("student");
  });

  it("orders checks the way a student reads them", () => {
    const sorted = sortedChecks({ checks: { zeta: { status: "ready" }, word: { status: "ready" }, installation: { status: "ready" } } });
    expect(sorted.map(([name]) => name)).toEqual(["installation", "word", "zeta"]);
  });
});
