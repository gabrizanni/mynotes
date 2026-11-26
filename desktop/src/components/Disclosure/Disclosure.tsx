import { ChevronDown } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

import { cx } from "../../design/cx";
import { Icon } from "../Icon";
import styles from "./Disclosure.module.css";

export interface DisclosureProps {
  /** Label of the toggle, e.g. "More options". */
  label: string;
  /** Short summary shown next to the label while closed. */
  summary?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}

/** Progressive disclosure: a button with aria-expanded that shows a region below it. */
export function Disclosure({ label, summary, defaultOpen = false, children }: DisclosureProps) {
  const [open, setOpen] = useState(defaultOpen);
  const regionId = useId();
  return (
    <div className={styles.disclosure}>
      <div className={styles.head}>
        <button type="button" className={styles.toggle} aria-expanded={open} aria-controls={regionId} onClick={() => setOpen((value) => !value)}>
          <Icon icon={ChevronDown} size={16} className={cx(styles.chevron, open && styles.open)} />
          <span className={styles.label}>{label}</span>
        </button>
        {/* Outside the button so its name stays the label alone. */}
        {!open && summary && (
          <span className={styles.summary} aria-hidden="true">
            {summary}
          </span>
        )}
      </div>
      <div id={regionId} className={styles.region} hidden={!open}>
        {children}
      </div>
    </div>
  );
}
