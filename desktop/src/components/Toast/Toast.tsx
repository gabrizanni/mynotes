import { CircleAlert, CircleCheck, Info, TriangleAlert, X, type LucideIcon } from "lucide-react";
import { useEffect } from "react";

import { cx } from "../../design/cx";
import { useT } from "../../i18n";
import { Icon } from "../Icon";
import styles from "./Toast.module.css";

export type ToastTone = "neutral" | "success" | "warning" | "danger";

export interface ToastItem {
  id: string;
  message: string;
  tone: ToastTone;
  action?: { label: string; run: () => void };
}

const icons: Record<ToastTone, LucideIcon> = { neutral: Info, success: CircleCheck, warning: TriangleAlert, danger: CircleAlert };

/** 6 s, bottom-left, with one optional action (UX-SPEC §4 Feedback). */
export const TOAST_MS = 6000;

function ToastView({ toast, onDismiss }: { toast: ToastItem; onDismiss: (id: string) => void }) {
  const t = useT();
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(toast.id), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toast.id, onDismiss]);
  return (
    <div className={cx(styles.toast, styles[toast.tone])}>
      <Icon icon={icons[toast.tone]} size={20} className={styles.icon} />
      <p className={styles.message}>{toast.message}</p>
      {toast.action && (
        <button
          type="button"
          className={styles.action}
          onClick={() => {
            toast.action?.run();
            onDismiss(toast.id);
          }}
        >
          {toast.action.label}
        </button>
      )}
      <button type="button" className={styles.dismiss} aria-label={t.common.dismiss} onClick={() => onDismiss(toast.id)}>
        <Icon icon={X} size={16} />
      </button>
    </div>
  );
}

/** Always mounted so screen readers hear new toasts (aria-live polite). */
export function ToastRegion({ toasts, onDismiss, inline = false }: { toasts: readonly ToastItem[]; onDismiss: (id: string) => void; inline?: boolean }) {
  return (
    <div className={inline ? undefined : styles.region} role="status" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <ToastView key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  );
}
