import { useId, type HTMLAttributes, type ReactNode } from "react";

import { cx } from "../../design/cx";
import styles from "./Card.module.css";

export type CardVariant = "default" | "emphasis" | "inverse";

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  variant?: CardVariant;
  padding?: "none" | "md" | "lg";
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** Heading level of the title, to keep the page outline sequential. */
  headingLevel?: 2 | 3;
  /** `section` when the card has a title (a landmark-like group), `div` otherwise. */
  as?: "section" | "div" | "article";
  children?: ReactNode;
}

const padClass = { none: styles.padNone, md: styles.padMd, lg: styles.padLg };

export function Card({
  variant = "default",
  padding = "md",
  title,
  description,
  actions,
  headingLevel = 2,
  as,
  className,
  children,
  ...rest
}: CardProps) {
  const titleId = useId();
  const Element = as ?? (title ? "section" : "div");
  const Heading = headingLevel === 2 ? "h2" : "h3";
  return (
    <Element
      aria-labelledby={title ? titleId : undefined}
      className={cx(styles.card, variant !== "default" && styles[variant], variant === "inverse" && "surface-inverse", padClass[padding], className)}
      {...rest}
    >
      {(title || actions) && (
        <header className={styles.header}>
          <div className={styles.heading}>
            {title && (
              <Heading id={titleId} className={styles.title}>
                {title}
              </Heading>
            )}
            {description && <p className={styles.description}>{description}</p>}
          </div>
          {actions && <div className={styles.actions}>{actions}</div>}
        </header>
      )}
      {children}
    </Element>
  );
}
