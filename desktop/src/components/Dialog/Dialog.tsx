import { X } from "lucide-react";
import { useId, type ReactNode } from "react";

import { useT } from "../../i18n";
import { IconButton } from "../Button";
import styles from "./Dialog.module.css";
import { useNativeDialog } from "./useNativeDialog";

export interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  /** Buttons, primary last (it sits on the right). */
  footer?: ReactNode;
  /** `alertdialog` for confirmations of destructive or hard-to-undo actions. */
  role?: "dialog" | "alertdialog";
  /** Render the panel in place, without a modal layer (component gallery, docs). */
  inline?: boolean;
}

export function Dialog({ open, onClose, title, description, children, footer, role = "dialog", inline = false }: DialogProps) {
  const t = useT();
  const ref = useNativeDialog(open && !inline, onClose);
  const titleId = useId();
  const descriptionId = useId();
  const inner = (
    <div className={styles.inner}>
      <div className={styles.header}>
        <h2 id={titleId} className={styles.title}>
          {title}
        </h2>
        <IconButton label={t.common.close} icon={X} size="sm" className={styles.close} onClick={onClose} />
      </div>
      {description && (
        <p id={descriptionId} className={styles.description}>
          {description}
        </p>
      )}
      {children}
      {footer && <div className={styles.footer}>{footer}</div>}
    </div>
  );
  if (inline) {
    return (
      <div role={role} aria-labelledby={titleId} className={`${styles.dialog} ${styles.inlinePanel}`}>
        {inner}
      </div>
    );
  }
  return (
    <dialog
      ref={ref}
      role={role}
      aria-labelledby={titleId}
      aria-describedby={description ? descriptionId : undefined}
      className={styles.dialog}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {open && inner}
    </dialog>
  );
}
