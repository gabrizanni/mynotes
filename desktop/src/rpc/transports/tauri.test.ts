import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn<(command: string, args?: unknown) => Promise<unknown>>();
type Handler = (event: { payload: unknown }) => void;
const handlers = new Map<string, Handler>();
const unlisten = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({ invoke: (command: string, args?: unknown) => invoke(command, args) }));
vi.mock("@tauri-apps/api/event", () => ({
  listen: (event: string, handler: Handler) => {
    handlers.set(event, handler);
    return Promise.resolve(unlisten);
  },
}));

const { createRpcClient } = await import("../client");
const { RpcCode, RpcError } = await import("../errors");
const { createTauriTransport, NOTIFICATION_EVENT } = await import("./tauri");

beforeEach(() => {
  invoke.mockReset();
  handlers.clear();
  unlisten.mockReset();
});

describe("tauri transport", () => {
  it("sends backend methods through rpc_call and setup.status to the shell", async () => {
    invoke.mockImplementation((command) =>
      Promise.resolve(command === "setup_status" ? { state: "idle", reason: "RUNTIME_MISSING" } : { ok: true, uptimeSeconds: 3 }),
    );
    const rpc = createRpcClient(createTauriTransport());
    await rpc.call("system.ping");
    expect(invoke).toHaveBeenLastCalledWith("rpc_call", { method: "system.ping", params: null });
    const setup = await rpc.call("setup.status");
    expect(invoke).toHaveBeenLastCalledWith("setup_status", undefined);
    expect(setup.state).toBe("idle");
  });

  it("keeps the JSON-RPC code of a Rust CommandError", async () => {
    invoke.mockRejectedValue({ code: RpcCode.BackendRestarted, message: "MyNotes restarted", data: null });
    const rpc = createRpcClient(createTauriTransport());
    const error = await rpc.call("config.get").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(RpcError);
    expect((error as InstanceType<typeof RpcError>).code).toBe(RpcCode.BackendRestarted);
    expect((error as InstanceType<typeof RpcError>).transient).toBe(true);
  });

  it("forwards notification events and stops after unsubscribe", async () => {
    const transport = createTauriTransport();
    const seen: string[] = [];
    const stop = transport.subscribe((method) => seen.push(method));
    const emit = handlers.get(NOTIFICATION_EVENT);
    expect(emit).toBeDefined();
    emit?.({ payload: { method: "capture.changed", params: {} } });
    emit?.({ payload: "not a notification" });
    stop();
    emit?.({ payload: { method: "queue.changed", params: {} } });
    expect(seen).toEqual(["capture.changed"]);
    await Promise.resolve();
    expect(unlisten).toHaveBeenCalledTimes(1);
  });
});
