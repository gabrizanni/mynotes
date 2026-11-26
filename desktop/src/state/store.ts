/** A minimal external store for `useSyncExternalStore`: immutable updates, synchronous notify. */
export type Listener = () => void;

export interface Store<S> {
  readonly getState: () => S;
  readonly setState: (update: (state: S) => S) => void;
  readonly subscribe: (listener: Listener) => () => void;
}

export function createStore<S>(initial: S): Store<S> {
  let state = initial;
  const listeners = new Set<Listener>();
  return {
    getState: () => state,
    setState(update) {
      const next = update(state);
      if (Object.is(next, state)) return;
      state = next;
      for (const listener of listeners) listener();
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
