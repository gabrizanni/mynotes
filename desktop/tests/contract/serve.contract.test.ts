/**
 * Contract tests: the app's zod schemas (`src/rpc/schema.ts`) against the real `mynotes serve`.
 *
 * - The server runs from a serve checkout (`MYNOTES_SERVE_REPO`, else this repository once the
 *   server is in it) on a temporary workspace, with a private HOME, runtime folders and a PATH
 *   without agent CLIs. The workspace is built through the API itself (course, schedule,
 *   recordings folder, queue settings, one finished job).
 * - Every method in the app's table that the server answers without an agent, a recorder or the
 *   network is called with the params the app sends, through the app's own `RpcClient`.
 * - `EXPECTED` records, per method, whether the app's schema accepts the real answer today. A
 *   `drift` entry is a known mismatch: the test fails when it disappears, so the entry is
 *   removed together with the fix (nothing silently starts or stops parsing).
 * - `MYNOTES_CONTRACT_RECORD=1` writes every real answer, scrubbed of temporary paths, to
 *   `tests/fixtures/rpc-real/`. They do not replace `tests/fixtures/rpc/`: the mock fixtures
 *   describe the screens' scenarios (a recording lesson, a notes run, …) that an empty fixture
 *   workspace cannot produce.
 */
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { createRpcClient, type RpcClient } from "../../src/rpc/client";
import { RpcCode, RpcError } from "../../src/rpc/errors";
import { methods, notifications, type MethodName, type NotificationName } from "../../src/rpc/schema";
import { createStdioTransport, type StdioTransport } from "./stdioTransport";

const HERE = dirname(fileURLToPath(import.meta.url));
const DESKTOP = resolve(HERE, "../..");
const RECORD = process.env.MYNOTES_CONTRACT_RECORD === "1";
const RECORD_DIR = join(DESKTOP, "tests/fixtures/rpc-real");
/** Placeholder for the temporary root in recorded fixtures. */
const SCRUBBED_ROOT = "/tmp/mynotes-contract";

/** Answered by the Rust shell (src-tauri/src/installer.rs), never by the server. */
const SHELL_METHODS: readonly MethodName[] = ["setup.status", "setup.run"];
/**
 * Modelled by the app but not in docs/SERVE.md yet: the server must not know them. When it
 * does, this test fails, so the entry moves to the called methods with its real shape.
 */
const PENDING_METHODS: readonly MethodName[] = ["library.transcript", "notes.publish"];
/** Not called here, with the reason: an agent, the recorder, the network, a download, or OS state. */
const NOT_CALLED: Partial<Record<MethodName, string>> = {
  "notes.run": "starts an agent",
  "lesson.process": "starts an agent",
  "provider.login": "starts an agent sign-in",
  "capture.start": "starts the recorder",
  "capture.test": "starts the recorder",
  "capture.recover": "asks the recorder",
  "capture.confirmTest": "needs a recorded test file",
  "capture.enable": "installs the recorder's background agent",
  "capture.disable": "removes the recorder's background agent",
  "capture.connect": "fetches a calendar over the network",
  "capture.sync": "called while seeding (a real job)",
  "capture.unsubscribe": "needs a connected calendar",
  "capture.setImportMode": "needs a connected calendar",
  "models.refresh": "asks the provider over the network",
  "transcription.run": "starts speech recognition",
  "speech.installModel": "downloads the speech model",
  "speech.verify": "starts speech recognition",
  "word.verifyRenderer": "starts LibreOffice",
  "queue.enqueue": "starts the transcription runner",
  "queue.job": "needs a queue job",
  "library.associate": "needs an imported recording id",
  "library.finish": "needs an imported recording id",
  "library.preferAudio": "needs an imported recording id",
  "library.editLesson": "needs a library lesson id",
  "library.importAudio": "copies audio into the library (covered by inspectAudio)",
  "preferences.saveModels": "needs a provider and a saved revision",
  "notes.restore": "needs a saved backup",
};

type Expectation = { parses: true } | { drift: string } | { error: number; why: string };

function serveCheckout(): string {
  const explicit = process.env.MYNOTES_SERVE_REPO;
  if (explicit) return resolve(explicit);
  const repo = resolve(DESKTOP, "..");
  if (existsSync(join(repo, "src/mynotes/serve.py"))) return repo;
  throw new Error("No serve checkout: set MYNOTES_SERVE_REPO to a checkout that has `mynotes serve`.");
}

