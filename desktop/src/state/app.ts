/**
 * The app store: composes every slice and binds its actions to the RPC client.
 *
 * Adding a slice: create `slices/<topic>.ts` (state, initial value, `<topic>Actions(ctx)`),
 * add it to `AppState` in types.ts and to the two maps below. Each slice only writes its own key.
 */
import type { RpcClient } from "../rpc";
import { assistantActions, assistantInitial } from "./slices/assistant";
import { captureActions, captureInitial } from "./slices/capture";
import { configActions, configInitial } from "./slices/config";
import { jobsActions, jobsInitial } from "./slices/jobs";
import { libraryActions, libraryInitial } from "./slices/library";
import { queueActions, queueInitial } from "./slices/queue";
import { uiActions, uiInitial, type UiState } from "./slices/ui";
import { createStore, type Store } from "./store";
import type { AppState, SliceContext, SliceKey } from "./types";

export function initialAppState(ui: Partial<UiState> = {}): AppState {
  return {
    capture: captureInitial,
    queue: queueInitial,
    library: libraryInitial,
    config: configInitial,
    jobs: jobsInitial,
    assistant: assistantInitial,
    ui: { ...uiInitial, ...ui },
  };
}

function bindActions(store: Store<AppState>, rpc: RpcClient) {
  const context = <K extends SliceKey>(key: K): SliceContext<K> => ({
    get: () => store.getState()[key],
    set: (patch) =>
      store.setState((state) => {
        const current = state[key];
        const changes = typeof patch === "function" ? patch(current) : patch;
        if (Object.keys(changes).length === 0) return state;
        return { ...state, [key]: { ...current, ...changes } };
      }),
    rpc,
    root: () => store.getState(),
  });
  return {
    capture: captureActions(context("capture")),
    queue: queueActions(context("queue")),
    library: libraryActions(context("library")),
    config: configActions(context("config")),
    jobs: jobsActions(context("jobs")),
    assistant: assistantActions(context("assistant")),
    ui: uiActions(context("ui")),
  };
}

export type AppActions = ReturnType<typeof bindActions>;

export interface AppStore {
  readonly store: Store<AppState>;
  readonly actions: AppActions;
  readonly rpc: RpcClient;
}

export function createAppStore(rpc: RpcClient, initial: AppState = initialAppState()): AppStore {
  const store = createStore(initial);
  return { store, actions: bindActions(store, rpc), rpc };
}
