import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { MethodName, MethodParams, MethodResult } from "../../rpc";
import { useRpc } from "../../state";

export type RpcData<T> = { status: "loading"; data: null } | { status: "ready"; data: T } | { status: "error"; data: null };

/**
 * One RPC read owned by a screen (history, style, transcript): not mirrored in the store
 * because no notification updates it. `key` re-runs the call; `reload()` runs it again.
 */
export function useRpcData<M extends MethodName>(method: M, params: MethodParams<M> | null, key: string): RpcData<MethodResult<M>> & { reload: () => void } {
  const rpc = useRpc();
  const [state, setState] = useState<RpcData<MethodResult<M>>>({ status: "loading", data: null });
  const [round, setRound] = useState(0);
  const paramsRef = useRef(params);
  paramsRef.current = params;
  const lastKey = useRef<string | null>(null);

  useEffect(() => {
    const current = paramsRef.current;
    if (current === null) return undefined;
    let alive = true;
    // A reload keeps the current data on screen; another key starts from loading.
    const sameKey = lastKey.current === key;
    lastKey.current = key;
    setState((previous) => (sameKey && previous.status === "ready" ? previous : { status: "loading", data: null }));
    const call = (name: M, args: MethodParams<M>) => (rpc.call as (this: void, name: M, args: MethodParams<M>) => Promise<MethodResult<M>>)(name, args);
    call(method, current).then(
      (data) => {
        if (alive) setState({ status: "ready", data });
      },
      () => {
        if (alive) setState({ status: "error", data: null });
      },
    );
    return () => {
      alive = false;
    };
  }, [rpc, method, key, round]);

  const reload = useCallback(() => setRound((value) => value + 1), []);
  // Stable between renders, so callers can memoize on it (a new object each render looped StyleTab).
  return useMemo(() => ({ ...state, reload }), [state, reload]);
}
