import { useState } from "react";

import { version } from "../../package.json";
import { Button } from "../components";
import { useT } from "../i18n";
import { restartBackend } from "../rpc/native";
import { detailLines, failureDetails, type FailedStatus } from "./backendFailure";
import styles from "./BackendFailure.module.css";

export interface BackendFailureProps {
  status: FailedStatus;
  /** Injected in tests; the app restarts the supervisor (`backend_restart`). */
  onRetry?: () => Promise<unknown>;
  copy?: (text: string) => Promise<void>;
}

const writeClipboard = (text: string) => navigator.clipboard.writeText(text);

/**
 * The supervisor gave up (`backend://status` → `failed`): five stops in two minutes, a protocol
 * mismatch, or a runtime it cannot start. Instead of a window whose every call fails, say so,
 * show the backend's last stderr lines, and offer "Try again" and "Copy details".
 */
export function BackendFailure({ status, onRetry = restartBackend, copy = writeClipboard }: BackendFailureProps) {
  const t = useT();
  const m = t.backend.failure;
  const [note, setNote] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const lines = detailLines(status);
  const body = status.code === "CRASH_LOOP" ? m.crashLoop(status.crashes) : status.code === "PROTOCOL_MISMATCH" ? m.mismatch : m.other;

  const retry = async () => {
    setRetrying(true);
    try {
      await onRetry();
    } catch {
      // The next `backend://status` event says how the restart went.
    } finally {
      setRetrying(false);
    }
  };
  const copyDetails = async () => {
    try {
      await copy(failureDetails(status, { appVersion: version, platform: navigator.platform || "unknown" }));
      setNote(m.copied);
    } catch {
      setNote(m.copyFailed); // The details stay selectable on screen.
    }
  };

  return (
    <main className={styles.screen} role="alert" aria-labelledby="backend-failure-title">
      <div className={styles.panel}>
        <h1 id="backend-failure-title" className={styles.title}>
          {m.title}
        </h1>
        <p className={styles.body}>{body}</p>
        <p className={styles.label} id="backend-failure-details">
          {m.detailsLabel}
        </p>
        <pre className={styles.details} aria-labelledby="backend-failure-details" tabIndex={0}>
          {lines.length ? lines.join("\n") : m.noDetails}
        </pre>
        <div className={styles.actions}>
          <Button variant="primary" onClick={() => void retry()} disabled={retrying}>
            {m.retry}
          </Button>
          <Button variant="outline" onClick={() => void copyDetails()}>
            {t.common.copyDetails}
          </Button>
        </div>
        {note && (
          <p className={styles.note} role="status">
            {note}
          </p>
        )}
      </div>
    </main>
  );
}
