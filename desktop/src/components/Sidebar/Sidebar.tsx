import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { Icon } from "../Icon";
import styles from "./Sidebar.module.css";

export interface SidebarItem {
  id: string;
  label: string;
  icon: LucideIcon;
  href: string;
  /** Visible shortcut hint, e.g. "Ctrl+1" (shown on hover and focus). */
  shortcut?: string;
  badge?: { value: number; label: string };
  children?: ReadonlyArray<{ id: string; label: string; href: string; current: boolean }>;
}

export interface SidebarAction {
  label: string;
  icon: LucideIcon;
  shortcut?: string;
  pressed?: boolean;
  onSelect: () => void;
}

export interface SidebarProps {
  logoSrc: string;
  items: readonly SidebarItem[];
  activeId: string | null;
  /** Recorder status row (UX-SPEC §1, sidebar bottom). */
  status?: ReactNode;
  actions: readonly SidebarAction[];
}

/** Section navigation as real links (hash routes), plus the bottom recorder/assistant/settings group. */
export function Sidebar({ logoSrc, items, activeId, status, actions }: SidebarProps) {
  const t = useT();
  return (
    <div className={styles.sidebar}>
      <div className={styles.brand}>
        <img className={styles.logo} src={logoSrc} alt="" width={28} height={28} />
        <span className={styles.wordmark}>{t.common.appName}</span>
      </div>
      <nav className={styles.nav} aria-label={t.nav.mainLabel}>
        <ul className={styles.list} role="list">
          {items.map((item) => (
            <li key={item.id}>
              <a className={styles.item} href={item.href} aria-current={item.id === activeId ? "page" : undefined}>
                <Icon icon={item.icon} size={20} className={styles.itemIcon} />
                <span className={styles.itemLabel}>{item.label}</span>
                {item.badge && item.badge.value > 0 ? (
                  <span className={styles.badge} aria-label={item.badge.label}>
                    {item.badge.value}
                  </span>
                ) : (
                  item.shortcut && (
                    <kbd className={styles.kbd} aria-hidden="true">
                      {item.shortcut}
                    </kbd>
                  )
                )}
              </a>
              {item.children && item.children.length > 0 && (
                <ul className={styles.courses} role="list">
                  {item.children.map((child) => (
                    <li key={child.id}>
                      <a className={styles.course} href={child.href} aria-current={child.current ? "page" : undefined}>
                        {child.label}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      </nav>
      <div className={styles.footer}>
        {status}
        {actions.map((action) => (
          <button key={action.label} type="button" className={styles.item} aria-pressed={action.pressed} onClick={action.onSelect}>
            <Icon icon={action.icon} size={20} className={styles.itemIcon} />
            <span className={styles.itemLabel}>{action.label}</span>
            {action.shortcut && (
              <kbd className={styles.kbd} aria-hidden="true">
                {action.shortcut}
              </kbd>
            )}
          </button>
        ))}
      </div>
    </div>
  );
}

export { styles as sidebarStyles };
