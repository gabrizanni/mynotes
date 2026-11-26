import { useEffect, useState } from "react";

import { getBackendStatus, onBackendStatus, type BackendStatus } from "../rpc/native";
import { isTauriRuntime } from "../rpc/transports/tauri";

/** The supervisor's status inside the app (first read, then every change); `null` in the mock. */
export function useBackendStatus(): BackendStatus | null {
  const [status, setStatus] = useState<BackendStatus | null>(null);
  useEffect(() => {
    if (!isTauriRuntime()) return undefined;
    let active = true;
    const stop = onBackendStatus((next) => active && setStatus(next));
    getBackendStatus()
      .then((first) => active && setStatus((current) => current ?? first))
      .catch(() => undefined); // The event stream still delivers the next status.
    return () => {
      active = false;
      stop();
    };
  }, []);
  return status;
}
