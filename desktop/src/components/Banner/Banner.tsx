import { CircleAlert, CircleCheck, Info, TriangleAlert, X, type LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { cx } from "../../design/cx";
import { useT } from "../../i18n";
import { Button, IconButton } from "../Button";
import { Icon } from "../Icon";
import styles from "./Banner.module.css";

export type BannerTone = "info" | "success" | "warning" | "danger";

const icons: Record<BannerTone, LucideIcon> = { info: Info, success: CircleCheck, warning: TriangleAlert, danger: CircleAlert };

export interface BannerProps {
  tone: BannerTone;
  /** The problem, in one sentence (UX-SPEC §5: problem plus one fix). */
  children: ReactNode;
  action?: { label: string; onSelect: () => void };
  onDismiss?: () => void;
}

export function Banner({ tone, children, action, onDismiss }: BannerProps) {
  const t = useT();
  return (
    <div className={cx(styles.banner, styles[tone])} role={tone === "danger" ? "alert" : "status"}>
      <Icon icon={icons[tone]} size={20} className={styles.icon} />
      <p className={styles.message}>{children}</p>
      {(action ?? onDismiss) && (
        <div className={styles.actions}>
          {action && (
            <Button variant="outline" size="sm" onClick={action.onSelect}>
              {action.label}
            </Button>
          )}
          {onDismiss && <IconButton label={t.common.dismiss} icon={X} size="sm" onClick={onDismiss} />}
        </div>
      )}
    </div>
  );
}
