import { useEffect, useRef, useState } from "react";

import { isTauriRuntime } from "../../rpc/transports/tauri";

/**
 * Drag audio anywhere on the page. Inside the app, Tauri reports real file paths
 * (`onDragDropEvent`); in a browser (mock, tests) the HTML drop event only knows file names,
 * which the fixture mock accepts. Returns whether files are being dragged over the window.
 */
export function useAudioDrop(onPaths: (paths: string[]) => void, enabled = true): boolean {
  const [dragging, setDragging] = useState(false);
  const callback = useRef(onPaths);
  useEffect(() => {
    callback.current = onPaths;
  });

  useEffect(() => {
    if (!enabled) return undefined;
    if (isTauriRuntime()) {
      let unlisten: (() => void) | null = null;
      let cancelled = false;
      import("@tauri-apps/api/webview")
        .then(({ getCurrentWebview }) =>
          getCurrentWebview().onDragDropEvent(({ payload }) => {
            if (payload.type === "enter" || payload.type === "over") setDragging(true);
            else if (payload.type === "leave") setDragging(false);
            else {
              setDragging(false);
              callback.current(payload.paths);
            }
          }),
        )
        .then((stop) => {
          if (cancelled) stop();
          else unlisten = stop;
        })
        .catch(() => setDragging(false));
      return () => {
        cancelled = true;
        unlisten?.();
      };
    }

    let depth = 0;
    const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes("Files");
    const onEnter = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth += 1;
      setDragging(true);
    };
    const onOver = (event: DragEvent) => {
      if (hasFiles(event)) event.preventDefault();
    };
    const onLeave = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      depth = Math.max(0, depth - 1);
      if (depth === 0) setDragging(false);
    };
    const onDrop = (event: DragEvent) => {
      if (!hasFiles(event)) return;
      event.preventDefault();
      depth = 0;
      setDragging(false);
      callback.current(Array.from(event.dataTransfer?.files ?? []).map((file) => file.name));
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragover", onOver);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("drop", onDrop);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("drop", onDrop);
    };
  }, [enabled]);

  return dragging;
}
