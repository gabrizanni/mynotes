/**
 * First-run setup, answered by the Rust shell (`src-tauri/src/installer.rs`), not by
 * `mynotes serve`: it runs before any backend exists.
 *
 * Contract:
 * - `getSetupStatus()` → `invoke("setup_status")` → {@link SetupStatus} (`setupStatusSchema` in
 *   schema.ts; also reachable as `rpc.call("setup.status")`, which the Tauri transport routes
 *   here). `state` is `idle` (runtime missing, `reason: "RUNTIME_MISSING"`), `running`, `failed`
 *   (`reason`, `error`) or `complete` (runtime found). `paused` is reserved for a cancelled run.
 * - `runSetup({ provider, workspace? })` → `invoke("setup_run", { request })` → the first
 *   status, or an `RpcError` whose `data.reason` is one of {@link SetupRefusal}.
 * - `onSetupProgress(listener)` → the `setup://progress` event: one status per progress line
 *   of `setup_runtime.py`. `bytesDone`/`bytesTotal` are set only while a component downloads
 *   (measured bytes, so a ProgressBar may use them); `stage` is `starting`, `installation`,
 *   `components`, `workspace`, `verification`, then `ready` or `failed`.
 *
 * When setup completes, Rust restarts the backend supervisor; `backend://status` (native.ts)
 * then reports `ready`.
 */
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { z } from "zod";

import { toRpcError } from "./errors";
import { setupStatusSchema, type SetupStatus } from "./schema";

export const SETUP_PROGRESS_EVENT = "setup://progress";

/** Agents `setup_runtime.py --provider` accepts. */
export const providerSchema = z.enum(["codex", "agy", "claude"]);

/** `setup_run` input; Rust rejects unknown fields (`deny_unknown_fields`). */
export const setupRunRequestSchema = z.strictObject({
  provider: providerSchema,
  /** Absolute local folder for the private workspace; the setup default otherwise. */
  workspace: z.string().min(1).optional(),
});

export type SetupRunRequest = z.input<typeof setupRunRequestSchema>;

/** `data.reason` of a refused `setup_run`. */
export const SetupRefusal = {
  AlreadyRunning: "ALREADY_RUNNING",
  /** Windows: the runtime comes from the MyNotes installer. */
  ExternalInstaller: "EXTERNAL_INSTALLER",
  /** No release payload: set MYNOTES_DESKTOP_PAYLOAD or ship one under resources/payload. */
  PayloadMissing: "PAYLOAD_MISSING",
  PayloadInvalid: "PAYLOAD_INVALID",
  /** No Python 3.11+ to run the setup script. */
  PythonMissing: "PYTHON_MISSING",
  InvalidWorkspace: "INVALID_WORKSPACE",
  SpawnFailed: "SPAWN_FAILED",
} as const;

export async function getSetupStatus(): Promise<SetupStatus> {
  try {
    return setupStatusSchema.parse(await invoke("setup_status"));
  } catch (error) {
    throw toRpcError("setup.status", error);
  }
}

export async function runSetup(request: SetupRunRequest): Promise<SetupStatus> {
  const checked = setupRunRequestSchema.parse(request);
  try {
    return setupStatusSchema.parse(await invoke("setup_run", { request: checked }));
  } catch (error) {
    throw toRpcError("setup.run", error);
  }
}

/** Listen to setup progress; invalid payloads are dropped. Returns an unsubscribe function. */
export function onSetupProgress(listener: (status: SetupStatus) => void): () => void {
  let active = true;
  const pending = listen<unknown>(SETUP_PROGRESS_EVENT, (event) => {
    const parsed = setupStatusSchema.safeParse(event.payload);
    if (active && parsed.success) listener(parsed.data);
  });
  return () => {
    active = false;
    pending.then((unlisten) => unlisten()).catch(() => undefined);
  };
}
