/**
 * Mock transport: serves `tests/fixtures/rpc/*.json` and simulates the server's behaviour.
 *
 * - Every call answers its fixture (see fixtures.ts for naming and scenario patches) after a
 *   small latency. A method without a fixture fails with "method not found", like the server.
 * - Timestamps are moved by whole days so the fixtures' anchor day is today (skipped when the
 *   clock is frozen on the anchor day, as in the screenshot harness).
 * - `capture.start` / `capture.stop` and `notes.run` change the mock state and emit the same
 *   notifications the server sends (`capture.changed`, `job.started`, `job.progress`, …),
 *   driven by timers. `simulate: false` keeps everything still (screenshots).
 */
import { RpcCode, RpcError } from "../errors";
import type { Transport } from "../transport";
import { dayOffset, resolveFixtures, shiftDates, type Json } from "./fixtures";
// onboarding (feat/desktop-onboarding)
import { onboardingMockHandlers } from "./onboardingMock";

export interface MockOptions {
  scenario: string;
  /** Run timers for job progress and capture state changes. */
  simulate: boolean;
  latencyMs: number;
  /** Delay between notes stages when simulating. */
  stageMs: number;
  now: () => Date;
  /** Raw fixture modules; defaults to the bundled tests/fixtures/rpc files. */
  modules?: Record<string, unknown>;
}

const bundled = import.meta.glob("../../../tests/fixtures/rpc/*.json", { eager: true, import: "default" });

const NOTES_STAGES = ["CHECKING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"] as const;

type Listener = (method: string, params: unknown) => void;
type JsonObject = { [key: string]: Json };

