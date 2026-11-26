/**
 * Keeps the store in step with the backend: one `watch.subscribe` for the snapshot topics,
 * then notifications instead of polling (docs/SERVE.md "Watching instead of polling").
 *
 * - `watch.subscribe` answers `{topics, snapshots: {<topic>: {topic, hash, data}}}` and every
 *   `<topic>.changed` is such a snapshot; `data` is null (with `error`) when the server could
 *   not compute it, which leaves the store as it was.
 * - When the shell reports `backend://status` → `ready` with a new generation (first start, or
 *   a restart of `mynotes serve`), the store reads every snapshot and the job list again: the
 *   server lost nothing on disk, but notifications sent while it was down never arrived.
 */
import { onBackendStatus, type BackendStatus } from "../rpc/native";
import { isTauriRuntime } from "../rpc/transports/tauri";
import type { AppStore } from "./app";

export interface SyncOptions {
  /** Backend lifecycle events; the Tauri shell's `backend://status` by default. */
  onBackendStatus?: (listener: (status: BackendStatus) => void) => () => void;
}

const TOPICS = ["capture", "queue", "library", "config"] as const;

export function startSync({ rpc, actions }: AppStore, options: SyncOptions = {}): () => void {
  const unsubscribers = [
    rpc.on("capture.changed", ({ data }) => data && actions.capture.apply(data)),
    rpc.on("queue.changed", ({ data }) => data && actions.queue.apply(data)),
    rpc.on("library.changed", ({ data }) => data && actions.library.apply(data)),
    rpc.on("config.changed", ({ data }) => data && actions.config.apply(data)),
    rpc.on("job.started", (event) => actions.jobs.started(event)),
    rpc.on("job.progress", (event) => actions.jobs.progress(event)),
    rpc.on("job.finished", (event) => actions.jobs.finished(event)),
  ];

  const subscribe = async () => {
    try {
      await rpc.call("session.open", { visible: true });
    } catch {
      // Older servers without sessions still answer watch.subscribe.
    }
    try {
      const { snapshots } = await rpc.call("watch.subscribe", { topics: [...TOPICS] });
      if (snapshots.capture?.data) actions.capture.apply(snapshots.capture.data);
      if (snapshots.queue?.data) actions.queue.apply(snapshots.queue.data);
      if (snapshots.library?.data) actions.library.apply(snapshots.library.data);
      if (snapshots.config?.data) actions.config.apply(snapshots.config.data);
    } catch {
      await Promise.all([actions.capture.refresh(), actions.queue.refresh(), actions.library.refresh(), actions.config.refresh()]);
    }
    await actions.jobs.refresh();
  };
  void subscribe();

  // Each new generation is a server that has not sent this window anything yet: read it all.
  // The window may also open while the backend is still starting, so the first one counts too.
  const listen = options.onBackendStatus ?? (isTauriRuntime() ? onBackendStatus : null);
  let generation: number | null = null;
  if (listen) {
    unsubscribers.push(
      listen((status) => {
        if (status.state !== "ready" || status.generation === generation) return;
        generation = status.generation;
        void subscribe();
      }),
    );
  }

  return () => {
    for (const unsubscribe of unsubscribers) unsubscribe();
  };
}
