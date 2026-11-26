import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { cx } from "../../design/cx";
import styles from "./AnchoredPopover.module.css";

export interface AnchoredPopoverProps {
  open: boolean;
  /** The element the panel points at (a calendar block); focus returns to it on Esc. */
  anchor: HTMLElement | null;
  onClose: () => void;
  /** Accessible name of the panel. */
  title: string;
  children: ReactNode;
  /** Render in place without positioning (component gallery). */
  inline?: boolean;
}

const GAP = 8;
const MARGIN = 12;

/**
 * A non-modal panel next to an element inside a scrolling surface (calendar grid). It lives
 * in a portal with fixed coordinates, so the grid's overflow never clips it. Esc, a click
 * outside, scrolling the page or resizing the window close it.
 */
export function AnchoredPopover({ open, anchor, onClose, title, children, inline = false }: AnchoredPopoverProps) {
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || inline || !anchor || !panel.current) return;
    const rect = anchor.getBoundingClientRect();
    const { offsetWidth: width, offsetHeight: height } = panel.current;
    const roomRight = window.innerWidth - rect.right - MARGIN;
    const left = roomRight >= width + GAP ? rect.right + GAP : Math.max(MARGIN, rect.left - width - GAP);
    const top = Math.min(Math.max(MARGIN, rect.top), Math.max(MARGIN, window.innerHeight - height - MARGIN));
    setPosition({ top, left });
  }, [open, inline, anchor]);

  useEffect(() => {
    if (!open || inline) return undefined;
    panel.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
      anchor?.focus();
    };
    const onPointer = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!panel.current?.contains(target) && !anchor?.contains(target)) onClose();
    };
    const onScroll = (event: Event) => {
      if (!panel.current?.contains(event.target as Node)) onClose();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onClose);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onClose);
    };
  }, [open, inline, anchor, onClose]);

  if (!open) return null;
  const body = (
    <div
      ref={panel}
      role="dialog"
      aria-label={title}
      tabIndex={-1}
      className={cx(styles.panel, inline && styles.inline, !inline && !position && styles.measuring)}
      style={inline || !position ? undefined : { top: position.top, left: position.left }}
    >
      {children}
    </div>
  );
  return inline ? body : createPortal(body, document.body);
}