function isObject(value: Json | undefined): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function iso(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

export function createMockTransport(partial: Partial<MockOptions> = {}): Transport {
  const options: MockOptions = {
    scenario: "default",
    simulate: true,
    latencyMs: 120,
    stageMs: 2500,
    now: () => new Date(),
    ...partial,
  };
  const { results, settings } = resolveFixtures(options.modules ?? bundled, options.scenario);
  const offset = dayOffset(settings.anchorDate, options.now());
  const state = new Map<string, Json>();
  for (const [method, value] of results) state.set(method, shiftDates(value, offset));

  const listeners = new Set<Listener>();
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let jobCounter = 0;

  const emit = (method: string, params: Json) => {
    for (const listener of listeners) listener(method, structuredClone(params));
  };
  const later = (ms: number, run: () => void) => {
    if (!options.simulate) return;
    const timer = setTimeout(() => {
      timers.delete(timer);
      run();
    }, ms);
    timers.add(timer);
  };
  const envelopeData = (method: string): JsonObject | null => {
    const value = state.get(method);
    if (!isObject(value)) return null;
    const data = value.data;
    return isObject(data) ? data : null;
  };

  const handlers: Record<string, (params: unknown) => Json> = {
    // assistant (feat/desktop-assistant-ui): a streamed reply with one tool row.
    ...assistantMockHandlers({ state, later, emit, latencyMs: options.latencyMs }),
    // onboarding (feat/desktop-onboarding): setup, sign-in, course, calendar, microphone.
    ...onboardingMockHandlers({ state, simulate: options.simulate, now: options.now, stageMs: options.stageMs, later, emit }),

    "watch.subscribe": () => {
      const snapshot: JsonObject = {};
      const topics: Array<[string, string]> = [
        ["capture", "capture.dashboard"],
        ["queue", "queue.status"],
        ["library", "library.list"],
        ["config", "config.get"],
      ];
      for (const [topic, method] of topics) {
        const data = envelopeData(method);
        if (data) snapshot[topic] = data;
      }
      return snapshot;
    },

    "capture.start": () => {
      const dashboard = envelopeData("capture.dashboard");
      const requestID = `mock-${Date.now().toString(16)}`;
      later(options.latencyMs * 4, () => {
        if (!dashboard || !isObject(dashboard.runtime)) return;
        const now = options.now();
        const session: JsonObject = {
          id: `session-${requestID}`,
          event: { id: `recording:${requestID}`, courseCode: null, lessonNumber: null, title: "Recording" },
          status: "RECORDING",
          actualStart: iso(now),
          outputPath: null,
        };
        dashboard.runtime = { ...dashboard.runtime, recording: true, exporting: false, currentSession: session };
        emit("capture.changed", dashboard);
        emit("capture.requestCompleted", { requestID, status: "STARTED", message: null });
      });
      return { exitCode: 0, data: { status: "PENDING", requestID, message: null } };
    },

    "capture.stop": () => {
      const dashboard = envelopeData("capture.dashboard");
      const requestID = `mock-${Date.now().toString(16)}`;
      later(options.latencyMs * 2, () => {
        if (!dashboard || !isObject(dashboard.runtime)) return;
        dashboard.runtime = { ...dashboard.runtime, recording: false, exporting: true };
        emit("capture.changed", dashboard);
        later(1500, () => {
          if (!isObject(dashboard.runtime)) return;
          const current = dashboard.runtime.currentSession;
          const sessions = Array.isArray(dashboard.runtime.sessions) ? dashboard.runtime.sessions : [];
          const saved = isObject(current) ? [...sessions, { ...current, status: "COMPLETED" }] : sessions;
          dashboard.runtime = { ...dashboard.runtime, exporting: false, currentSession: null, sessions: saved };
          emit("capture.changed", dashboard);
          emit("capture.requestCompleted", { requestID, status: "STOPPED", message: null });
        });
      });
      return { exitCode: 0, data: { status: "PENDING", requestID, message: null } };
    },

    "notes.run": (params) => {
      const request = (params ?? {}) as { course?: string; lessons?: string };
      jobCounter += 1;
      const jobId = `mock-notes-${jobCounter}`;
      const course = request.course ?? "";
      const lectureIds = (request.lessons ?? "")
        .split(",")
        .filter(Boolean)
        .map((n) => `${course}-L${n.padStart(2, "0")}`);
      const startedAt = iso(options.now());
      const job: JsonObject = {
        jobId,
        kind: "notes",
        state: "running",
        stage: NOTES_STAGES[0],
        stageStartedAt: startedAt,
        startedAt,
        courseCode: course,
        lectureIds,
        result: null,
      };
      const list = state.get("job.list");
      if (isObject(list) && Array.isArray(list.jobs)) list.jobs.unshift(job);
      later(options.latencyMs, () => emit("job.started", job));
      NOTES_STAGES.slice(1).forEach((stage, index) => {
        later(options.stageMs * (index + 1), () => {
          const at = iso(options.now());
          job.stage = stage;
          job.stageStartedAt = at;
          emit("job.progress", { jobId, stage, detail: null, at });
        });
      });
      later(options.stageMs * NOTES_STAGES.length, () => {
        const at = iso(options.now());
        job.state = "succeeded";
        job.stage = "DONE";
        job.finishedAt = at;
        job.exitCode = 0;
        job.result = { status: "COMPLETED" };
        emit("job.finished", { jobId, state: "succeeded", exitCode: 0, result: { status: "COMPLETED" }, at });
      });
      return { jobId };
    },

    "job.get": (params) => {
      const jobId = (params as { jobId?: string } | undefined)?.jobId;
      const list = state.get("job.list");
      const found = isObject(list) && Array.isArray(list.jobs) ? list.jobs.find((job) => isObject(job) && job.jobId === jobId) : undefined;
      if (!found) throw new RpcError("job.get", RpcCode.InvalidParams, `Unknown job ${String(jobId)}`);
      return found;
    },
  };

  return {
    kind: "mock",
    async request(method, params) {
      await new Promise((resolve) => setTimeout(resolve, options.simulate ? options.latencyMs : 0));
      const handler = handlers[method];
      if (handler) return structuredClone(handler(params));
      const value = state.get(method);
      if (value === undefined) {
        throw new RpcError(method, RpcCode.MethodNotFound, `No mock fixture for ${method}`);
      }
      return structuredClone(value);
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    close() {
      for (const timer of timers) clearTimeout(timer);
      timers.clear();
      listeners.clear();
    },
  };
}

// ---------------------------------------------------------------------------------------------
// BEGIN assistant (feat/desktop-assistant-ui)
// `assistant.start` / `assistant.send` answer at once, then stream like serve_assistant.py:
// `assistant.delta` chunks, one `assistant.tool` row (running, then completed) and
// `assistant.done`. `assistant.cancel` ends the turn with `assistant.error` CANCELLED.
// Saved conversations come from the `assistant.get` fixture (scenario patches give the
// streaming, finished and error states); `assistant.list` lists it when it has turns.
// ---------------------------------------------------------------------------------------------

interface AssistantMockContext {
  state: Map<string, Json>;
  later: (ms: number, run: () => void) => void;
  emit: (method: string, params: Json) => void;
  latencyMs: number;
}

const ASSISTANT_REPLY = [
  "I checked the sources for **STAT lesson 3**: the transcript and the slides are ready.",
  "\n\nThe notes are now in your course notes. To see them, open the course and choose ",
  "*Open in Word*, or run:\n\n```\nmynotes notes STAT --lessons 3\n```",
];
const ASSISTANT_TOOL_COMMAND = "mynotes notes STAT --lessons 3 --json";

function text(value: Json | undefined, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function assistantMockHandlers({ state, later, emit, latencyMs }: AssistantMockContext): Record<string, (params: unknown) => Json> {
  const sessions = new Map<string, JsonObject>();
  const cancelled = new Set<string>();
  let counter = 0;
  const step = Math.max(latencyMs, 40);

  const fixture = (): JsonObject | null => {
    const value = state.get("assistant.get");
    return isObject(value) ? value : null;
  };
  const find = (sessionId: unknown): JsonObject | null => {
    if (typeof sessionId !== "string") return null;
    const saved = fixture();
    return sessions.get(sessionId) ?? (saved && saved.id === sessionId ? saved : null);
  };
  const turnsOf = (session: JsonObject): JsonObject[] => (Array.isArray(session.turns) ? session.turns.filter(isObject) : []);

  const stream = (session: JsonObject, turn: JsonObject) => {
    const sessionId = text(session.id);
    const turnId = text(turn.id);
    const key = `${sessionId}/${turnId}`;
    const ids = { sessionId, turnId };
    const tool: JsonObject = { toolId: "toolu_mock_1", name: "Bash", summary: "Prepare notes for STAT lesson 3", status: "running", command: ASSISTANT_TOOL_COMMAND, durationMs: null };
    const live = (run: () => void) => () => {
      if (!cancelled.has(key)) run();
    };
    later(step, live(() => {
      turn.text = ASSISTANT_REPLY[0] ?? "";
      emit("assistant.delta", { ...ids, text: ASSISTANT_REPLY[0] ?? "" });
    }));
    later(step * 2, live(() => {
      turn.tools = [{ ...tool }];
      emit("assistant.tool", { ...ids, ...tool });
    }));
    later(step * 4, live(() => {
      const done = { ...tool, status: "completed", durationMs: 132_000 };
      turn.tools = [done];
      emit("assistant.tool", { ...ids, ...done });
    }));
    ASSISTANT_REPLY.slice(1).forEach((chunk, index) => {
      later(step * (5 + index), live(() => {
        turn.text = `${text(turn.text)}${chunk}`;
        emit("assistant.delta", { ...ids, text: chunk });
      }));
    });
    later(step * (5 + ASSISTANT_REPLY.length), live(() => {
      turn.state = "COMPLETED";
      emit("assistant.done", { ...ids, text: text(turn.text), providerSession: "mock-provider-session" });
    }));
  };

  const begin = (session: JsonObject, params: unknown): JsonObject => {
    const message = isObject(params as Json) ? text((params as JsonObject).message) : "";
    const turns = turnsOf(session);
    const turn: JsonObject = { id: `turn-${turns.length + 1}`, message, state: "RUNNING", text: "", tools: [], error: null };
    session.turns = [...turns, turn];
    stream(session, turn);
    return { sessionId: text(session.id), turnId: text(turn.id) };
  };

  return {
    "assistant.start": (params) => {
      counter += 1;
      const id = `as-${counter.toString(16).padStart(16, "0")}`;
      const session: JsonObject = { id, provider: "claude", model: "", providerSession: null, resume: "native", title: "", turns: [] };
      sessions.set(id, session);
      return begin(session, params);
    },
    "assistant.send": (params) => {
      const session = find(isObject(params as Json) ? (params as JsonObject).sessionId : null);
      if (!session) throw new RpcError("assistant.send", -32007, "Unknown assistant session");
      return begin(session, params);
    },
    "assistant.cancel": (params) => {
      const session = find(isObject(params as Json) ? (params as JsonObject).sessionId : null);
      if (!session) throw new RpcError("assistant.cancel", -32007, "Unknown assistant session");
      const turn = turnsOf(session).at(-1);
      const running = turn?.state === "RUNNING";
      if (turn && running) {
        const ids = { sessionId: text(session.id), turnId: text(turn.id) };
        cancelled.add(`${ids.sessionId}/${ids.turnId}`);
        turn.state = "CANCELLED";
        later(step, () => emit("assistant.error", { ...ids, code: "CANCELLED", message: "Stopped" }));
      }
      return { sessionId: text(session.id), turnId: turn ? text(turn.id) : null, cancelRequested: running };
    },
    "assistant.list": () => {
      const saved = fixture();
      const all = [...sessions.values(), ...(saved && turnsOf(saved).length > 0 ? [saved] : [])];
      return {
        sessions: all.map((session) => {
          const turns = turnsOf(session);
          const last = turns.at(-1);
          return {
            id: text(session.id),
            title: text(session.title),
            provider: text(session.provider, "claude"),
            updatedAt: typeof session.updatedAt === "number" ? session.updatedAt : 0,
            turns: turns.length,
            state: last ? text(last.state) : null,
            running: last?.state === "RUNNING",
          };
        }),
      };
    },
    "assistant.get": (params) => {
      const session = find(isObject(params as Json) ? (params as JsonObject).sessionId : null);
      if (!session) throw new RpcError("assistant.get", -32007, "Unknown assistant session");
      return session;
    },
  };
}
// END assistant (feat/desktop-assistant-ui)
