/**
 * Assistant panel view model (pure): the stream reducer, the Markdown-lite splitter, tool row
 * labels and the error view. The backend contract is `assistant.*` in docs/SERVE.md.
 *
 * Messages are rendered as text nodes only: `splitBlocks` and `splitInline` return plain
 * strings, never HTML, so a reply can never inject markup.
 */
import type { AssistantNotification, AssistantSession, AssistantToolRow } from "../../rpc/schema";

// ---------------------------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------------------------

export type ToolStatus = "running" | "completed" | "failed" | "denied";
export type TurnState = "sending" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

export interface ToolRow {
  readonly toolId: string;
  readonly name: string;
  readonly summary: string;
  readonly status: ToolStatus;
  readonly command: string | null;
  readonly durationMs: number | null;
}

export interface TurnError {
  readonly code: string;
  readonly message: string;
}

export interface Turn {
  readonly id: string;
  readonly message: string;
  readonly text: string;
  readonly tools: readonly ToolRow[];
  readonly state: TurnState;
  readonly error: TurnError | null;
}

export interface Conversation {
  readonly sessionId: string | null;
  readonly provider: string | null;
  readonly turns: readonly Turn[];
}

export type AssistantView =
  | { readonly status: "restoring" }
  | { readonly status: "empty" }
  | { readonly status: "ready"; readonly conversation: Conversation };

export const emptyView: AssistantView = { status: "empty" };
export const PENDING_TURN = "pending";

export type AssistantEvent =
  | { readonly type: "restored"; readonly session: AssistantSession | null }
  | { readonly type: "sending"; readonly message: string; readonly provider: string | null }
  | { readonly type: "accepted"; readonly sessionId: string; readonly turnId: string }
  | { readonly type: "rejected"; readonly error: TurnError }
  | { readonly type: "reset" }
  | AssistantNotification;

const FINAL_STATES: ReadonlySet<TurnState> = new Set(["completed", "failed", "cancelled", "interrupted"]);
const MAX_TEXT = 200_000;

function toolStatus(value: string): ToolStatus {
  return value === "completed" || value === "failed" || value === "denied" ? value : "running";
}

function turnState(value: string): TurnState {
  const states: Record<string, TurnState> = {
    RUNNING: "running",
    COMPLETED: "completed",
    FAILED: "failed",
    CANCELLED: "cancelled",
    INTERRUPTED: "interrupted",
  };
  return states[value] ?? "failed";
}

function toTool(row: AssistantToolRow): ToolRow {
  return {
    toolId: row.toolId,
    name: row.name,
    summary: row.summary,
    status: toolStatus(row.status),
    command: row.command ?? null,
    durationMs: row.durationMs ?? null,
  };
}

/** A conversation from `assistant.get` (a restart can show a RUNNING turn still streaming). */
export function fromSession(session: AssistantSession): Conversation {
  return {
    sessionId: session.id,
    provider: session.provider,
    turns: session.turns.map((turn) => ({
      id: turn.id,
      message: turn.message,
      text: turn.text,
      tools: turn.tools.map(toTool),
      state: turnState(turn.state),
      error: turn.error ? { code: turn.error.code, message: turn.error.message } : null,
    })),
  };
}

function mapLast(conversation: Conversation, turnId: string, change: (turn: Turn) => Turn): Conversation {
  const index = conversation.turns.findIndex((turn) => turn.id === turnId);
  if (index < 0) return conversation;
  const turns = conversation.turns.slice();
  const current = turns[index];
  if (!current) return conversation;
  turns[index] = change(current);
  return { ...conversation, turns };
}

function belongs(conversation: Conversation, sessionId: string): boolean {
  return conversation.sessionId === sessionId;
}

