import { MoreHorizontal, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { cx } from "../../design/cx";
import { IconButton } from "../Button";
import { Icon } from "../Icon";
import styles from "./Menu.module.css";

export interface MenuItem {
  id: string;
  label: string;
  icon?: LucideIcon;
  /** Red label for actions that remove something; still confirmed by a dialog. */
  destructive?: boolean;
  disabled?: boolean;
  onSelect: () => void;
}

export interface MenuProps {
  /** Accessible name of the icon-only trigger, e.g. "Lesson actions". */
  label: string;
  items: readonly MenuItem[];
  icon?: LucideIcon;
  align?: "start" | "end";
  /** Start open (gallery). */
  defaultOpen?: boolean;
}

/**
 * The ⋯ menu: an icon button that opens a `role="menu"`. Arrow keys, Home/End move, Enter or
 * Space selects, Esc and Tab close; focus returns to the trigger.
 */
export function Menu({ label, items, icon = MoreHorizontal, align = "end", defaultOpen = false }: MenuProps) {
  const [open, setOpen] = useState(defaultOpen);
  const anchor = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const menuId = useId();
  const enabled = items.map((item, index) => (item.disabled ? -1 : index)).filter((index) => index >= 0);

  useEffect(() => {
    if (!open) return undefined;
    if (!defaultOpen) refs.current[enabled[0] ?? 0]?.focus();
    const onPointer = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
    // Focus moves once, when the menu opens; `enabled` only changes with the items.
  }, [open, defaultOpen]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) trigger.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const current = refs.current.findIndex((node) => node === document.activeElement);
    const position = enabled.indexOf(current);
    let next: number | undefined;
    if (event.key === "ArrowDown") next = enabled[(position + 1) % enabled.length];
    else if (event.key === "ArrowUp") next = enabled[(position - 1 + enabled.length) % enabled.length];
    else if (event.key === "Home") next = enabled[0];
    else if (event.key === "End") next = enabled[enabled.length - 1];
    else if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    } else if (event.key === "Tab") {
      close(false);
      return;
    }
    if (next === undefined) return;
    event.preventDefault();
    refs.current[next]?.focus();
  };

  return (
    <div className={styles.anchor} ref={anchor}>
      <IconButton
        ref={trigger}
        label={label}
        icon={icon}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      />
      {open && (
        <div id={menuId} role="menu" aria-label={label} className={cx(styles.menu, align === "end" && styles.end)} onKeyDown={onKeyDown}>
          {items.map((item, index) => (
            <button
              key={item.id}
              ref={(node) => {
                refs.current[index] = node;
              }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              disabled={item.disabled}
              className={cx(styles.item, item.destructive && styles.destructive)}
              onClick={() => {
                close();
                item.onSelect();
              }}
            >
              {item.icon && <Icon icon={item.icon} size={16} />}
              <span>{item.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
