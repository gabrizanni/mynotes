import { describe, expect, it } from "vitest";

import { createRpcClient, type RpcClient } from "./client";
import { RpcCode, RpcError } from "./errors";
import { jobAnswer, jobKind, runJob } from "./jobs";
import type { Transport } from "./transport";
import { createMockTransport } from "./transports/mock";

const fixtureFree = (): RpcClient => createRpcClient(createMockTransport({ simulate: false, latencyMs: 0 }));

/** A transport that answers `{jobId}` and then sends the given `job.finished` (or nothing). */
function scripted(finished: Record<string, unknown> | null): Transport {
  const listeners = new Set<(method: string, params: unknown) => void>();
  return {
    kind: "mock",
    request: () => {
      if (finished) setTimeout(() => listeners.forEach((listener) => listener("job.finished", { jobId: "job-1", ...finished })), 1);
      return Promise.resolve({ jobId: "job-1" });
    },
    subscribe: (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    close: () => listeners.clear(),
  };
}

describe("runJob: job-lane methods awaited like direct calls", () => {
  it("resolves with the command's answer from job.finished, after job.started", async () => {
    const rpc = fixtureFree();
    const seen: string[] = [];
    rpc.on("job.started", (event) => seen.push(`started:${event.method}`));
    rpc.on("job.finished", (event) => seen.push(`finished:${event.state}`));
    const answer = await runJob(rpc, "capture.sync", { ifDue: true });
    expect(answer).toEqual({ exitCode: 0, data: { status: "SYNCED", message: null } });
    expect(seen).toEqual(["started:capture.sync", "finished:COMPLETED"]);
    rpc.close();
  });

  it("keeps an answer that carries an exit code other than 0 (a calendar that could not be read)", async () => {
    const rpc = createRpcClient(createMockTransport({ scenario: "schedule-error", simulate: false, latencyMs: 0 }));
    const { exitCode, data } = await runJob(rpc, "capture.connect", { source: { url: "https://calendar.example/x.ics" } });
    expect(exitCode).toBe(1);
    expect(data).toMatchObject({ status: "FAILED", code: "GOOGLE_FEED_UNAVAILABLE" });
    rpc.close();
  });

  it("throws the command's error when the job printed no JSON", async () => {
    const rpc = createRpcClient(scripted({ state: "FAILED", exitCode: 2, error: { code: RpcCode.CliError, message: "The recording app requires macOS or Windows 11" } }));
    const failure = runJob(rpc, "capture.enable", undefined);
    await expect(failure).rejects.toBeInstanceOf(RpcError);
    await expect(failure).rejects.toMatchObject({ code: RpcCode.CliError, method: "capture.enable", message: "The recording app requires macOS or Windows 11" });
  });

  it("times out without cancelling the job, and rejects answers in an unexpected shape", async () => {
    await expect(runJob(createRpcClient(scripted(null)), "capture.disable", undefined, 20)).rejects.toMatchObject({ code: RpcCode.Timeout });
    const odd = { jobId: "job-1", state: "COMPLETED", exitCode: 0, result: { exitCode: 0, data: { status: 3 } } };
    expect(() => jobAnswer("notes.publish", odd)).toThrow(expect.objectContaining({ code: RpcCode.InvalidResult }));
    expect(() => jobAnswer("notes.publish", { jobId: "job-1", state: "CANCELLED" })).toThrow(expect.objectContaining({ code: RpcCode.InternalError }));
  });

  it("publishing is its own kind of job, never a notes run", () => {
    expect(jobKind("notes.publish")).toBe("publish");
    expect(jobKind("capture.connect")).toBe("capture");
    expect(jobKind("notes.run")).toBe("notes");
  });
});

describe("mock job methods behave like the server's job lane", () => {
  it("turns the recorder on and off through jobs, then updates the dashboard", async () => {
    const rpc = fixtureFree();
    const boards: boolean[] = [];
    rpc.on("capture.changed", (snapshot) => boards.push(snapshot.data?.enabled === true));
    const started = await rpc.call("capture.enable");
    expect(started.jobId).toMatch(/^mock-capture-enable-/);
    expect(started.job).toMatchObject({ method: "capture.enable", state: "RUNNING" });
    expect(await runJob(rpc, "capture.disable", undefined)).toEqual({ exitCode: 0, data: { status: "DISABLED", message: null } });
    expect(boards.at(-1)).toBe(false);
    const jobs = await rpc.call("job.list");
    expect(jobs.jobs.map((job) => [job.method, job.state])).toEqual(expect.arrayContaining([["capture.disable", "COMPLETED"]]));
    rpc.close();
  });

  it("publishes a lesson through a job that answers the publish fixture", async () => {
    const rpc = fixtureFree();
    const answer = await runJob(rpc, "notes.publish", { lecture: "stat-l06", candidate: "/tmp/candidate.docx" });
    expect(answer).toMatchObject({ exitCode: 0, data: { status: "PUBLISHED", lecture_id: "STAT-L06" } });
    rpc.close();
  });

  it("processes a lesson from a transcript without transcribing it", async () => {
    const rpc = createRpcClient(createMockTransport({ simulate: true, latencyMs: 0, stageMs: 5 }));
    const stages: string[] = [];
    rpc.on("job.progress", (event) => stages.push(event.stage));
    const finished = new Promise((resolve) => rpc.on("job.finished", resolve));
    const { jobId, job } = await rpc.call("lesson.process", { lectureId: "STAT-L01", transcript: "/tmp/lesson.txt" });
    expect(job?.params).toEqual({ lectureId: "STAT-L01", transcript: "/tmp/lesson.txt" });
    expect((await rpc.call("job.get", { jobId })).lastProgress?.stage).toBe("PREPARING");
    await expect(finished).resolves.toMatchObject({ jobId, state: "COMPLETED" });
    expect(stages).not.toContain("TRANSCRIBING");
    rpc.close();
  });

  it("refuses an audio file and a transcript together, like the server", async () => {
    const rpc = fixtureFree();
    await expect(rpc.call("lesson.process", { lectureId: "STAT-L01", audio: "a.m4a", transcript: "a.txt" })).rejects.toMatchObject({ code: RpcCode.InvalidParams });
    rpc.close();
  });

  it("answers the transcript text of the lesson asked", async () => {
    const rpc = fixtureFree();
    const { data } = await rpc.call("library.transcript", { lectureId: "STAT-L03" });
    expect(data.path.endsWith("STAT-L03.txt")).toBe(true);
    expect(data.text.startsWith("[00:00:00–00:00:20] professor: Good morning.")).toBe(true);
    expect(data).toMatchObject({ truncated: false, encoding: "utf-8" });
    rpc.close();
  });
});