/** The single reducer of the panel: user actions, RPC answers and server notifications. */
export function reduce(view: AssistantView, event: AssistantEvent): AssistantView {
  switch (event.type) {
    case "restored":
      return event.session && event.session.turns.length > 0 ? { status: "ready", conversation: fromSession(event.session) } : emptyView;
    case "reset":
      return emptyView;
    case "sending": {
      const previous = view.status === "ready" ? view.conversation : { sessionId: null, provider: event.provider, turns: [] };
      const turn: Turn = { id: PENDING_TURN, message: event.message, text: "", tools: [], state: "sending", error: null };
      return { status: "ready", conversation: { ...previous, turns: [...previous.turns, turn] } };
    }
    case "accepted": {
      if (view.status !== "ready") return view;
      const conversation = { ...view.conversation, sessionId: event.sessionId };
      return {
        status: "ready",
        conversation: mapLast(conversation, PENDING_TURN, (turn) => ({ ...turn, id: event.turnId, state: turn.state === "sending" ? "running" : turn.state })),
      };
    }
    case "rejected": {
      if (view.status !== "ready") return view;
      return { status: "ready", conversation: mapLast(view.conversation, PENDING_TURN, (turn) => ({ ...turn, state: "failed", error: event.error })) };
    }
    case "assistant.delta": {
      if (view.status !== "ready" || !belongs(view.conversation, event.sessionId)) return view;
      return {
        status: "ready",
        conversation: mapLast(view.conversation, event.turnId, (turn) =>
          FINAL_STATES.has(turn.state) ? turn : { ...turn, state: "running", text: (turn.text + event.text).slice(0, MAX_TEXT) },
        ),
      };
    }
    case "assistant.tool": {
      if (view.status !== "ready" || !belongs(view.conversation, event.sessionId)) return view;
      const row = toTool(event);
      return {
        status: "ready",
        conversation: mapLast(view.conversation, event.turnId, (turn) => {
          const existing = turn.tools.findIndex((tool) => tool.toolId === row.toolId);
          const tools = existing < 0 ? [...turn.tools, row] : turn.tools.map((tool, index) => (index === existing ? row : tool));
          return { ...turn, tools };
        }),
      };
    }
    case "assistant.done": {
      if (view.status !== "ready" || !belongs(view.conversation, event.sessionId)) return view;
      return {
        status: "ready",
        conversation: mapLast(view.conversation, event.turnId, (turn) => ({
          ...turn,
          state: "completed",
          text: event.text || turn.text,
          tools: turn.tools.map((tool) => (tool.status === "running" ? { ...tool, status: "completed" } : tool)),
        })),
      };
    }
    case "assistant.error": {
      if (view.status !== "ready" || !belongs(view.conversation, event.sessionId)) return view;
      const state: TurnState = event.code === "CANCELLED" ? "cancelled" : "failed";
      return {
        status: "ready",
        conversation: mapLast(view.conversation, event.turnId, (turn) => ({
          ...turn,
          state,
          error: state === "cancelled" ? null : { code: event.code, message: event.message },
          tools: turn.tools.map((tool) => (tool.status === "running" ? { ...tool, status: "failed" } : tool)),
        })),
      };
    }
  }
}

export function lastTurn(view: AssistantView): Turn | null {
  return view.status === "ready" ? (view.conversation.turns.at(-1) ?? null) : null;
}

/** True while a reply streams (or is being sent): the composer shows Stop instead of Send. */
export function isBusy(view: AssistantView): boolean {
  const turn = lastTurn(view);
  return turn !== null && (turn.state === "sending" || turn.state === "running");
}

// ---------------------------------------------------------------------------------------------
// Markdown-lite: paragraphs, headings, lists and fenced code. Text only, never HTML.
// ---------------------------------------------------------------------------------------------

export type Block =
  | { readonly kind: "paragraph"; readonly text: string }
  | { readonly kind: "heading"; readonly text: string }
  | { readonly kind: "list"; readonly ordered: boolean; readonly items: readonly string[] }
  | { readonly kind: "code"; readonly lang: string; readonly code: string; readonly open: boolean };

