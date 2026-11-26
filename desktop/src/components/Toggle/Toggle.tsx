import { useId, type ReactNode } from "react";

import styles from "./Toggle.module.css";

export interface ToggleProps {
  label: ReactNode;
  description?: ReactNode;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}

/** A labelled switch row: the label toggles too, the switch says on/off to screen readers. */
export function Toggle({ label, description, checked, onChange, disabled }: ToggleProps) {
  const id = useId();
  const descriptionId = description ? `${id}-description` : undefined;
  return (
    <div className={styles.row}>
      <div className={styles.text}>
        <label className={styles.label} htmlFor={id}>
          {label}
        </label>
        {description && (
          <span className={styles.description} id={descriptionId}>
            {description}
          </span>
        )}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-describedby={descriptionId}
        disabled={disabled}
        className={styles.switch}
        onClick={() => onChange(!checked)}
      >
        <span className={styles.mark} aria-hidden="true" />
      </button>
    </div>
  );
}
