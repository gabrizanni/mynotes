import { ChevronDown } from "lucide-react";
import type { ReactNode, SelectHTMLAttributes } from "react";

import { cx } from "../../design/cx";
import { Field, fieldStyles } from "../Field";
import { Icon } from "../Icon";
import styles from "./Select.module.css";

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface SelectProps extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "children"> {
  label: ReactNode;
  options: readonly SelectOption[];
  hint?: ReactNode;
  error?: ReactNode;
  optional?: boolean;
  id?: string;
  /** Screen-reader-only label when a row already names the control (import destinations). */
  hideLabel?: boolean;
}

/** Native <select> for full keyboard and screen-reader support, with the app's skin. */
export function Select({ label, options, hint, error, optional, id, className, hideLabel, ...rest }: SelectProps) {
  return (
    <Field label={label} hint={hint} error={error} optional={optional} id={id} hideLabel={hideLabel}>
      {(a11y) => (
        <div className={styles.wrap}>
          <select className={cx(fieldStyles.control, styles.select, className)} {...a11y} {...rest}>
            {options.map((option) => (
              <option key={option.value} value={option.value} disabled={option.disabled}>
                {option.label}
              </option>
            ))}
          </select>
          <Icon icon={ChevronDown} size={16} className={styles.chevron} />
        </div>
      )}
    </Field>
  );
}
