import { LoaderCircle } from "lucide-react";

import { cx } from "../../design/cx";
import { useT } from "../../i18n";
import { Icon } from "../Icon";
import { chipFor, type ChipKind } from "./statusMap";
import styles from "./StatusChip.module.css";

export interface StatusChipProps {
  kind: ChipKind;
  /** Raw backend status, e.g. "AWAITING_APPROVAL"; mapped to a label, never shown as is. */
  status: string;
  onInverse?: boolean;
  className?: string;
}

/** Icon + label, never color alone (WCAG 1.4.1). A live recording shows a pulsing dot. */
export function StatusChip({ kind, status, onInverse = false, className }: StatusChipProps) {
  const t = useT();
  const spec = chipFor(kind, status);
  const spinning = spec.busy === true && spec.icon === LoaderCircle;
  return (
    <span className={cx(styles.chip, onInverse ? styles.onInverse : styles[spec.tone], className)} data-status={status}>
      {spec.tone === "live" ? (
        <span className={styles.dot} aria-hidden="true" />
      ) : (
        <Icon icon={spec.icon} size={14} className={spinning ? styles.busy : styles.still} />
      )}
      {spec.label(t.status)}
    </span>
  );
}
