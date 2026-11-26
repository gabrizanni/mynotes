import { createContext, useCallback, useContext, useRef, useSyncExternalStore, type ReactNode } from "react";

import type { AppActions, AppStore } from "./app";
import type { AppState } from "./types";

const StoreContext = createContext<AppStore | null>(null);

export function StoreProvider({ value, children }: { value: AppStore; children: ReactNode }) {
  return <StoreContext.Provider value={value}>{children}</StoreContext.Provider>;
}

function useAppStore(): AppStore {
  const value = useContext(StoreContext);
  if (!value) throw new Error("useAppState must be used inside <StoreProvider>");
  return value;
}

/**
 * Read part of the state. The selection is cached per snapshot, so selectors may build new
 * objects; pass `isEqual` (e.g. `shallowEqual`) to skip re-renders when the result is equal.
 */
export function useAppState<T>(selector: (state: AppState) => T, isEqual: (a: T, b: T) => boolean = Object.is): T {
  const { store } = useAppStore();
  const cache = useRef<{ state: AppState; selector: (state: AppState) => T; value: T } | null>(null);
  const getSelection = useCallback(() => {
    const state = store.getState();
    const cached = cache.current;
    if (cached && cached.state === state && cached.selector === selector) return cached.value;
    const value = selector(state);
    if (cached && isEqual(cached.value, value)) {
      cache.current = { state, selector, value: cached.value };
      return cached.value;
    }
    cache.current = { state, selector, value };
    return value;
  }, [store, selector, isEqual]);
  return useSyncExternalStore(store.subscribe, getSelection, getSelection);
}

export function useActions(): AppActions {
  return useAppStore().actions;
}

export function useRpc(): AppStore["rpc"] {
  return useAppStore().rpc;
}

export function shallowEqual<T>(a: T, b: T): boolean {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  const keysA = Object.keys(a);
  if (keysA.length !== Object.keys(b).length) return false;
  return keysA.every((key) => Object.is((a as Record<string, unknown>)[key], (b as Record<string, unknown>)[key]));
}
