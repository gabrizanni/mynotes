import { defineConfig, devices } from "@playwright/test";

/**
 * Headless Chromium against the Vite dev server with the mock transport.
 * - `npm run shots`: tests/screenshots/*.shots.ts → PNGs in tests/shots/ (git-ignored).
 * - `npm run test:e2e`: tests/e2e/*.spec.ts, mocked end-to-end flows.
 * Playwright starts the dev server on its own port and stops it when the run ends.
 */
// Parallel worktrees can pick another port: MYNOTES_E2E_PORT=5191 npm run shots
const PORT = Number(process.env.MYNOTES_E2E_PORT ?? 5181);

export default defineConfig({
  outputDir: "test-results",
  reporter: [["list"]],
  workers: 2,
  timeout: 30_000,
  use: {
    ...devices["Desktop Chrome"],
    baseURL: `http://localhost:${PORT}`,
    viewport: { width: 1200, height: 800 },
    deviceScaleFactor: 1,
    timezoneId: "Europe/Rome",
    headless: true,
  },
  projects: [
    { name: "shots", testDir: "tests/screenshots", testMatch: /.*\.shots\.ts$/ },
    { name: "e2e", testDir: "tests/e2e", testMatch: /.*\.spec\.ts$/ },
  ],
  webServer: {
    command: `npx vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "ignore",
  },
});
