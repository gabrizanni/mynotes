import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { cx } from "../../design/cx";
import styles from "./ChoiceList.module.css";

export interface Choice<V extends string> {
  value: V;
  label: string;
  /** One line that explains the choice (e.g. "Usually under 10 min."). */
  description?: ReactNode;
  /** Trailing detail, e.g. a duration in tabular numbers. */
  meta?: ReactNode;
  disabled?: boolean;
}

export interface ChoiceListProps<V extends string> {
  label: ReactNode;
  hideLabel?: boolean;
  choices: readonly Choice<V>[];
  value: V;
  onChange: (value: V) => void;
}

/**
 * Radio rows with a one-line explanation each: for choices that need words, where a
 * Segmented control would hide them. Roving focus: Tab enters once, arrows move and select.
 */
export function ChoiceList<V extends string>({ label, hideLabel = false, choices, value, onChange }: ChoiceListProps<V>) {
  const labelId = useId();
  const refs = useRef<Array<HTMLButtonElement | null>>([]);

  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const step = event.key === "ArrowDown" || event.key === "ArrowRight" ? 1 : event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) return;
    event.preventDefault();
    for (let i = 1; i <= choices.length; i += 1) {
      const target = (index + step * i + choices.length) % choices.length;
      const choice = choices[target];
      if (choice && !choice.disabled) {
        onChange(choice.value);
        refs.current[target]?.focus();
        return;
      }
    }
  };

  return (
    <div className={styles.field}>
      <span id={labelId} className={hideLabel ? "visually-hidden" : styles.label}>
        {label}
      </span>
      <div role="radiogroup" aria-labelledby={labelId} className={styles.list}>
        {choices.map((choice, index) => {
          const checked = choice.value === value;
          return (
            <button
              key={choice.value}
              ref={(node) => {
                refs.current[index] = node;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={checked ? 0 : -1}
              disabled={choice.disabled}
              className={cx(styles.choice, checked && styles.checked)}
              onClick={() => onChange(choice.value)}
              onKeyDown={(event) => move(event, index)}
            >
              <span className={styles.radio} aria-hidden="true" />
              <span className={styles.text}>
                <span className={styles.title}>{choice.label}</span>
                {choice.description && <span className={styles.description}>{choice.description}</span>}
              </span>
              {choice.meta && <span className={styles.meta}>{choice.meta}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
