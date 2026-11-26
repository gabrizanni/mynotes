import { defineConfig } from "vitest/config";

// Contract tests start the real `mynotes serve` (see serve.contract.test.ts): Node environment,
// one file at a time, generous timeouts for the first `uv run` sync.
export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/contract/**/*.test.ts"],
    root: new URL("../..", import.meta.url).pathname,
    fileParallelism: false,
    testTimeout: 120_000,
    hookTimeout: 240_000,
  },
});
