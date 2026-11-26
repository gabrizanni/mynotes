import { Minus, Plus } from "lucide-react";
import type { KeyboardEvent, ReactNode } from "react";

import { useT } from "../../i18n";
import { Field } from "../Field";
import { Icon } from "../Icon";
import styles from "./Stepper.module.css";

export interface StepperProps {
  label: ReactNode;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (value: number) => void;
  hint?: ReactNode;
  /** Spoken value, e.g. "3 images". Defaults to the number. */
  valueText?: (value: number) => string;
}

/** ARIA spinbutton: one tab stop, arrows/Home/End on the value; −/+ for the pointer. */
export function Stepper({ label, value, min, max, step = 1, onChange, hint, valueText }: StepperProps) {
  const t = useT();
  const clamp = (next: number) => Math.min(max, Math.max(min, next));
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const next: Record<string, number> = {
      ArrowUp: value + step,
      ArrowRight: value + step,
      ArrowDown: value - step,
      ArrowLeft: value - step,
      PageUp: value + step * 5,
      PageDown: value - step * 5,
      Home: min,
      End: max,
    };
    const target = next[event.key];
    if (target === undefined) return;
    event.preventDefault();
    onChange(clamp(target));
  };
  return (
    <Field label={label} hint={hint} labelAs="span">
      {({ id, "aria-describedby": describedBy }) => (
        <div className={styles.stepper}>
          <button
            type="button"
            tabIndex={-1}
            className={styles.step}
            aria-label={t.common.decrease}
            disabled={value <= min}
            onClick={() => onChange(clamp(value - step))}
          >
            <Icon icon={Minus} size={16} />
          </button>
          <div
            role="spinbutton"
            tabIndex={0}
            id={id}
            aria-labelledby={`${id}-label`}
            aria-describedby={describedBy}
            aria-valuenow={value}
            aria-valuemin={min}
            aria-valuemax={max}
            aria-valuetext={valueText?.(value)}
            className={styles.value}
            onKeyDown={onKeyDown}
          >
            {value}
          </div>
          <button
            type="button"
            tabIndex={-1}
            className={styles.step}
            aria-label={t.common.increase}
            disabled={value >= max}
            onClick={() => onChange(clamp(value + step))}
          >
            <Icon icon={Plus} size={16} />
          </button>
        </div>
      )}
    </Field>
  );
}
