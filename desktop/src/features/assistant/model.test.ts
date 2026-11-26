import { describe, expect, it } from "vitest";

import {
  PENDING_TURN,
  durationParts,
  emptyView,
  errorView,
  isBusy,
  lastTurn,
  pageContext,
  providerInfo,
  reduce,
  splitBlocks,
  splitInline,
  toolLabel,
  type AssistantEvent,
  type AssistantView,
} from "./model";

const ids = { sessionId: "as-0000000000000001", turnId: "turn-1" };

function run(events: AssistantEvent[], start: AssistantView = emptyView): AssistantView {
  return events.reduce(reduce, start);
}

const tool = { toolId: "toolu_1", name: "Bash", summary: "Prepare notes", command: "mynotes notes STAT --lessons 3 --json", durationMs: null };

describe("stream reducer", () => {
  it("builds a reply from deltas, tool rows and done", () => {
    const view = run([
      { type: "sending", message: "Prepare notes for lesson 3", provider: null },
      { type: "accepted", ...ids },
      { type: "assistant.delta", ...ids, text: "Let me " },
      { type: "assistant.tool", ...ids, ...tool, status: "running" },
      { type: "assistant.delta", ...ids, text: "check." },
      { type: "assistant.tool", ...ids, ...tool, status: "completed", durationMs: 132_000 },
      { type: "assistant.done", ...ids, text: "Let me check.", providerSession: "p" },
    ]);
    const turn = lastTurn(view);
    expect(turn).toMatchObject({ id: "turn-1", state: "completed", text: "Let me check.", message: "Prepare notes for lesson 3" });
    expect(turn?.tools).toEqual([{ ...tool, status: "completed", durationMs: 132_000 }]);
    expect(isBusy(view)).toBe(false);
  });

  it("is busy from sending until the turn ends, and a pending turn takes the server id", () => {
    const sending = run([{ type: "sending", message: "Hi", provider: "codex" }]);
    expect(lastTurn(sending)?.id).toBe(PENDING_TURN);
    expect(isBusy(sending)).toBe(true);
    const accepted = reduce(sending, { type: "accepted", ...ids });
    expect(lastTurn(accepted)).toMatchObject({ id: "turn-1", state: "running" });
    expect(isBusy(accepted)).toBe(true);
    expect(accepted.status === "ready" && accepted.conversation.provider).toBe("codex");
  });

  it("ignores events of another conversation and deltas after the end", () => {
    const view = run([
      { type: "sending", message: "Hi", provider: null },
      { type: "accepted", ...ids },
      { type: "assistant.delta", sessionId: "as-other", turnId: "turn-1", text: "ignored" },
      { type: "assistant.done", ...ids, text: "Done.", providerSession: null },
      { type: "assistant.delta", ...ids, text: " late" },
    ]);
    expect(lastTurn(view)?.text).toBe("Done.");
  });

  it("keeps partial text when stopped and turns running tools into failed ones on error", () => {
    const base: AssistantEvent[] = [
      { type: "sending", message: "Hi", provider: null },
      { type: "accepted", ...ids },
      { type: "assistant.delta", ...ids, text: "Working" },
      { type: "assistant.tool", ...ids, ...tool, status: "running" },
    ];
    const stopped = run([...base, { type: "assistant.error", ...ids, code: "CANCELLED", message: "Stopped" }]);
    expect(lastTurn(stopped)).toMatchObject({ state: "cancelled", text: "Working", error: null });
    const failed = run([...base, { type: "assistant.error", ...ids, code: "AUTH", message: "Please run /login" }]);
    expect(lastTurn(failed)).toMatchObject({ state: "failed", error: { code: "AUTH", message: "Please run /login" } });
    expect(lastTurn(failed)?.tools[0]?.status).toBe("failed");
  });

  it("marks a rejected send as failed and restores saved conversations", () => {
    const rejected = run([{ type: "sending", message: "Hi", provider: null }, { type: "rejected", error: { code: "BUSY", message: "busy" } }]);
    expect(lastTurn(rejected)).toMatchObject({ state: "failed", error: { code: "BUSY" } });
    const restored = reduce(emptyView, {
      type: "restored",
      session: { id: "as-1", provider: "claude", turns: [{ id: "turn-1", message: "Hi", state: "RUNNING", text: "Par", tools: [] }] },
    });
    expect(lastTurn(restored)).toMatchObject({ state: "running", text: "Par" });
    expect(reduce(restored, { type: "restored", session: { id: "as-2", provider: "claude", turns: [] } })).toEqual(emptyView);
    expect(reduce(restored, { type: "reset" })).toEqual(emptyView);
  });

  it("maps unknown tool statuses to running and unknown turn states to failed", () => {
    const restored = reduce(emptyView, {
      type: "restored",
      session: { id: "as-1", provider: "claude", turns: [{ id: "turn-1", message: "Hi", state: "WEIRD", text: "", tools: [{ ...tool, status: "odd" }] }] },
    });
    expect(lastTurn(restored)?.state).toBe("failed");
    expect(lastTurn(restored)?.tools[0]?.status).toBe("running");
  });
});

