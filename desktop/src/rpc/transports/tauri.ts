import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

import type { Transport } from "../transport";

/** Event the Rust supervisor emits for every server notification. */
export const NOTIFICATION_EVENT = "rpc://notification";

interface NotificationPayload {
  method: string;
  params: unknown;
}

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/** Calls go through the `rpc_call` command, which checks an allowlist in Rust. */
export function createTauriTransport(): Transport {
  return {
    kind: "tauri",
    request: (method, params) => invoke("rpc_call", { method, params }),
    subscribe(listener) {
      const pending = listen<NotificationPayload>(NOTIFICATION_EVENT, (event) => {
        listener(event.payload.method, event.payload.params);
      });
      return () => {
        void pending.then((unlisten) => unlisten());
      };
    },
    close: () => undefined,
  };
}
