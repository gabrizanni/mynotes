import { ChevronRight } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";

import { cx } from "../../design/cx";
import { Icon } from "../Icon";
import styles from "./ListRow.module.css";

export interface ListRowProps {
  title: ReactNode;
  description?: ReactNode;
  leading?: ReactNode;
  /** Trailing content, usually a StatusChip. */
  meta?: ReactNode;
  /** Makes the row a button. */
  onSelect?: () => void;
  /** Makes the row a link. */
  href?: string;
  chevron?: boolean;
  className?: string;
}

/** A 48 px row. Interactive rows are one big button or link, never a div with a click handler. */
export function ListRow({ title, description, leading, meta, onSelect, href, chevron, className }: ListRowProps) {
  const interactive = Boolean(onSelect ?? href);
  const content = (
    <>
      {leading && <span className={styles.leading}>{leading}</span>}
      <span className={styles.text}>
        <span className={styles.title}>{title}</span>
        {description && <span className={styles.description}>{description}</span>}
      </span>
      {meta && <span className={styles.meta}>{meta}</span>}
      {(chevron ?? interactive) && interactive && <Icon icon={ChevronRight} size={16} className={styles.chevron} />}
    </>
  );
  const classes = cx(styles.row, interactive && styles.interactive, className);
  if (href) {
    return (
      <a href={href} className={classes}>
        {content}
      </a>
    );
  }
  if (onSelect) {
    return (
      <button type="button" className={classes} onClick={onSelect}>
        {content}
      </button>
    );
  }
  return <div className={classes}>{content}</div>;
}

/** Semantic list with hairline separators between rows. */
export function List({ children, className, ...rest }: HTMLAttributes<HTMLUListElement>) {
  return (
    <ul role="list" className={cx(styles.list, className)} {...rest}>
      {children}
    </ul>
  );
}
