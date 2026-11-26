/**
 * Real-app smoke test (Linux CI, under Xvfb): drive the built app through `tauri-driver` and the
 * W3C WebDriver protocol, wait until the window shows readable text (the React root rendered,
 * the first heading fully opaque), save one screenshot, then close. Plain `fetch`, no WebDriver
 * client library.
 *
 * It fails when the window shows no text, and always prints what the page looked like: visible
 * text length, route, first heading and its effective opacity (product over its ancestors), the
 * nearest animated ancestor's opacity and animation state, and the errors the window collected
 * (`window.__mynotesErrors`, see src/app/crashGuard.ts). A blank screenshot can then be told
 * apart: an animation held at opacity 0, an error screen, or nothing rendered at all.
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
/** After the root renders, how long the first heading may take to become fully visible. */
const VISIBLE_LIMIT_MS = 15_000;

/** What the page shows, computed in the webview. */
const PAGE_STATE = `
  const effective = (element) => {
    let value = 1;
    for (let node = element; node; node = node.parentElement) value *= Number(getComputedStyle(node).opacity);
    return value;
  };
  const heading = document.querySelector("h1, h2");
  let animated = heading;
  while (animated && getComputedStyle(animated).animationName === "none") animated = animated.parentElement;
  const style = animated ? getComputedStyle(animated) : null;
  return {
    textLength: (document.body.innerText || "").trim().length,
    route: location.hash,
    heading: heading ? (heading.textContent || "").trim().slice(0, 80) : null,
    headingOpacity: heading ? effective(heading) : null,
    animated: style ? { opacity: style.opacity, animation: style.animationName, state: style.animationPlayState } : null,
    crashScreen: document.querySelector("[data-crash-screen]") !== null,
    errors: window.__mynotesErrors || [],
  };
`;

interface PageState {
  textLength: number;
  route: string;
  heading: string | null;
  headingOpacity: number | null;
  animated: { opacity: string; animation: string; state: string } | null;
  crashScreen: boolean;
  errors: unknown[];
}

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
    const pageState = async () =>
      (await webdriver("POST", `/session/${id}/execute/sync`, { script: PAGE_STATE, args: [] })) as PageState;
    let state: PageState | null = null;
    const shoot = async () => {
      const png = await webdriver("GET", `/session/${id}/screenshot`);
      if (typeof png !== "string") throw new Error("no screenshot data");
      writeFileSync(screenshot, Buffer.from(png, "base64"));
    };
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
      try {
        await waitFor(
          async () => {
            state = await pageState();
            return state.textLength > 0 && state.headingOpacity === 1;
          },
          VISIBLE_LIMIT_MS,
          "readable text and a fully visible first heading",
        );
      } catch (error) {
        await shoot().catch(() => undefined); // The evidence, even when the check fails.
        throw new Error(`${error instanceof Error ? error.message : String(error)}; page: ${JSON.stringify(state)}`);
      }
      const title = await webdriver("GET", `/session/${id}/title`);
      await shoot();
      console.warn(JSON.stringify({ smoke: "ok", title, screenshot, page: state, ms: Date.now() - started }));
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
