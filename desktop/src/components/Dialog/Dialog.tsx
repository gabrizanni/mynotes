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
  /** `lg` (640 px) for forms with a row per item, such as the audio import. */
  size?: "md" | "lg";
}

export function Dialog({ open, onClose, title, description, children, footer, role = "dialog", inline = false, size = "md" }: DialogProps) {
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
      <div role={role} aria-labelledby={titleId} className={`${styles.dialog} ${size === "lg" ? styles.lg : ""} ${styles.inlinePanel}`}>
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
      className={size === "lg" ? `${styles.dialog} ${styles.lg}` : styles.dialog}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {open && inner}
    </dialog>
  );
}
