import { X } from "lucide-react";
import { useId, type ReactNode } from "react";

import { useT } from "../../i18n";
import { IconButton } from "../Button";
import { useNativeDialog } from "../Dialog";
import styles from "./Sheet.module.css";

export interface SheetProps {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  /** Render the panel in place, without a modal layer (component gallery, docs). */
  inline?: boolean;
}

/** A modal side panel for multi-step tasks (e.g. "Notes for Lesson 3 · Statistics"). */
export function Sheet({ open, onClose, title, children, footer, inline = false }: SheetProps) {
  const t = useT();
  const ref = useNativeDialog(open && !inline, onClose);
  const titleId = useId();
  const content = (
    <>
      <div className={styles.header}>
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
        <IconButton label={t.common.close} icon={X} size="sm" onClick={onClose} />
      </div>
      <div className={styles.body}>{children}</div>
      {footer && <div className={styles.footer}>{footer}</div>}
    </>
  );
  if (inline) {
    return (
      <div role="dialog" aria-labelledby={titleId} className={`${styles.sheet} ${styles.inlinePanel}`}>
        {content}
      </div>
    );
  }
  return (
    <dialog ref={ref} aria-labelledby={titleId} className={styles.sheet}>
      {open && content}
    </dialog>
  );
}
