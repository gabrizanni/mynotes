import { describe, expect, it, vi } from "vitest";

import { mockApp } from "../../tests/unit/helpers";
import type { BackendStatus } from "../rpc/native";
import { toJob } from "../rpc/jobs";
import { startSync } from "./sync";

const ready = (generation: number): BackendStatus => ({ state: "ready", pid: 1, serverPid: 2, protocol: 1, version: "0.4.1", generation });

async function settle() {
  for (let i = 0; i < 5; i += 1) await new Promise((done) => setTimeout(done, 0));
}

describe("startSync", () => {
  it("applies the snapshots of watch.subscribe and the data of <topic>.changed", async () => {
    const { app } = mockApp("default");
    const stop = startSync(app, { onBackendStatus: () => () => undefined });
    await settle();
    expect(app.store.getState().capture.dashboard?.schemaVersion).toBe(1);
    expect(app.store.getState().jobs.order.length).toBeGreaterThan(0);
    stop();
  });

  it("reads everything again for each new backend generation, once per generation", async () => {
    const { app, rpc } = mockApp("default");
    const call = vi.spyOn(rpc, "call");
    let emit: (status: BackendStatus) => void = () => undefined;
    const stop = startSync(app, {
      onBackendStatus: (listener) => {
        emit = listener;
        return () => undefined;
      },
    });
    await settle();
    const subscribes = () => call.mock.calls.filter(([method]) => method === "watch.subscribe").length;
    expect(subscribes()).toBe(1);
    emit(ready(1));
    emit(ready(1));
    await settle();
    expect(subscribes()).toBe(2);
    emit({ state: "restarting", attempt: 1, delayMs: 500, reason: "exit 1" });
    emit(ready(2));
    await settle();
    expect(subscribes()).toBe(3);
    stop();
  });
});

describe("toJob", () => {
  it("turns a server record into the app's job", () => {
    const job = toJob({
      id: "job-1",
      method: "notes.run",
      params: { course: "STAT", lessons: "6,7" },
      state: "FAILED",
      startedAt: 1_795_600_000,
      finishedAt: 1_795_600_600,
      exitCode: 2,
      result: { exitCode: 2, data: { status: "PAUSED_QUOTA", batch_id: "b-1" } },
      lastProgress: { jobId: "job-1", stage: "GENERATING" },
    });
    expect(job).toMatchObject({
      jobId: "job-1",
      kind: "notes",
      state: "failed",
      stage: "GENERATING",
      courseCode: "STAT",
      lectureIds: ["STAT-L06", "STAT-L07"],
      batchId: "b-1",
      result: { status: "PAUSED_QUOTA" },
      startedAt: new Date(1_795_600_000_000).toISOString(),
    });
  });

  it("reads a lesson job's course from its lecture id and keeps a result without status out", () => {
    const job = toJob({ id: "job-2", method: "lesson.process", params: { lectureId: "MICRO-L04" }, state: "COMPLETED", startedAt: 1_795_600_000, result: { exitCode: 0, data: null } });
    expect(job).toMatchObject({ kind: "lesson", state: "succeeded", courseCode: "MICRO", lectureIds: ["MICRO-L04"], result: null });
  });
});
