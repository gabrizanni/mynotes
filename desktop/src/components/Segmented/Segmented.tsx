import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { Field } from "../Field";
import styles from "./Segmented.module.css";

export interface SegmentedOption<V extends string> {
  value: V;
  label: string;
  disabled?: boolean;
}

export interface SegmentedProps<V extends string> {
  label: ReactNode;
  /** Hide the visible label when the context already names the control. */
  hideLabel?: boolean;
  options: readonly SegmentedOption<V>[];
  value: V;
  onChange: (value: V) => void;
  hint?: ReactNode;
}

/** A radiogroup with roving focus: Tab enters once, arrow keys move and select. */
export function Segmented<V extends string>({ label, hideLabel = false, options, value, onChange, hint }: SegmentedProps<V>) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const fallbackId = useId();
  const enabled = options.filter((option) => !option.disabled);

  const move = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const keys: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
    let target: number | null = null;
    if (event.key in keys) {
      const step = keys[event.key] ?? 0;
      for (let i = 1; i <= options.length; i += 1) {
        const candidate = (index + step * i + options.length) % options.length;
        if (!options[candidate]?.disabled) {
          target = candidate;
          break;
        }
      }
    } else if (event.key === "Home" || event.key === "End") {
      const edge = event.key === "Home" ? enabled[0] : enabled[enabled.length - 1];
      target = edge ? options.indexOf(edge) : null;
    }
    const option = target === null ? undefined : options[target];
    if (target === null || !option) return;
    event.preventDefault();
    onChange(option.value);
    refs.current[target]?.focus();
  };

  const group = (labelledBy: string) => (
    <div role="radiogroup" aria-labelledby={labelledBy} className={styles.group}>
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
            tabIndex={checked ? 0 : -1}
            disabled={option.disabled}
            className={styles.option}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => move(event, index)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );

  if (hideLabel) {
    return (
      <>
        <span id={fallbackId} className="visually-hidden">
          {label}
        </span>
        {group(fallbackId)}
      </>
    );
  }
  return (
    <Field label={label} hint={hint} labelAs="span">
      {({ id }) => group(`${id}-label`)}
    </Field>
  );
}
