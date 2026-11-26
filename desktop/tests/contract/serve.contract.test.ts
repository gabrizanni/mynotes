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
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
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
const SHELL_METHODS: readonly MethodName[] = ["setup.status"];
/** Not called: they start an agent (`notes.run`), the recorder (`capture.start`) or cancel one. */
const NOT_CALLED: readonly MethodName[] = ["notes.run", "capture.start"];

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
  for (const lesson of [1, 2]) writeFileSync(join(dirs.recordings, `DEMO-L0${lesson}.wav`), silentWav());
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

/** Params the app sends (src/state/slices/*.ts) or the shape its schema demands. */
function paramsFor(method: MethodName): unknown {
  const params: Partial<Record<MethodName, unknown>> = {
    "system.hello": { protocol: 1 },
    "session.open": { visible: true },
    "session.setVisible": { visible: false },
    "watch.subscribe": { topics: ["capture", "queue", "library", "config"] },
    "doctor.get": {},
    "provider.status": { provider: "claude" },
    // What slices/capture.ts sends.
    "capture.stop": { waitSeconds: 0 },
    "notes.plan": { course: courseCode, lessons: "1", quality: "standard" },
    "notes.batchStatus": { batchId: "batch-missing" },
    "job.get": { jobId },
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
  "watch.subscribe": { drift: "answers {topics, snapshots: {<topic>: {topic, hash, data}}}; the app reads result.capture etc." },
  "doctor.get": { parses: true },
  "provider.status": { drift: "answers {exitCode, data: {…}}; the app expects {provider, status} at the top" },
  "config.get": { parses: true },
  "capture.dashboard": { parses: true },
  "capture.stop": { error: RpcCode.InvalidParams, why: "the server takes no waitSeconds (it always passes --wait-seconds 0)" },
  "queue.status": { parses: true },
  "library.list": { parses: true },
  "notes.plan": { error: RpcCode.CliError, why: "no provider in the fixture workspace, and notesParams has no provider field" },
  "notes.batchStatus": { error: RpcCode.CliError, why: "unknown batch id" },
  "job.list": { drift: "Job records have id/method/state RUNNING…COMPLETED and numeric times; the app expects jobId/kind/state lower-case/startedAt string" },
  "job.get": { drift: "same Job record shape as job.list" },
  "job.cancel": { parses: true },
};

const NOTIFICATION_EXPECTED: Partial<Record<NotificationName, Expectation>> = {
  "capture.changed": { drift: "a watch Snapshot {topic, hash, data}; the app parses a bare dashboard" },
  "job.started": { drift: "{jobId, method, params, pid}; the app expects a full Job (kind, state, startedAt)" },
  "job.finished": { drift: "result is {exitCode, data: {status}}; the app reads result.status" },
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
    if (method === "watch.subscribe" && !(result as { capture?: unknown }).capture) {
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
    const missing = (Object.keys(methods) as MethodName[]).filter((m) => !SHELL_METHODS.includes(m) && !server.has(m));
    expect(missing).toEqual([]);
    for (const method of SHELL_METHODS) expect(server.has(method), `${method} belongs to the shell`).toBe(false);
  });

  const called = (Object.keys(methods) as MethodName[]).filter((m) => !SHELL_METHODS.includes(m) && !NOT_CALLED.includes(m));
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