describe("markdown-lite splitter", () => {
  it("splits paragraphs, headings, lists and fenced code", () => {
    const blocks = splitBlocks("# Summary\nFirst line\nsecond line\n\n- one\n- two\n  continued\n\n1. a\n2) b\n\n```python\nprint('x')\n```\nAfter");
    expect(blocks).toEqual([
      { kind: "heading", text: "Summary" },
      { kind: "paragraph", text: "First line\nsecond line" },
      { kind: "list", ordered: false, items: ["one", "two continued"] },
      { kind: "list", ordered: true, items: ["a", "b"] },
      { kind: "code", lang: "python", code: "print('x')", open: false },
      { kind: "paragraph", text: "After" },
    ]);
  });

  it("keeps an unclosed fence open while the reply streams", () => {
    expect(splitBlocks("Run:\n```\nmynotes notes STAT")).toEqual([
      { kind: "paragraph", text: "Run:" },
      { kind: "code", lang: "", code: "mynotes notes STAT", open: true },
    ]);
  });

  it("treats HTML as literal text", () => {
    const html = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    expect(splitBlocks(html)).toEqual([{ kind: "paragraph", text: html }]);
    expect(splitInline(html)).toEqual([{ kind: "text", text: html }]);
  });

  it("splits inline code and strong spans, leaving unmatched markers alone", () => {
    expect(splitInline("Open `Word` for **STAT** now, 2 * 3 `x")).toEqual([
      { kind: "text", text: "Open " },
      { kind: "code", text: "Word" },
      { kind: "text", text: " for " },
      { kind: "strong", text: "STAT" },
      { kind: "text", text: " now, 2 * 3 `x" },
    ]);
  });

  it("reads *emphasis* but not a multiplication", () => {
    expect(splitInline("A *new* section, 2 * 3 * 4")).toEqual([
      { kind: "text", text: "A " },
      { kind: "em", text: "new" },
      { kind: "text", text: " section, 2 * 3 * 4" },
    ]);
  });

  it("ignores a closing fence of another kind and normalizes CRLF", () => {
    expect(splitBlocks("~~~\na\n```\nb\n~~~\r\nend")).toEqual([
      { kind: "code", lang: "", code: "a\n```\nb", open: false },
      { kind: "paragraph", text: "end" },
    ]);
  });
});

describe("tool labels, durations, errors, provider, page context", () => {
  it("names MyNotes commands and falls back to the summary", () => {
    expect(toolLabel({ name: "Bash", summary: "x", command: "mynotes notes STAT --lessons 3 --json" })).toEqual({ kind: "notes", course: "STAT", lessons: "3" });
    expect(toolLabel({ name: "shell", summary: "x", command: "bash -lc 'mynotes transcribe STAT-L03 --json'" })).toEqual({ kind: "transcribe", course: "STAT", lesson: "3" });
    expect(toolLabel({ name: "Bash", summary: "x", command: "mynotes context --activity notes" })).toEqual({ kind: "instructions" });
    expect(toolLabel({ name: "Bash", summary: "x", command: "mynotes undo STAT" })).toEqual({ kind: "undo", course: "STAT" });
    expect(toolLabel({ name: "Bash", summary: "x", command: "mynotes capture sync" })).toEqual({ kind: "calendar" });
    expect(toolLabel({ name: "Read", summary: "Read /tmp/a/slides.txt", command: null })).toEqual({ kind: "read", file: "slides.txt" });
    expect(toolLabel({ name: "Bash", summary: "List files", command: "ls -la" })).toEqual({ kind: "command", text: "List files" });
  });

  it("rounds durations to seconds then minutes", () => {
    expect(durationParts(null)).toBeNull();
    expect(durationParts(400)).toBeNull();
    expect(durationParts(45_200)).toEqual({ seconds: 45 });
    expect(durationParts(132_000)).toEqual({ minutes: 2 });
  });

  it("gives every error one fix", () => {
    expect(errorView({ code: "AUTH", message: "" })).toEqual({ kind: "auth", fix: "signIn" });
    expect(errorView({ code: "QUOTA", message: "" })).toEqual({ kind: "quota", fix: "none" });
    expect(errorView({ code: "UNAVAILABLE", message: "" })).toEqual({ kind: "unavailable", fix: "settings" });
    expect(errorView({ code: "SOMETHING_NEW", message: "" })).toEqual({ kind: "other", fix: "retry" });
  });

  it("names the provider in the footer", () => {
    expect(providerInfo("codex")).toEqual({ name: "Codex", company: "OpenAI" });
    expect(providerInfo("agy")).toEqual({ name: "Antigravity", company: "Google" });
    expect(providerInfo(null)).toEqual({ name: "Claude Code", company: "Anthropic" });
  });

  it("sends the page as data, with course and lesson when they are known", () => {
    expect(pageContext({ name: "lesson", id: "STAT-L03" }, "STAT-L03")).toEqual({ course: "STAT", lesson: 3, lectureId: "STAT-L03", page: "STAT-L03" });
    expect(pageContext({ name: "course", code: "STAT" }, "STAT")).toEqual({ course: "STAT", page: "STAT" });
    expect(pageContext({ name: "course", code: "../x" }, "x")).toEqual({ page: "x" });
    expect(pageContext({ name: "today" }, "Today")).toEqual({ page: "Today" });
  });
});
