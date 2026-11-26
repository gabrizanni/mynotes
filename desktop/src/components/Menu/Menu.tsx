import { ChevronDown, MoreHorizontal, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";

import { cx } from "../../design/cx";
import { Button, IconButton, type ButtonSize, type ButtonVariant } from "../Button";
import { Icon } from "../Icon";
import styles from "./Menu.module.css";

export interface MenuItem {
  id: string;
  label: string;
  icon?: LucideIcon;
  /** Short helper line under the label. */
  description?: string;
  disabled?: boolean;
  /** Destructive items read as danger (icon and label), never by color alone: they say what they remove. */
  tone?: "danger";
  onSelect: () => void;
}

export interface MenuProps {
  /** Trigger label; also the accessible name of an icon-only trigger. */
  label: string;
  items: readonly MenuItem[];
  /** `button`: labelled button with a chevron. `icon`: a ⋯ button (row and lesson actions). */
  trigger?: "button" | "icon";
  variant?: Extract<ButtonVariant, "ghost" | "soft" | "outline">;
  size?: ButtonSize;
  icon?: LucideIcon;
  /** Edge the menu lines up with; icon triggers sit at the end of a row, so they default to `end`. */
  align?: "start" | "end";
  /** Start open (gallery). */
  defaultOpen?: boolean;
}

/**
 * Menu button (WAI-ARIA APG): opening moves focus to the first item (ArrowUp: the last), arrows
 * move, Home/End jump, Esc and Tab close, focus returns to the trigger. One component serves the
 * labelled "Add" menus and the ⋯ action menus.
 */
export function Menu({ label, items, trigger = "button", variant = "outline", size = "md", icon, align, defaultOpen = false }: MenuProps) {
  const edge = align ?? (trigger === "icon" ? "end" : "start");
  const [open, setOpen] = useState(defaultOpen);
  const anchor = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const focusOnOpen = useRef<"first" | "last" | null>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const target = focusOnOpen.current;
    focusOnOpen.current = null;
    if (target) {
      const enabled = itemRefs.current.filter((node): node is HTMLButtonElement => node !== null && !node.disabled);
      (target === "first" ? enabled[0] : enabled[enabled.length - 1])?.focus();
    }
    const onPointer = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  const close = (refocus = true) => {
    setOpen(false);
    if (refocus) triggerRef.current?.focus();
  };

  const openWith = (target: "first" | "last") => {
    focusOnOpen.current = target;
    setOpen(true);
  };

  const onTriggerKey = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openWith("first");
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openWith("last");
    }
  };

  const onMenuKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const enabled = itemRefs.current.filter((node): node is HTMLButtonElement => node !== null && !node.disabled);
    const index = enabled.findIndex((node) => node === document.activeElement);
    const go = (next: number) => {
      event.preventDefault();
      enabled[(next + enabled.length) % enabled.length]?.focus();
    };
    if (event.key === "ArrowDown") go(index + 1);
    else if (event.key === "ArrowUp") go(index - 1);
    else if (event.key === "Home") go(0);
    else if (event.key === "End") go(enabled.length - 1);
    else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") close(false);
  };

  const triggerShared = {
    ref: triggerRef,
    "aria-haspopup": "menu" as const,
    "aria-expanded": open,
    "aria-controls": open ? menuId : undefined,
    onClick: () => {
      if (open) setOpen(false);
      else openWith("first");
    },
    onKeyDown: onTriggerKey,
  };

  return (
    <div className={styles.anchor} ref={anchor}>
      {trigger === "icon" ? (
        <IconButton label={label} icon={icon ?? MoreHorizontal} size={size} {...triggerShared} />
      ) : (
        <Button variant={variant} size={size} icon={icon} trailingIcon={ChevronDown} {...triggerShared}>
          {label}
        </Button>
      )}
      {open && (
        <div id={menuId} role="menu" aria-label={label} className={cx(styles.menu, edge === "end" && styles.end)} onKeyDown={onMenuKey}>
          {items.map((item, index) => (
            <button
              key={item.id}
              ref={(node) => {
                itemRefs.current[index] = node;
              }}
              type="button"
              role="menuitem"
              tabIndex={-1}
              disabled={item.disabled}
              className={cx(styles.item, item.tone === "danger" && styles.danger)}
              onClick={() => {
                close();
                item.onSelect();
              }}
            >
              {item.icon && <Icon icon={item.icon} size={16} className={styles.itemIcon} />}
              <span className={styles.itemText}>
                <span className={styles.itemLabel}>{item.label}</span>
                {item.description && <span className={styles.itemDescription}>{item.description}</span>}
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
