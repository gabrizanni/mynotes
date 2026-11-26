/**
 * Keeps the store in step with the backend: one `watch.subscribe` for the snapshot topics,
 * then notifications instead of polling (ARCHITECTURE.md A1, serve_watch.py).
 */
import type { AppStore } from "./app";

export function startSync({ rpc, actions }: AppStore): () => void {
  const unsubscribers = [
    rpc.on("capture.changed", (dashboard) => actions.capture.apply(dashboard)),
    rpc.on("queue.changed", (status) => actions.queue.apply(status)),
    rpc.on("library.changed", (library) => actions.library.apply(library)),
    rpc.on("config.changed", (config) => actions.config.apply(config)),
    rpc.on("job.started", (job) => actions.jobs.started(job)),
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
      const snapshot = await rpc.call("watch.subscribe", { topics: ["capture", "queue", "library", "config"] });
      if (snapshot.capture) actions.capture.apply(snapshot.capture);
      if (snapshot.queue) actions.queue.apply(snapshot.queue);
      if (snapshot.library) actions.library.apply(snapshot.library);
      if (snapshot.config) actions.config.apply(snapshot.config);
    } catch {
      await Promise.all([actions.capture.refresh(), actions.queue.refresh(), actions.library.refresh(), actions.config.refresh()]);
    }
    await actions.jobs.refresh();
  };
  void subscribe();

  return () => {
    for (const unsubscribe of unsubscribers) unsubscribe();
  };
}