/** A 1-second silent 16 kHz mono WAV. */
function silentWav(seconds = 1): Buffer {
  const rate = 16_000;
  const data = Buffer.alloc(rate * 2 * seconds);
  const header = Buffer.alloc(44);
  header.write("RIFF", 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write("WAVEfmt ", 8);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write("data", 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

function scrub(value: unknown, root: string): unknown {
  return JSON.parse(JSON.stringify(value).split(root).join(SCRUBBED_ROOT)) as unknown;
}

interface Fixture {
  root: string;
  workspace: string;
  env: NodeJS.ProcessEnv;
  python: string;
}

function buildEnvironment(repo: string): Fixture {
  // The serve checkout's interpreter, resolved (and synced) with the developer's own uv setup.
  const python = execFileSync("uv", ["run", "--project", repo, "python", "-c", "import sys; print(sys.executable)"], {
    encoding: "utf8",
  }).trim();
  const root = mkdtempSync(join(tmpdir(), "mynotes-contract-"));
  // Not `home`: `/home/<name>/` reads as a personal path to the public-tree audit.
  const dirs = { home: join(root, "user-home"), bin: join(root, "bin"), workspace: join(root, "workspace"), recordings: join(root, "recordings") };
  for (const dir of Object.values(dirs)) mkdirSync(dir, { recursive: true });
  const env: NodeJS.ProcessEnv = {
    HOME: dirs.home,
    USERPROFILE: dirs.home,
    PATH: [dirs.bin, "/usr/bin", "/bin"].join(":"),
    LANG: "C.UTF-8",
    MYNOTES_HOME: join(root, "runtime"),
    MYNOTES_CAPTURE_HOME: join(root, "capture-runtime"),
    TMPDIR: tmpdir(),
  };
  execFileSync(python, ["-I", "-X", "utf8", "-m", "mynotes", "init", "--workspace", dirs.workspace], { env, stdio: "ignore" });
  // An hour old: import checks refuse files that may still be copying.
  const settled = new Date(Date.now() - 3_600_000);
  for (const lesson of [1, 2]) {
    const path = join(dirs.recordings, `DEMO-L0${lesson}.wav`);
    writeFileSync(path, silentWav());
    utimesSync(path, settled, settled);
  }
  return { root, workspace: dirs.workspace, env, python };
}

let fixture: Fixture;
let transport: StdioTransport;
let rpc: RpcClient;
let courseCode = "";
let jobId = "";
const seen = new Map<string, unknown[]>();
const recorded = new Map<string, unknown>();

/** Raw call for workspace seeding (methods the app does not model yet). */
async function seed(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const answer = await transport.request(method, params);
  recorded.set(method, answer);
  return answer as Record<string, unknown>;
}

async function waitFor<T>(probe: () => T | undefined, limitMs: number): Promise<T | undefined> {
  const deadline = Date.now() + limitMs;
  while (Date.now() < deadline) {
    const value = probe();
    if (value !== undefined) return value;
    await new Promise((done) => setTimeout(done, 100));
  }
  return probe();
}

beforeAll(async () => {
  fixture = buildEnvironment(serveCheckout());
  transport = createStdioTransport({
    command: fixture.python,
    args: ["-I", "-X", "utf8", "-m", "mynotes", "serve", "--stdio", "--workspace", fixture.workspace],
    env: fixture.env,
    callTimeoutMs: 90_000,
  });
  transport.subscribe((method, params) => {
    const list = seen.get(method) ?? [];
    list.push(params);
    seen.set(method, list);
  });
  // Record each raw answer under its method, whatever the schema makes of it.
  rpc = createRpcClient({
    ...transport,
    request: async (method, params) => {
      const answer = await transport.request(method, params);
      recorded.set(method, answer);
      return answer;
    },
  });
  await rpc.call("system.hello", { protocol: 1 });

  const added = await seed("course.add", { name: "Synthetic Systems", deferContext: true });
  const course = (added.data as { course?: { code?: string } } | undefined)?.course;
  courseCode = course?.code ?? "";
  await seed("capture.configure", { config: {} });
  await seed("capture.import", {
    events: [1, 2, 3].map((lesson) => ({
      courseCode,
      lessonNumber: lesson,
      title: `Lesson ${lesson}`,
      startsAt: `2030-03-0${lesson}T09:00:00Z`,
      endsAt: `2030-03-0${lesson}T10:30:00Z`,
    })),
  });
  await seed("library.folder", { path: join(fixture.root, "recordings") });
  await seed("queue.configure", { parallel: 1 });
  // One real job, so job.list / job.get / job notifications carry a record.
  const started = await seed("capture.sync", { ifDue: true });
  jobId = typeof started.jobId === "string" ? started.jobId : "";
  if (jobId) await seed("job.wait", { jobId, timeoutSeconds: 60 });
}, 240_000);

afterAll(async () => {
  const code = await transport?.shutdown();
  if (RECORD && fixture) {
    mkdirSync(RECORD_DIR, { recursive: true });
    for (const [name, value] of [...recorded, ...[...seen].map(([n, list]) => [`${n}.notification`, list[0]] as const)]) {
      writeFileSync(join(RECORD_DIR, `${name}.json`), `${JSON.stringify(scrub(value, fixture.root), null, 2)}\n`);
    }
  }
  if (fixture) rmSync(fixture.root, { recursive: true, force: true });
  expect(code, transport?.stderr()).toBe(0);
}, 60_000);

/** Params the app sends (src/state/slices/*.ts, the features) on the seeded workspace. */
function paramsFor(method: MethodName): unknown {
  const event = `static:${courseCode}-L01`;
  const params: Partial<Record<MethodName, unknown>> = {
    "system.hello": { protocol: 1 },
    "session.open": { visible: true },
    "session.setVisible": { visible: false },
    "watch.subscribe": { topics: ["capture", "queue", "library", "config"] },
    "doctor.get": {},
    "provider.status": { provider: "claude" },
    "models.catalog": { provider: "claude" },
    "profile.save": { skip: true },
    "course.add": { name: "Second Synthetic Course", deferContext: true },
    "course.edit": { code: courseCode, course: { name: "Synthetic Systems II" } },
    "course.link": { code: courseCode, path: join(fixture.root, "recordings"), dryRun: true },
    "style.show": { course: courseCode },
    "style.edit": { course: courseCode, reset: true },
    "style.preview": { course: courseCode },
    "capture.configure": { config: { recordBeforeSeconds: 60 } },
    "capture.addLesson": { lesson: { courseCode, lessonNumber: 9, title: "Extra lesson", startsAt: "2030-03-09T09:00:00Z", endsAt: "2030-03-09T10:30:00Z", timezone: "UTC" } },
    "capture.editLesson": { event, patch: { title: "Lesson one, renamed" } },
    "capture.resetLesson": { event },
    "queue.configure": { parallel: 1 },
    "library.list": {},
    "library.inspectAudio": { paths: [join(fixture.root, "recordings", "DEMO-L01.wav")] },
    "notes.plan": { course: courseCode, lessons: "1", quality: "standard" },
    "notes.batchStatus": { batchId: "batch-missing" },
    "notes.history": { course: courseCode },
    "notes.undo": { course: courseCode },
    "job.list": {},
    "job.get": { jobId },
    "job.wait": { jobId, timeoutSeconds: 5 },
    "job.cancel": { jobId },
  };
  return params[method];
}

/**
 * What today's schemas make of the real answers (server of the serve checkout, protocol 1).
 * Drift entries name the mismatch for whoever updates src/rpc/schema.ts.
 */
const EXPECTED: Record<string, Expectation> = {
  "system.hello": { parses: true },
  "system.ping": { parses: true },
  "session.open": { parses: true },
  "session.setVisible": { parses: true },
  "watch.subscribe": { parses: true },
  "doctor.get": { parses: true },
  "provider.status": { parses: true },
  "config.get": { parses: true },
  "profile.save": { parses: true },
  "preferences.get": { parses: true },
  "models.catalog": { parses: true },
  "course.add": { parses: true },
  "course.edit": { parses: true },
  "course.link": { parses: true },
  "style.show": { parses: true },
  "style.edit": { parses: true },
  "style.preview": { parses: true },
  "capture.dashboard": { parses: true },
  "capture.stop": { parses: true },
  "capture.pause": { parses: true },
  "capture.resume": { parses: true },
  "capture.configure": { parses: true },
  "capture.addLesson": { parses: true },
  "capture.editLesson": { parses: true },
  "capture.resetLesson": { parses: true },
  "queue.status": { parses: true },
  "queue.pause": { parses: true },
  "queue.resume": { parses: true },
  "queue.clear": { parses: true },
  "queue.configure": { parses: true },
  "library.list": { parses: true },
  "library.inspectAudio": { parses: true },
  "library.verify": { parses: true },
  "notes.plan": { error: RpcCode.CliError, why: "no provider in the fixture workspace" },
  "notes.batchStatus": { error: RpcCode.CliError, why: "unknown batch id" },
  "notes.history": { parses: true },
  "notes.undo": { error: RpcCode.CliError, why: "a fresh course has no update to undo" },
  "job.list": { parses: true },
  "job.get": { parses: true },
  "job.wait": { parses: true },
  "job.cancel": { parses: true },
};

const NOTIFICATION_EXPECTED: Partial<Record<NotificationName, Expectation>> = {
  "capture.changed": { parses: true },
  "job.started": { parses: true },
  "job.finished": { parses: true },
};

interface Outcome {
  kind: "parsed" | "rejected" | "error";
  code?: number;
  message?: string;
}

async function outcomeOf(method: MethodName): Promise<Outcome> {
  // Params come from paramsFor; the client checks them with the method's own schema.
  const call = rpc.call.bind(rpc) as (name: MethodName, params?: unknown) => Promise<unknown>;
  try {
    const result = await call(method, paramsFor(method));
    if (method === "watch.subscribe" && !(result as { snapshots?: { capture?: { data?: unknown } } }).snapshots?.capture?.data) {
      return { kind: "rejected", message: "no capture snapshot where the app reads it" };
    }
    return { kind: "parsed" };
  } catch (error) {
    if (!(error instanceof RpcError)) throw error;
    if (error.code === RpcCode.InvalidResult) return { kind: "rejected", code: error.code, message: JSON.stringify(error.data).slice(0, 400) };
    return { kind: "error", code: error.code, message: error.message };
  }
}

function check(name: string, outcome: Outcome, expectation: Expectation | undefined) {
  expect(expectation, `${name}: add an EXPECTED entry`).toBeDefined();
  if (!expectation) return;
  if ("parses" in expectation) expect(outcome, `${name} should parse`).toEqual({ kind: "parsed" });
  if ("drift" in expectation) expect(outcome.kind, `${name}: drift fixed? remove the entry (${expectation.drift})`).toBe("rejected");
  if ("error" in expectation) expect(outcome.code, `${name}: ${expectation.why}: ${outcome.message ?? ""}`).toBe(expectation.error);
}

describe("mynotes serve contract (protocol 1)", () => {
  it("knows every backend method the app calls", async () => {
    const hello = await rpc.call("system.hello", { protocol: 1 });
    const server = new Set(hello.methods ?? []);
    const missing = (Object.keys(methods) as MethodName[]).filter((m) => !SHELL_METHODS.includes(m) && !PENDING_METHODS.includes(m) && !server.has(m));
    expect(missing).toEqual([]);
    for (const method of SHELL_METHODS) expect(server.has(method), `${method} belongs to the shell`).toBe(false);
    for (const method of PENDING_METHODS) expect(server.has(method), `${method} now exists: model its real shape`).toBe(false);
  });

  const skipped = new Set<MethodName>([...SHELL_METHODS, ...PENDING_METHODS, ...(Object.keys(NOT_CALLED) as MethodName[])]);
  const called = (Object.keys(methods) as MethodName[]).filter((m) => !skipped.has(m));
  it.each(called)("%s answers in the shape the app expects", async (method) => {
    check(method, await outcomeOf(method), EXPECTED[method]);
  }, 120_000);

  it("seeded a workspace with a course and a finished job", () => {
    expect(courseCode).not.toBe("");
    expect(jobId).toMatch(/^job-/);
  });

  it("notifications match the app's notification schemas", async () => {
    await rpc.call("watch.subscribe", { topics: ["capture"] });
    await transport.request("capture.pause", undefined);
    await waitFor(() => seen.get("capture.changed")?.[0], 15_000);
    await transport.request("capture.resume", undefined);
    for (const [name, expectation] of Object.entries(NOTIFICATION_EXPECTED) as [NotificationName, Expectation][]) {
      const payload = seen.get(name)?.[0];
      expect(payload, `${name} was not received`).toBeDefined();
      const parsed = notifications[name].safeParse(payload);
      check(name, parsed.success ? { kind: "parsed" } : { kind: "rejected" }, expectation);
    }
  }, 60_000);
});
