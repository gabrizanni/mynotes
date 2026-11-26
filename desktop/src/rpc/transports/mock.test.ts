import { afterEach, describe, expect, it, vi } from "vitest";

import { createRpcClient } from "../client";
import { RpcCode } from "../errors";
import { createMockTransport } from "./mock";

afterEach(() => {
  vi.useRealTimers();
});

describe("mock transport", () => {
  it("serves scenario patches over the base fixture", async () => {
    const now = () => new Date("2026-11-26T08:47:00Z");
    const live = createRpcClient(createMockTransport({ scenario: "recording", simulate: false, latencyMs: 0, now }));
    const { data } = await live.call("capture.dashboard");
    expect(data.runtime.recording).toBe(true);
    expect(data.runtime.currentSession?.event.courseCode).toBe("STAT");
    expect(data.events.length).toBeGreaterThan(5);
  });

  it("fails like the server for a method without fixture", async () => {
    const transport = createMockTransport({ simulate: false, latencyMs: 0 });
    // `memory.show` is a real server method the app does not use yet: no fixture.
    await expect(transport.request("memory.show", {})).rejects.toMatchObject({ code: RpcCode.MethodNotFound });
  });

  it("simulates a notes job through every stage with notifications", async () => {
    vi.useFakeTimers();
    const client = createRpcClient(createMockTransport({ simulate: true, latencyMs: 1, stageMs: 100, now: () => new Date() }));
    const stages: string[] = [];
    const finished = vi.fn();
    const started = vi.fn();
    client.on("job.started", started);
    client.on("job.progress", (event) => stages.push(event.stage));
    client.on("job.finished", finished);
    const call = client.call("notes.run", { course: "STAT", lessons: "7" });
    await vi.advanceTimersByTimeAsync(5);
    const { jobId } = await call;
    await vi.advanceTimersByTimeAsync(1000);
    expect(stages).toEqual(["CHECKING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"]);
    // The server's shapes: started carries method and params; the outcome is in result.data.
    expect(started).toHaveBeenCalledWith(expect.objectContaining({ jobId, method: "notes.run", params: { course: "STAT", lessons: "7" } }));
    expect(finished).toHaveBeenCalledWith(expect.objectContaining({ jobId, state: "COMPLETED", exitCode: 0 }));
    expect(finished.mock.lastCall?.[0]).toMatchObject({ result: { exitCode: 0, data: { status: "COMPLETED" } } });
  });
});
