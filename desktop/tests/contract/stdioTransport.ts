/**
 * Node transport for `mynotes serve --stdio`: the same JSON-RPC 2.0 line framing as the Rust
 * supervisor (one UTF-8 JSON object per line, 16 MiB cap, CRLF tolerated, UTF-8 split across
 * reads), so the contract tests drive the real server through the app's own `RpcClient`.
 */
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";

import { RpcCode, RpcError } from "../../src/rpc/errors";
import type { Transport } from "../../src/rpc/transport";

const MAX_MESSAGE_BYTES = 16 * 1024 * 1024;
const NEWLINE = 0x0a;

export interface StdioOptions {
  command: string;
  args: readonly string[];
  env: NodeJS.ProcessEnv;
  cwd?: string;
  /** Per-call limit; the server keeps running when it passes. */
  callTimeoutMs?: number;
}

export interface StdioTransport extends Transport {
  readonly pid: number | undefined;
  /** Close stdin (the server's stop signal) and wait for exit; SIGKILL after `graceMs`. */
  shutdown(graceMs?: number): Promise<number | null>;
  stderr(): string;
}

interface Pending {
  method: string;
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
  timer: ReturnType<typeof setTimeout>;
}

interface Message {
  id?: number | string | null;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

function isMessage(value: unknown): value is Message {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function createStdioTransport(options: StdioOptions): StdioTransport {
  const child: ChildProcessWithoutNullStreams = spawn(options.command, [...options.args], {
    env: options.env,
    cwd: options.cwd,
    stdio: ["pipe", "pipe", "pipe"],
  });
  const pending = new Map<number, Pending>();
  const listeners = new Set<(method: string, params: unknown) => void>();
  let nextId = 1;
  let buffered: Buffer[] = [];
  let bufferedBytes = 0;
  let stderrText = "";
  let exited: Promise<number | null> | null = null;

  const failAll = (reason: string) => {
    for (const [id, call] of pending) {
      clearTimeout(call.timer);
      call.reject(new RpcError(call.method, RpcCode.BackendRestarted, reason));
      pending.delete(id);
    }
  };

  const handleLine = (line: Buffer) => {
    const text = line.toString("utf8").replace(/\r$/, "");
    if (text.trim() === "") return;
    let message: unknown;
    try {
      message = JSON.parse(text);
    } catch {
      return; // Not a protocol line; the server only logs to stderr, so this is noise.
    }
    if (!isMessage(message)) return;
    if (typeof message.method === "string" && message.id === undefined) {
      for (const listener of listeners) listener(message.method, message.params);
      return;
    }
    if (typeof message.id !== "number") return;
    const call = pending.get(message.id);
    if (!call) return;
    pending.delete(message.id);
    clearTimeout(call.timer);
    if (message.error) call.reject(new RpcError(call.method, message.error.code, message.error.message, message.error.data));
    else call.resolve(message.result ?? null);
  };

  child.stdout.on("data", (chunk: Buffer) => {
    let start = 0;
    for (let index = chunk.indexOf(NEWLINE); index !== -1; index = chunk.indexOf(NEWLINE, start)) {
      buffered.push(chunk.subarray(start, index));
      const line = Buffer.concat(buffered);
      buffered = [];
      bufferedBytes = 0;
      if (line.length <= MAX_MESSAGE_BYTES + 1) handleLine(line);
      start = index + 1;
    }
    if (start < chunk.length) {
      buffered.push(chunk.subarray(start));
      bufferedBytes += chunk.length - start;
      if (bufferedBytes > MAX_MESSAGE_BYTES + 1) {
        buffered = [];
        bufferedBytes = 0;
      }
    }
  });
  child.stderr.on("data", (chunk: Buffer) => {
    stderrText = (stderrText + chunk.toString("utf8")).slice(-64 * 1024);
  });
  const exit = new Promise<number | null>((resolve) => {
    child.on("exit", (code) => {
      failAll("the backend exited");
      resolve(code);
    });
    child.on("error", (error) => {
      failAll(`the backend could not start: ${error.message}`);
      resolve(null);
    });
  });

  return {
    kind: "stdio",
    get pid() {
      return child.pid;
    },
    request(method, params) {
      const id = nextId++;
      const line = JSON.stringify({ jsonrpc: "2.0", id, method, ...(params === undefined ? {} : { params }) });
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new RpcError(method, RpcCode.Timeout, `${method} did not answer in time`));
        }, options.callTimeoutMs ?? 60_000);
        pending.set(id, { method, resolve, reject, timer });
        child.stdin.write(`${line}\n`, (error) => {
          if (!error) return;
          pending.delete(id);
          clearTimeout(timer);
          reject(new RpcError(method, RpcCode.BackendRestarted, error.message));
        });
      });
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    close() {
      child.stdin.end();
    },
    shutdown(graceMs = 10_000) {
      exited ??= (async () => {
        if (child.exitCode !== null || child.signalCode !== null) return child.exitCode;
        child.stdin.end();
        const timer = setTimeout(() => child.kill("SIGKILL"), graceMs);
        const code = await exit;
        clearTimeout(timer);
        return code;
      })();
      return exited;
    },
    stderr: () => stderrText,
  };
}
