import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cx } from "../../design/cx";
import { Icon } from "../Icon";
import styles from "./EmptyState.module.css";

export interface EmptyStateProps {
  icon: LucideIcon;
  /** One line (UX-SPEC §4). */
  title: string;
  body?: string;
  /** One action, usually a soft button. */
  action?: ReactNode;
  align?: "start" | "center";
}

export function EmptyState({ icon, title, body, action, align = "center" }: EmptyStateProps) {
  return (
    <div className={cx(styles.empty, align === "center" && styles.centered)}>
      <Icon icon={icon} size={24} className={styles.icon} />
      <p className={styles.title}>{title}</p>
      {body && <p className={styles.body}>{body}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}
