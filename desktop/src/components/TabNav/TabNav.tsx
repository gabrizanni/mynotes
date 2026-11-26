import { cx } from "../../design/cx";
import styles from "./TabNav.module.css";

export interface TabNavItem {
  id: string;
  label: string;
  href: string;
}

export interface TabNavProps {
  /** Accessible name of the navigation, e.g. "Statistics sections". */
  label: string;
  items: readonly TabNavItem[];
  current: string;
}

/**
 * Section tabs that are links (each tab has its own URL, Back works). Uses `nav` +
 * `aria-current="page"`, not the ARIA tabs pattern, because every tab is a separate page.
 */
export function TabNav({ label, items, current }: TabNavProps) {
  return (
    <nav aria-label={label} className={styles.nav}>
      <ul role="list" className={styles.list}>
        {items.map((item) => (
          <li key={item.id}>
            <a href={item.href} className={cx(styles.tab, item.id === current && styles.current)} aria-current={item.id === current ? "page" : undefined}>
              {item.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
