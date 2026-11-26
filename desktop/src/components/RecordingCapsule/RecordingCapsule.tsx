import { CircleAlert, CircleCheck, LoaderCircle, MicOff } from "lucide-react";
import type { ReactNode } from "react";

import { cx } from "../../design/cx";
import { useT } from "../../i18n";
import { Icon } from "../Icon";
import styles from "./RecordingCapsule.module.css";

/** Visual states of the floating recorder (UX-SPEC §3 Recording capsule). No fake level bars. */
export type CapsuleState =
  | { kind: "upcoming"; course: string; minutes: number }
  | { kind: "starting" }
  | { kind: "recording"; elapsed: string; title: string }
  | { kind: "saving" }
  | { kind: "saved"; duration: string }
  | { kind: "problem"; problem: "microphone" | "failed" };

export interface RecordingCapsuleProps {
  state: CapsuleState;
  /** Force the expanded layout (gallery, focus) instead of hover-to-expand. */
  expanded?: boolean;
  onStop?: () => void;
  onSkip?: () => void;
  onTranscribe?: () => void;
  onFix?: () => void;
}

export function RecordingCapsule({ state, expanded = false, onStop, onSkip, onTranscribe, onFix }: RecordingCapsuleProps) {
  const t = useT();
  const c = t.capture;
  let status: ReactNode;
  let details: ReactNode = null;
  let action: ReactNode = null;

  switch (state.kind) {
    case "upcoming":
      status = <span>{c.upcoming(state.course, state.minutes)}</span>;
      action = onSkip && (
        <button type="button" className={styles.action} onClick={onSkip}>
          {c.skip}
        </button>
      );
      break;
    case "starting":
      status = (
        <span className={styles.status}>
          <Icon icon={LoaderCircle} size={16} className={styles.spinner} />
          {c.starting}
        </span>
      );
      break;
    case "recording":
      status = (
        <span className={styles.status}>
          <span className={styles.dot} aria-hidden="true" />
          <span className={styles.rec}>{c.rec}</span>
          <span className={styles.time}>{state.elapsed}</span>
        </span>
      );
      details = state.title;
      action = (
        <button type="button" className={cx(styles.action, styles.stop)} onClick={onStop}>
          <span className={styles.stopGlyph} aria-hidden="true" />
          {c.stop}
        </button>
      );
      break;
    case "saving":
      status = (
        <span className={styles.status}>
          <Icon icon={LoaderCircle} size={16} className={styles.spinner} />
          {c.saving}
        </span>
      );
      break;
    case "saved":
      status = (
        <span className={styles.status}>
          <Icon icon={CircleCheck} size={16} className={styles.ok} />
          {c.saved(state.duration)}
        </span>
      );
      action = onTranscribe && (
        <button type="button" className={styles.action} onClick={onTranscribe}>
          {c.transcribe}
        </button>
      );
      break;
    case "problem":
      status = (
        <span className={styles.status}>
          <Icon icon={state.problem === "microphone" ? MicOff : CircleAlert} size={16} className={styles.problem} />
          {state.problem === "microphone" ? c.micBlocked : c.failed}
        </span>
      );
      action = (
        <button type="button" className={styles.action} onClick={onFix}>
          {state.problem === "microphone" ? c.fix : c.details}
        </button>
      );
      break;
  }

  return (
    <div className={cx(styles.capsule, expanded && styles.expanded)} role="status" aria-label={c.label}>
      {status}
      {details && (
        <span className={styles.details}>
          <span className={styles.detailsInner}>· {details}</span>
        </span>
      )}
      {action}
    </div>
  );
}
