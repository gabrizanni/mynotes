import type { CSSProperties } from "react";

import { cx } from "../../design/cx";
import styles from "./Slider.module.css";

export interface SliderProps {
  /** Accessible name, e.g. "Position". */
  label: string;
  value: number;
  min?: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  /** Spoken value, e.g. "12:04 of 1:32:10". */
  valueText?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * A native range input (keyboard and screen readers for free) over a drawn track: the played
 * part is a solid fill scaled with `transform`, so moving it never triggers layout.
 */
export function Slider({ label, value, min = 0, max, step = 1, onChange, valueText, disabled, className }: SliderProps) {
  const span = max - min;
  const fill = span > 0 ? Math.min(1, Math.max(0, (value - min) / span)) : 0;
  return (
    <div className={cx(styles.wrap, disabled && styles.disabled, className)} style={{ "--fill": fill } as CSSProperties}>
      <span className={styles.track} aria-hidden="true">
        <span className={styles.fill} />
      </span>
      <input
        type="range"
        className={styles.input}
        aria-label={label}
        aria-valuetext={valueText}
        min={min}
        max={max > min ? max : min + 1}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </div>
  );
}
