/**
 * Entry point of the RPC layer.
 *
 * `connect()` picks the transport: Tauri inside the app window, the mock everywhere else
 * (Vite dev server, Playwright, Vitest). Mock options come from the URL:
 *   ?scenario=recording   fixture scenario (tests/fixtures/rpc/<method>@recording.json)
 *   ?simulate=0           freeze timers (screenshots)
 */
import { createRpcClient, type RpcClient } from "./client";
import { createTauriTransport, isTauriRuntime } from "./transports/tauri";

export type { RpcClient } from "./client";
export { RpcCode, RpcError } from "./errors";
export * from "./schema";

export interface ConnectOptions {
  transport?: "tauri" | "mock";
  scenario?: string;
  simulate?: boolean;
}

export function mockOptionsFromUrl(search: string): Required<Pick<ConnectOptions, "scenario" | "simulate">> {
  const params = new URLSearchParams(search);
  return {
    scenario: params.get("scenario") ?? "default",
    simulate: params.get("simulate") !== "0",
  };
}

export async function connect(options: ConnectOptions = {}): Promise<RpcClient> {
  const kind = options.transport ?? (isTauriRuntime() ? "tauri" : "mock");
  if (kind === "tauri") return createRpcClient(createTauriTransport());
  // Loaded on demand so fixtures never ship inside the Tauri bundle's main chunk.
  const { createMockTransport } = await import("./transports/mock");
  const fromUrl = typeof window === "undefined" ? { scenario: "default", simulate: true } : mockOptionsFromUrl(window.location.search);
  return createRpcClient(
    createMockTransport({ scenario: options.scenario ?? fromUrl.scenario, simulate: options.simulate ?? fromUrl.simulate }),
  );
}
