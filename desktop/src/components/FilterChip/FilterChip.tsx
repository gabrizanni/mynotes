import { Check, type LucideIcon } from "lucide-react";

import { cx } from "../../design/cx";
import { Icon } from "../Icon";
import styles from "./FilterChip.module.css";

export interface FilterChipProps {
  label: string;
  pressed: boolean;
  onChange: (pressed: boolean) => void;
  /** How many items the filter would show; spoken with the label. */
  count?: number;
  icon?: LucideIcon;
}

/** A toggle chip (aria-pressed). On shows a check mark, not just a fill. */
export function FilterChip({ label, pressed, onChange, count, icon }: FilterChipProps) {
  return (
    <button type="button" aria-pressed={pressed} className={cx(styles.chip, pressed && styles.on)} onClick={() => onChange(!pressed)}>
      {pressed ? <Icon icon={Check} size={14} className={styles.icon} /> : icon && <Icon icon={icon} size={14} className={styles.icon} />}
      <span>{label}</span>
      {count !== undefined && <span className={styles.count}>{count}</span>}
    </button>
  );
}
