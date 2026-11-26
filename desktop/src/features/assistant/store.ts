/**
 * Assistant conversation store, one per RPC client: it listens to `assistant.*` notifications
 * for the app's whole life, so a reply keeps streaming while the panel is closed.
 * Every change goes through `reduce` (model.ts). The draft stays in the app store's
 * assistant slice (it is what the Generate flow's handoff prefills).
 */
import { useSyncExternalStore } from "react";

import { RpcCode, RpcError, type AssistantNotification, type RpcClient } from "../../rpc";
import { useRpc } from "../../state";
import { PENDING_TURN, isBusy, lastTurn, reduce, type AssistantEvent, type AssistantView, type PageContext } from "./model";

/** Notifications that arrive before the start answer are held, at most this many. */
const MAX_EARLY_EVENTS = 500;
const NOTIFICATIONS = ["assistant.delta", "assistant.tool", "assistant.done", "assistant.error"] as const;

export interface AssistantStore {
  readonly getState: () => AssistantView;
  readonly subscribe: (listener: () => void) => () => void;
  /** Open the most recent conversation once (on first show). */
  readonly restore: () => Promise<void>;
  readonly send: (message: string, context: PageContext) => Promise<void>;
  readonly stop: () => Promise<void>;
  readonly reset: () => void;
}

function rejection(error: unknown): { code: string; message: string } {
  if (error instanceof RpcError) {
    if (error.code === -32008) return { code: "BUSY", message: error.message };
    if (error.code === RpcCode.InvalidParams) return { code: "PROVIDER", message: error.message };
    return { code: "PROVIDER", message: error.message };
  }
  return { code: "PROVIDER", message: error instanceof Error ? error.message : String(error) };
}

export function createAssistantStore(rpc: RpcClient): AssistantStore {
  let view: AssistantView = { status: "restoring" };
  let restored = false;
  let early: AssistantNotification[] = [];
  const listeners = new Set<() => void>();

  const dispatch = (event: AssistantEvent) => {
    const next = reduce(view, event);
    if (next === view) return;
    view = next;
    for (const listener of listeners) listener();
  };

  const currentSession = (): string | null => (view.status === "ready" ? view.conversation.sessionId : null);

  const receive = (event: AssistantNotification) => {
    if (event.sessionId === currentSession()) {
      dispatch(event);
      return;
    }
    // The answer to start/send has not been applied yet: keep the event for that session.
    if (lastTurn(view)?.id === PENDING_TURN && early.length < MAX_EARLY_EVENTS) early.push(event);
  };

  for (const name of NOTIFICATIONS) {
    rpc.on(name, (params) => receive({ type: name, ...params } as AssistantNotification));
  }

  const accept = (sessionId: string, turnId: string) => {
    dispatch({ type: "accepted", sessionId, turnId });
    const held = early.filter((event) => event.sessionId === sessionId);
    early = [];
    for (const event of held) dispatch(event);
  };

  return {
    getState: () => view,
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    async restore() {
      if (restored) return;
      restored = true;
      try {
        const { sessions } = await rpc.call("assistant.list", { limit: 1 });
        const latest = sessions[0];
        const session = latest ? await rpc.call("assistant.get", { sessionId: latest.id }) : null;
        if (view.status === "restoring") dispatch({ type: "restored", session });
      } catch {
        if (view.status === "restoring") dispatch({ type: "restored", session: null });
      }
    },
    async send(message, context) {
      const text = message.trim();
      if (!text || isBusy(view)) return;
      const sessionId = currentSession();
      dispatch({ type: "sending", message: text, provider: null });
      try {
        const answer = sessionId
          ? await rpc.call("assistant.send", { sessionId, message: text, context })
          : await rpc.call("assistant.start", { message: text, context });
        accept(answer.sessionId, answer.turnId);
      } catch (error) {
        early = [];
        dispatch({ type: "rejected", error: rejection(error) });
      }
    },
    async stop() {
      const sessionId = currentSession();
      if (!sessionId || !isBusy(view)) return;
      try {
        await rpc.call("assistant.cancel", { sessionId });
      } catch {
        // The reply ends on its own; its done or error notification still arrives.
      }
    },
    reset() {
      if (isBusy(view)) return;
      early = [];
      dispatch({ type: "reset" });
    },
  };
}

const stores = new WeakMap<RpcClient, AssistantStore>();

export function assistantStoreFor(rpc: RpcClient): AssistantStore {
  let store = stores.get(rpc);
  if (!store) {
    store = createAssistantStore(rpc);
    stores.set(rpc, store);
  }
  return store;
}

export function useAssistantStore(): { store: AssistantStore; view: AssistantView } {
  const store = assistantStoreFor(useRpc());
  const view = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  return { store, view };
}
