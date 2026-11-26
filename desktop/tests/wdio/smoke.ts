/**
 * Real-app smoke test (Linux CI, under Xvfb): drive the built app through `tauri-driver` and the
 * W3C WebDriver protocol, wait for the React root to render, save one screenshot, then close.
 * Plain `fetch`, no WebDriver client library.
 *
 *   node tests/wdio/smoke.ts <app binary> <screenshot.png>
 *
 * Requires `tauri-driver` and `WebKitWebDriver` on PATH (ubuntu: `webkit2gtk-driver`). The app
 * gets the environment of this process, so MYNOTES_DESKTOP_PYTHON / MYNOTES_WORKSPACE select
 * its backend. Prints one JSON line; exits 1 on any failure.
 */
import { spawn } from "node:child_process";
import { writeFileSync } from "node:fs";

const DRIVER = "http://127.0.0.1:4444";
const RENDER_LIMIT_MS = 60_000;

interface WebDriverAnswer {
  value: unknown;
}

async function webdriver(method: "GET" | "POST" | "DELETE", path: string, body?: unknown): Promise<unknown> {
  const response = await fetch(`${DRIVER}${path}`, {
    method,
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const answer = (await response.json()) as WebDriverAnswer;
  if (!response.ok) throw new Error(`${method} ${path}: ${response.status} ${JSON.stringify(answer.value).slice(0, 300)}`);
  return answer.value;
}

const sleep = (ms: number) => new Promise((done) => setTimeout(done, ms));

async function waitFor(probe: () => Promise<boolean>, limitMs: number, what: string): Promise<void> {
  const deadline = Date.now() + limitMs;
  for (;;) {
    try {
      if (await probe()) return;
    } catch {
      // Not ready yet.
    }
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await sleep(250);
  }
}

async function main(): Promise<void> {
  const [application, screenshot] = process.argv.slice(2);
  if (!application || !screenshot) throw new Error("usage: smoke.ts <app binary> <screenshot.png>");
  const driver = spawn("tauri-driver", [], { stdio: ["ignore", "inherit", "inherit"] });
  const started = Date.now();
  try {
    await waitFor(async () => (await fetch(`${DRIVER}/status`)).ok, 30_000, "tauri-driver");
    const session = (await webdriver("POST", "/session", {
      capabilities: { alwaysMatch: { browserName: "wry", "tauri:options": { application } } },
    })) as { sessionId: string };
    const id = session.sessionId;
    try {
      await waitFor(
        async () => {
          const children = await webdriver("POST", `/session/${id}/execute/sync`, {
            script: "return document.getElementById('root')?.childElementCount ?? 0",
            args: [],
          });
          return typeof children === "number" && children > 0;
        },
        RENDER_LIMIT_MS,
        "the React root",
      );
      const title = await webdriver("GET", `/session/${id}/title`);
      const png = await webdriver("GET", `/session/${id}/screenshot`);
      if (typeof png !== "string") throw new Error("no screenshot data");
      writeFileSync(screenshot, Buffer.from(png, "base64"));
      console.warn(JSON.stringify({ smoke: "ok", title, screenshot, ms: Date.now() - started }));
    } finally {
      await webdriver("DELETE", `/session/${id}`).catch(() => undefined);
    }
  } finally {
    driver.kill("SIGTERM");
  }
}

main().catch((error: unknown) => {
  console.error(JSON.stringify({ smoke: "failed", error: error instanceof Error ? error.message : String(error) }));
  process.exit(1);
});
