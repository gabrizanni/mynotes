import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { cx } from "../../design/cx";
import { Button, type ButtonProps } from "../Button";
import styles from "./Popover.module.css";

export interface PopoverProps {
  /** Label of the trigger button. */
  label: ReactNode;
  triggerProps?: Omit<ButtonProps, "children" | "onClick">;
  /** Accessible name of the panel. */
  title: string;
  align?: "start" | "end";
  /** Start open (gallery). */
  defaultOpen?: boolean;
  children: ReactNode | ((close: () => void) => ReactNode);
}

/** Non-modal panel: Esc or a click outside closes it and focus returns to the trigger. */
export function Popover({ label, triggerProps, title, align = "start", defaultOpen = false, children }: PopoverProps) {
  const [open, setOpen] = useState(defaultOpen);
  const anchor = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panelId = useId();

  useEffect(() => {
    if (!open) return undefined;
    if (!defaultOpen) panel.current?.focus();
    const onPointer = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        // Handled here: page-level Esc shortcuts (onboarding Back) must not also fire.
        event.preventDefault();
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, defaultOpen]);

  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };

  return (
    <div className={styles.anchor} ref={anchor}>
      <Button
        variant="ghost"
        {...triggerProps}
        ref={trigger}
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-haspopup="dialog"
        onClick={() => setOpen((value) => !value)}
      >
        {label}
      </Button>
      {open && (
        <div ref={panel} id={panelId} role="dialog" aria-label={title} tabIndex={-1} className={cx(styles.panel, align === "end" && styles.end)}>
          {typeof children === "function" ? children(close) : children}
        </div>
      )}
    </div>
  );
}
