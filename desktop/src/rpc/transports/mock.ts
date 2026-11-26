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
import { mockJobs, type JsonObject } from "./mockJobs";
import { createNotesMock } from "./mockNotes";
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

const WATCH_TOPICS = new Map([
  ["capture", "capture.dashboard"],
  ["queue", "queue.status"],
  ["library", "library.list"],
  ["config", "config.get"],
]);

type Listener = (method: string, params: unknown) => void;

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

  // `<topic>.changed` travels as a watch snapshot, like the server's (serve_watch.py).
  let revision = 0;
  const emit = (method: string, params: Json) => {
    const topic = method.endsWith(".changed") ? method.slice(0, -".changed".length) : null;
    revision += 1;
    const payload: Json = topic && WATCH_TOPICS.has(topic) ? { topic, hash: `mock-${revision}`, data: params } : params;
    for (const listener of listeners) listener(method, structuredClone(payload));
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

  const jobs = mockJobs({ state, emit, now: options.now });

  const handlers: Record<string, (params: unknown) => Json> = {
    // Onboarding: setup, sign-in, course, calendar, microphone.
    ...onboardingMockHandlers({ state, simulate: options.simulate, now: options.now, stageMs: options.stageMs, later, emit, jobs }),

    "watch.subscribe": (params) => {
      const asked = (params as { topics?: string[] } | undefined)?.topics ?? [...WATCH_TOPICS.keys()];
      const snapshots: JsonObject = {};
      for (const topic of asked) {
        const method = WATCH_TOPICS.get(topic);
        const data = method ? envelopeData(method) : null;
        if (method) snapshots[topic] = data ? { topic, hash: `mock-${revision}`, data } : { topic, hash: "", data: null, error: { message: "No snapshot" } };
      }
      return { topics: [...asked].sort(), snapshots };
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

    // A short job; the cached catalog fixture stays what `models.catalog` answers afterwards.
    "models.refresh": (params) => {
      jobCounter += 1;
      const ref = jobs.start(`mock-models-${jobCounter}`, "models.refresh", (params ?? {}) as JsonObject, (run) => run());
      const timer = setTimeout(() => {
        timers.delete(timer);
        const job = jobs.find(ref.jobId);
        if (job) jobs.finish(job, "COMPLETED", 0, envelopeData("models.catalog"));
      }, options.latencyMs);
      timers.add(timer);
      return ref;
    },

    "job.get": (params) => {
      const jobId = (params as { jobId?: string } | undefined)?.jobId ?? "";
      const found = jobs.find(jobId);
      if (!found) throw new RpcError("job.get", RpcCode.UnknownJob, `Unknown job ${jobId}`);
      return found;
    },

    // The mock never blocks: it answers the record as it is now.
    "job.wait": (params) => {
      const jobId = (params as { jobId?: string } | undefined)?.jobId ?? "";
      const found = jobs.find(jobId);
      if (!found) throw new RpcError("job.wait", RpcCode.UnknownJob, `Unknown job ${jobId}`);
      return found;
    },
  };

  // Generate notes flow, transcription and the lesson transcript.
  const notesMock = createNotesMock({ state, emit, jobs, simulate: options.simulate, stageMs: options.stageMs, latencyMs: options.latencyMs, now: options.now });
  Object.assign(handlers, notesMock.handlers);

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
      notesMock.dispose();
      for (const timer of timers) clearTimeout(timer);
      timers.clear();
      listeners.clear();
    },
  };
}