const FENCE = /^\s{0,3}(`{3,}|~{3,})\s*([\w+.-]*)\s*$/;
const BULLET = /^\s{0,3}[-*+]\s+(.*)$/;
const NUMBERED = /^\s{0,3}\d{1,3}[.)]\s+(.*)$/;
const HEADING = /^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$/;

/** Split a (possibly still streaming) reply into blocks; an unclosed fence stays one open code block. */
export function splitBlocks(source: string): Block[] {
  const blocks: Block[] = [];
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let fence: { marker: string; lang: string; lines: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
    paragraph = [];
  };
  const flushList = () => {
    if (list) blocks.push({ kind: "list", ordered: list.ordered, items: list.items });
    list = null;
  };

  for (const line of lines) {
    if (fence) {
      const close = FENCE.exec(line);
      if (close?.[1] && close[1][0] === fence.marker[0] && close[1].length >= fence.marker.length && !close[2]) {
        blocks.push({ kind: "code", lang: fence.lang, code: fence.lines.join("\n"), open: false });
        fence = null;
      } else {
        fence.lines.push(line);
      }
      continue;
    }
    const open = FENCE.exec(line);
    if (open?.[1]) {
      flushParagraph();
      flushList();
      fence = { marker: open[1], lang: open[2] ?? "", lines: [] };
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      flushList();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ kind: "heading", text: heading[1] ?? "" });
      continue;
    }
    const bullet = BULLET.exec(line);
    const numbered = bullet ? null : NUMBERED.exec(line);
    const item = bullet ?? numbered;
    if (item) {
      flushParagraph();
      const ordered = numbered !== null;
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push(item[1] ?? "");
      continue;
    }
    if (list) {
      // A continuation line of the last item.
      const items: string[] = list.items;
      items[items.length - 1] = `${items[items.length - 1] ?? ""} ${line.trim()}`;
      continue;
    }
    paragraph.push(line);
  }
  if (fence) blocks.push({ kind: "code", lang: fence.lang, code: fence.lines.join("\n"), open: true });
  flushParagraph();
  flushList();
  return blocks;
}

export type Inline = { readonly kind: "text" | "code" | "strong" | "em"; readonly text: string };

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|(?<![\w*])\*(?![\s*])[^*\n]+?(?<!\s)\*(?![\w*]))/g;

/** `code`, **strong** and *em* spans; everything else stays literal text. */
export function splitInline(text: string): Inline[] {
  const parts: Inline[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const index = match.index;
    if (index > last) parts.push({ kind: "text", text: text.slice(last, index) });
    const token = match[0];
    if (token.startsWith("`")) parts.push({ kind: "code", text: token.slice(1, -1) });
    else if (token.startsWith("**")) parts.push({ kind: "strong", text: token.slice(2, -2) });
    else parts.push({ kind: "em", text: token.slice(1, -1) });
    last = index + token.length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return parts;
}

// ---------------------------------------------------------------------------------------------
// Tool rows: "Prepared notes for STAT lesson 3 · 2 min"
// ---------------------------------------------------------------------------------------------

export type ToolLabel =
  | { readonly kind: "notes"; readonly course: string; readonly lessons: string | null }
  | { readonly kind: "transcribe"; readonly course: string; readonly lesson: string }
  | { readonly kind: "instructions" }
  | { readonly kind: "check" }
  | { readonly kind: "undo"; readonly course: string }
  | { readonly kind: "calendar" }
  | { readonly kind: "read"; readonly file: string }
  | { readonly kind: "command"; readonly text: string };

