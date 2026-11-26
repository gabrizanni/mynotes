import { createRpcClient, type RpcClient } from "../../src/rpc/client";
import { createMockTransport } from "../../src/rpc/transports/mock";
import { createAppStore, initialAppState, type AppStore } from "../../src/state";

/** A store fed by the fixture mock, frozen (no timers) unless `simulate` is set. */
export function mockApp(scenario = "default", options: { simulate?: boolean; now?: Date } = {}): { app: AppStore; rpc: RpcClient } {
  const now = options.now ?? new Date("2026-11-26T09:42:00Z");
  const rpc = createRpcClient(
    createMockTransport({ scenario, simulate: options.simulate ?? false, latencyMs: 0, stageMs: 10, now: () => now }),
  );
  return { app: createAppStore(rpc, initialAppState()), rpc };
}

export async function loadAll(app: AppStore): Promise<void> {
  const { capture, queue, library, config, jobs } = app.actions;
  await Promise.all([capture.refresh(), queue.refresh(), library.refresh(), config.refresh(), jobs.refresh()]);
}
