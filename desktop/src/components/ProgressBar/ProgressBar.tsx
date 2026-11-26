import { useId } from "react";

import { cx } from "../../design/cx";
import styles from "./ProgressBar.module.css";

export interface ProgressBarProps {
  label: string;
  /** Real measured progress only (bytes, items). No indeterminate or invented values. */
  value: number;
  max: number;
  /** Visible and spoken value, e.g. "212 / 300 MB". */
  valueText: string;
  onInverse?: boolean;
}

/** Determinate progress (UX-SPEC §4: real bytes only; use StageStepper otherwise). */
export function ProgressBar({ label, value, max, valueText, onInverse = false }: ProgressBarProps) {
  const id = useId();
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  return (
    <div className={cx(styles.progress, onInverse && styles.inverse)}>
      <div className={styles.row}>
        <span className={styles.label} id={id}>
          {label}
        </span>
        <span className={styles.value} aria-hidden="true">
          {valueText}
        </span>
      </div>
      <div
        className={styles.track}
        role="progressbar"
        aria-labelledby={id}
        aria-valuemin={0}
        aria-valuemax={max}
        aria-valuenow={Math.min(max, Math.max(0, value))}
        aria-valuetext={valueText}
      >
        <div className={styles.fill} style={{ transform: `scaleX(${ratio})` }} />
      </div>
    </div>
  );
}
