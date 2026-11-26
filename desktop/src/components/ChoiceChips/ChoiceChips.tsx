import { Check } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Icon } from "../Icon";
import styles from "./ChoiceChips.module.css";

export interface ChoiceChip<V extends string> {
  value: V;
  label: string;
}

export interface ChoiceChipsProps<V extends string> {
  label: ReactNode;
  hideLabel?: boolean;
  chips: readonly ChoiceChip<V>[];
  selected: readonly V[];
  onChange: (selected: V[]) => void;
  hint?: ReactNode;
}

/** Optional multi-select chips: each is a toggle button (aria-pressed) with a check when on. */
export function ChoiceChips<V extends string>({ label, hideLabel = false, chips, selected, onChange, hint }: ChoiceChipsProps<V>) {
  const labelId = useId();
  const hintId = useId();
  const toggle = (value: V) => onChange(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value]);
  return (
    <div className={styles.field}>
      <span id={labelId} className={hideLabel ? "visually-hidden" : styles.label}>
        {label}
      </span>
      <div role="group" aria-labelledby={labelId} aria-describedby={hint ? hintId : undefined} className={styles.chips}>
        {chips.map((chip) => {
          const on = selected.includes(chip.value);
          return (
            <button key={chip.value} type="button" aria-pressed={on} className={styles.chip} onClick={() => toggle(chip.value)}>
              {on && <Icon icon={Check} size={14} />}
              <span>{chip.label}</span>
            </button>
          );
        })}
      </div>
      {hint && (
        <p id={hintId} className={styles.hint}>
          {hint}
        </p>
      )}
    </div>
  );
}
