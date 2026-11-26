import { Check, type LucideIcon } from "lucide-react";
import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { cx } from "../../design/cx";
import { Icon } from "../Icon";
import styles from "./ChoiceCards.module.css";

export interface ChoiceCardOption<V extends string> {
  value: V;
  title: string;
  description?: string;
  icon?: LucideIcon;
  disabled?: boolean;
}

export interface ChoiceCardsProps<V extends string> {
  /** Names the group for assistive tech; shown above the cards unless `hideLabel`. */
  label: ReactNode;
  hideLabel?: boolean;
  options: readonly ChoiceCardOption<V>[];
  /** `null` until the student picks one: no option is preselected. */
  value: V | null;
  onChange: (value: V) => void;
  /** Cards per row from 720 px of container width; they stack below. */
  columns?: 1 | 2 | 3;
}

/**
 * Large single-choice cards (a radiogroup). Tab enters once, arrows move and select, the
 * check mark and the tinted surface show the choice, never colour alone.
 */
export function ChoiceCards<V extends string>({ label, hideLabel = false, options, value, onChange, columns = 3 }: ChoiceCardsProps<V>) {
  const labelId = useId();
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const focusable = options.findIndex((option) => option.value === value);
  const firstEnabled = options.findIndex((option) => !option.disabled);
  const tabStop = focusable >= 0 ? focusable : firstEnabled;

  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const steps: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    const step = steps[event.key];
    if (step === undefined) return;
    event.preventDefault();
    for (let i = 1; i <= options.length; i += 1) {
      const candidate = (index + step * i + options.length) % options.length;
      const option = options[candidate];
      if (option && !option.disabled) {
        onChange(option.value);
        refs.current[candidate]?.focus();
        return;
      }
    }
  };

  return (
    <div className={styles.wrap}>
      <span id={labelId} className={hideLabel ? "visually-hidden" : styles.label}>
        {label}
      </span>
      <div role="radiogroup" aria-labelledby={labelId} className={cx(styles.grid, styles[`cols${columns}`])}>
        {options.map((option, index) => {
          const checked = option.value === value;
          return (
            <button
              key={option.value}
              ref={(node) => {
                refs.current[index] = node;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              tabIndex={index === tabStop ? 0 : -1}
              disabled={option.disabled}
              className={styles.card}
              onClick={() => onChange(option.value)}
              onKeyDown={(event) => move(event, index)}
            >
              {option.icon && <Icon icon={option.icon} size={24} className={styles.icon} />}
              <span className={styles.text}>
                <span className={styles.title}>{option.title}</span>
                {option.description && <span className={styles.description}>{option.description}</span>}
              </span>
              <span className={styles.mark} aria-hidden="true">
                {checked && <Icon icon={Check} size={14} />}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
