import { RpcCode, RpcError, toRpcError } from "./errors";
import {
  methods,
  notifications,
  type MethodName,
  type MethodParams,
  type MethodResult,
  type NotificationName,
  type NotificationParams,
} from "./schema";
import type { Transport } from "./transport";

type ParamsArg<M extends MethodName> = undefined extends MethodParams<M>
  ? [params?: MethodParams<M>]
  : [params: MethodParams<M>];

export interface RpcClient {
  readonly transport: Transport["kind"];
  /** Call a method; params and result are checked with the zod schemas in schema.ts. */
  call<M extends MethodName>(method: M, ...args: ParamsArg<M>): Promise<MethodResult<M>>;
  /** Listen to one notification; invalid payloads are dropped with a warning. */
  on<N extends NotificationName>(name: N, handler: (params: NotificationParams<N>) => void): () => void;
  close(): void;
}

function isNotificationName(name: string): name is NotificationName {
  return Object.hasOwn(notifications, name);
}

export function createRpcClient(transport: Transport): RpcClient {
  const handlers = new Map<NotificationName, Set<(params: unknown) => void>>();

  const unsubscribe = transport.subscribe((name, raw) => {
    if (!isNotificationName(name)) return;
    const listeners = handlers.get(name);
    if (!listeners || listeners.size === 0) return;
    const parsed = notifications[name].safeParse(raw);
    if (!parsed.success) {
      console.warn(`mynotes: dropped invalid ${name} notification`, parsed.error.issues);
      return;
    }
    for (const listener of listeners) listener(parsed.data);
  });

  return {
    transport: transport.kind,

    async call<M extends MethodName>(method: M, ...args: ParamsArg<M>): Promise<MethodResult<M>> {
      const spec = Object.hasOwn(methods, method) ? methods[method] : undefined;
      if (!spec) throw new RpcError(method, RpcCode.MethodNotFound, `Unknown method ${method}`);
      const params = spec.params.safeParse(args[0]);
      if (!params.success) {
        throw new RpcError(method, RpcCode.InvalidParams, "Invalid parameters", params.error.issues);
      }
      let raw: unknown;
      try {
        raw = await transport.request(method, params.data);
      } catch (error) {
        throw toRpcError(method, error);
      }
      const result = spec.result.safeParse(raw);
      if (!result.success) {
        throw new RpcError(method, RpcCode.InvalidResult, "The backend answered in an unexpected shape", result.error.issues);
      }
      return result.data as MethodResult<M>;
    },

    on<N extends NotificationName>(name: N, handler: (params: NotificationParams<N>) => void): () => void {
      const listeners = handlers.get(name) ?? new Set<(params: unknown) => void>();
      // Safe: payloads are parsed with notifications[name] before listeners run.
      const erased = handler as (params: unknown) => void;
      listeners.add(erased);
      handlers.set(name, listeners);
      return () => {
        listeners.delete(erased);
      };
    },

    close() {
      unsubscribe();
      handlers.clear();
      transport.close();
    },
  };
}
