import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import type { Transport } from "../transport";

/** Event the Rust supervisor emits for every server notification (`src-tauri/src/events.rs`). */
export const NOTIFICATION_EVENT = "rpc://notification";

/**
 * Methods answered by the Rust shell instead of `mynotes serve`: setup runs before any backend
 * exists. Everything else goes through `rpc_call`, which checks the protocol-1 allowlist.
 */
export const NATIVE_METHODS: Readonly<Record<string, { command: string; args?: (params: unknown) => Record<string, unknown> }>> = {
  "setup.status": { command: "setup_status" },
  // `setup_run` takes `{request}` (installer.rs, deny_unknown_fields).
  "setup.run": { command: "setup_run", args: (params) => ({ request: params ?? {} }) },
};

interface NotificationPayload {
  method: string;
  params: unknown;
}

function isNotificationPayload(value: unknown): value is NotificationPayload {
  return typeof value === "object" && value !== null && typeof (value as { method?: unknown }).method === "string";
}

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** Calls go through the `rpc_call` command (allowlist in Rust); errors keep their JSON-RPC code. */
export function createTauriTransport(): Transport {
  return {
    kind: "tauri",
    request(method, params) {
      const native = Object.hasOwn(NATIVE_METHODS, method) ? NATIVE_METHODS[method] : undefined;
      if (native) return native.args ? invoke(native.command, native.args(params)) : invoke(native.command);
      return invoke("rpc_call", { method, params: params ?? null });
    },
    subscribe(listener) {
      let active = true;
      const pending = listen<unknown>(NOTIFICATION_EVENT, (event) => {
        if (active && isNotificationPayload(event.payload)) listener(event.payload.method, event.payload.params);
      });
      return () => {
        active = false;
        pending.then((unlisten) => unlisten()).catch(() => undefined);
      };
    },
    close: () => undefined,
  };
}
