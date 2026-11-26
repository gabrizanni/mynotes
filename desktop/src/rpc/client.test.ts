import { describe, expect, it, vi } from "vitest";

import { createRpcClient } from "./client";
import { RpcCode, RpcError } from "./errors";
import type { Transport } from "./transport";

function fakeTransport(answer: (method: string, params: unknown) => unknown) {
  let listener: ((method: string, params: unknown) => void) | null = null;
  const transport: Transport = {
    kind: "mock",
    request: (method, params) => Promise.resolve(answer(method, params)),
    subscribe(next) {
      listener = next;
      return () => {
        listener = null;
      };
    },
    close: vi.fn(),
  };
  return { transport, push: (method: string, params: unknown) => listener?.(method, params) };
}

describe("rpc client", () => {
  it("parses results with zod and keeps unmodeled fields", async () => {
    const { transport } = fakeTransport(() => ({ protocol: 1, methods: [], futureField: "kept" }));
    const result = await createRpcClient(transport).call("system.hello", { protocol: 1 });
    expect(result.protocol).toBe(1);
    expect((result as Record<string, unknown>).futureField).toBe("kept");
  });

  it("rejects a result in the wrong shape with InvalidResult", async () => {
    const { transport } = fakeTransport(() => ({ exitCode: 0, data: { schemaVersion: 2 } }));
    await expect(createRpcClient(transport).call("capture.dashboard")).rejects.toMatchObject({ code: RpcCode.InvalidResult });
  });

  it("refuses invalid params before they reach the transport", async () => {
    const request = vi.fn();
    const { transport } = fakeTransport(request);
    const client = createRpcClient(transport);
    // @ts-expect-error: quality must be one of the three backend values.
    await expect(client.call("notes.run", { course: "STAT", lessons: "7", quality: "best" })).rejects.toBeInstanceOf(RpcError);
    expect(request).not.toHaveBeenCalled();
  });

  it("normalizes transport errors into RpcError", async () => {
    const transport: Transport = {
      kind: "tauri",
      request: () => Promise.reject(Object.assign(new Error("busy"), { code: RpcCode.Busy })),
      subscribe: () => () => undefined,
      close: () => undefined,
    };
    const error = await createRpcClient(transport).call("queue.status").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(RpcError);
    expect((error as RpcError).transient).toBe(true);
  });

  it("delivers valid notifications and drops invalid ones", () => {
    const { transport, push } = fakeTransport(() => null);
    const client = createRpcClient(transport);
    const progress = vi.fn();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    client.on("job.progress", progress);
    push("job.progress", { jobId: "j1", stage: "GENERATING" });
    push("job.progress", { stage: "GENERATING" });
    push("unknown.event", {});
    expect(progress).toHaveBeenCalledTimes(1);
    expect(progress).toHaveBeenCalledWith(expect.objectContaining({ jobId: "j1", stage: "GENERATING" }));
    expect(warn).toHaveBeenCalledTimes(1);
  });
});
