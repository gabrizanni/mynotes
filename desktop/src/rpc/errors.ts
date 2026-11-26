/** JSON-RPC error codes shared with `src-tauri/src/backend/protocol.rs` and `mynotes serve`. */
export const RpcCode = {
  ParseError: -32700,
  InvalidRequest: -32600,
  MethodNotFound: -32601,
  InvalidParams: -32602,
  InternalError: -32603,
  /** The CLI printed `mynotes: …`. */
  CliError: -32001,
  Busy: -32002,
  NeedsReview: -32003,
  /** `job.*` with an unknown job id. */
  UnknownJob: -32004,
  /** `system.hello` with a protocol the server does not speak. */
  UnsupportedProtocol: -32005,
  ShuttingDown: -32006,
  /** Cancelled before it started (`$/cancelRequest`, or the server stopping). */
  RequestCancelled: -32800,
  BackendRestarted: -32010,
  BackendUnavailable: -32011,
  MethodNotAllowed: -32012,
  /** App-side: no answer within the method's time limit (the server may still finish). */
  Timeout: -32013,
  /** App-side: the result did not match its zod schema. */
  InvalidResult: -32090,
} as const;

export type RpcCodeValue = (typeof RpcCode)[keyof typeof RpcCode];

export class RpcError extends Error {
  readonly code: number;
  readonly data: unknown;
  readonly method: string;

  constructor(method: string, code: number, message: string, data?: unknown) {
    super(message);
    this.name = "RpcError";
    this.method = method;
    this.code = code;
    this.data = data;
  }

  /** Errors worth retrying without asking the student. */
  get transient(): boolean {
    return this.code === RpcCode.Busy || this.code === RpcCode.BackendRestarted || this.code === RpcCode.BackendUnavailable;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** Normalize whatever a transport threw into an RpcError. */
export function toRpcError(method: string, error: unknown): RpcError {
  if (error instanceof RpcError) return error;
  if (isRecord(error) && typeof error.code === "number" && typeof error.message === "string") {
    return new RpcError(method, error.code, error.message, error.data);
  }
  const message = error instanceof Error ? error.message : String(error);
  return new RpcError(method, RpcCode.InternalError, message);
}