function words(command: string): string[] {
  return command
    .replace(/^(?:\/usr\/bin\/env\s+)?(?:bash|zsh|sh)\s+-l?c\s+/, "")
    .replace(/^['"]|['"]$/g, "")
    .trim()
    .split(/\s+/);
}

function flag(parts: readonly string[], name: string): string | null {
  const index = parts.indexOf(name);
  return index >= 0 ? (parts[index + 1] ?? null) : null;
}

const LESSON_ID = /^([A-Za-z0-9_-]+)-L0*(\d{1,3})$/;

/** What a tool call did, from its raw command when it is a MyNotes command. */
export function toolLabel(tool: Pick<ToolRow, "name" | "summary" | "command">): ToolLabel {
  const command = tool.command ?? "";
  const parts = words(command);
  if (parts[0] === "mynotes") {
    const [, verb, target] = parts;
    if (verb === "notes" && target && !target.startsWith("-")) return { kind: "notes", course: target, lessons: flag(parts, "--lessons") };
    if ((verb === "transcribe" || verb === "prepare") && target) {
      const lesson = LESSON_ID.exec(target);
      if (lesson?.[1] && lesson[2]) return { kind: "transcribe", course: lesson[1], lesson: lesson[2] };
    }
    if (verb === "context") return { kind: "instructions" };
    if (verb === "status" || verb === "doctor") return { kind: "check" };
    if (verb === "undo" && target) return { kind: "undo", course: target };
    if (verb === "capture" && (target === "sync" || target === "connect" || target === "subscribe")) return { kind: "calendar" };
  }
  if (tool.name === "Read" || tool.name === "view_file") {
    const file = tool.summary.split(/[\\/]/).at(-1) ?? tool.summary;
    return { kind: "read", file: file.replace(/^Read\s+/, "") };
  }
  return { kind: "command", text: tool.summary || tool.name };
}

/** "2 min", "45 s"; null below one second or when unknown. */
export function durationParts(ms: number | null): { minutes: number } | { seconds: number } | null {
  if (ms === null || ms < 1000) return null;
  const seconds = Math.round(ms / 1000);
  return seconds < 60 ? { seconds } : { minutes: Math.round(seconds / 60) };
}

// ---------------------------------------------------------------------------------------------
// Errors (problem + one fix) and the provider footer
// ---------------------------------------------------------------------------------------------

export type ErrorKind = "quota" | "auth" | "network" | "timeout" | "unavailable" | "interrupted" | "busy" | "other";
export type ErrorFix = "retry" | "signIn" | "settings" | "none";

export function errorView(error: TurnError): { kind: ErrorKind; fix: ErrorFix } {
  switch (error.code) {
    case "QUOTA":
      return { kind: "quota", fix: "none" };
    case "AUTH":
      return { kind: "auth", fix: "signIn" };
    case "NETWORK":
      return { kind: "network", fix: "retry" };
    case "TIMEOUT":
      return { kind: "timeout", fix: "retry" };
    case "UNAVAILABLE":
      return { kind: "unavailable", fix: "settings" };
    case "INTERRUPTED":
      return { kind: "interrupted", fix: "retry" };
    case "BUSY":
      return { kind: "busy", fix: "none" };
    default:
      return { kind: "other", fix: "retry" };
  }
}

export interface ProviderInfo {
  readonly name: string;
  readonly company: string;
}

const PROVIDERS: Record<string, ProviderInfo> = {
  claude: { name: "Claude Code", company: "Anthropic" },
  codex: { name: "Codex", company: "OpenAI" },
  agy: { name: "Antigravity", company: "Google" },
};

export function providerInfo(provider: string | null | undefined): ProviderInfo {
  return PROVIDERS[provider ?? ""] ?? PROVIDERS.claude ?? { name: "Claude Code", company: "Anthropic" };
}

// ---------------------------------------------------------------------------------------------
// Page context sent with each message: data, never instructions (server-side checked too)
// ---------------------------------------------------------------------------------------------

export interface PageContext {
  readonly course?: string;
  readonly lesson?: number;
  readonly lectureId?: string;
  readonly page?: string;
}

const COURSE_CODE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/;

export function pageContext(route: { name: string; code?: string; id?: string }, label: string): PageContext {
  const page = label.slice(0, 80);
  if (route.name === "course" && route.code && COURSE_CODE.test(route.code)) return { course: route.code, page };
  if (route.name === "lesson" && route.id) {
    const lesson = LESSON_ID.exec(route.id);
    if (lesson?.[1] && lesson[2]) return { course: lesson[1], lesson: Number(lesson[2]), lectureId: route.id, page };
  }
  return page ? { page } : {};
}
