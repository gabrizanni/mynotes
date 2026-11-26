import { ArrowLeft, type LucideIcon } from "lucide-react";
import { useEffect, useLayoutEffect, useRef, type FormEvent, type ReactNode } from "react";

import { Button } from "../../components";
import { cx } from "../../design/cx";
import { useT } from "../../i18n";
import { useOnboarding } from "./controller";
import { keyIntent, prevStepId } from "./model";
import styles from "./Onboarding.module.css";

export interface StepAction {
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  loading?: boolean;
  icon?: LucideIcon;
}

export interface StepLayoutProps {
  title: string;
  lede?: ReactNode;
  /** Display size (40 px) for the first and last steps; H1 (32 px) otherwise. */
  display?: boolean;
  width?: "narrow" | "wide";
  /** The screen's one primary button. `null` while nothing can be confirmed (a test running). */
  primary: StepAction | null;
  /** Quiet actions next to the primary: Skip, Do this later, Remove calendar… */
  secondary?: readonly StepAction[];
  /** Esc goes back unless a test or a save is running. */
  escapeBack?: boolean;
  children?: ReactNode;
}

function describe(target: EventTarget | null): Parameters<typeof keyIntent>[1] {
  const element = target instanceof HTMLElement ? target : document.body;
  const value = element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement ? element.value : undefined;
  return {
    tagName: element.tagName,
    type: element instanceof HTMLInputElement ? element.type : undefined,
    isContentEditable: element.isContentEditable,
    value,
    insideDialog: element.closest("[role='dialog'], dialog") !== null,
  };
}

/**
 * One onboarding step: serif title, short lede, content in a reading column, then a footer
 * with Back on the left and the primary action on the right. The step is a form, so Enter in a
 * text field submits it natively; a window listener covers Enter outside controls and Esc.
 */
export function StepLayout({ title, lede, display = false, width = "narrow", primary, secondary = [], escapeBack = true, children }: StepLayoutProps) {
  const t = useT();
  const { step, back } = useOnboarding();
  const heading = useRef<HTMLHeadingElement>(null);
  const primaryRef = useRef(primary);
  primaryRef.current = primary;
  const canGoBack = prevStepId(step) !== null;

  // New step: announce it by moving focus to its heading.
  useEffect(() => {
    heading.current?.focus({ preventScroll: true });
  }, [step]);

  // Listening before paint: Enter or Esc pressed as the step appears is never lost.
  useLayoutEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const intent = keyIntent(event.key, describe(event.target), {
        defaultPrevented: event.defaultPrevented,
        escapeBack: escapeBack && canGoBack,
        composing: event.isComposing,
      });
      if (intent === "back") {
        event.preventDefault();
        back();
      } else if (intent === "primary") {
        const action = primaryRef.current;
        if (action && !action.disabled && !action.loading) {
          event.preventDefault();
          action.onSelect();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [back, escapeBack, canGoBack]);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (primary && !primary.disabled && !primary.loading) primary.onSelect();
  };

  return (
    <form className={styles.form} onSubmit={onSubmit} noValidate aria-labelledby={`${step}-title`}>
      <div className={styles.scroll}>
        <div key={step} className={cx(styles.column, width === "wide" && styles.wide, styles.enter)}>
          <div className={styles.head}>
            <h1 id={`${step}-title`} ref={heading} tabIndex={-1} className={cx(styles.title, display && styles.display)}>
              {title}
            </h1>
            {lede && <p className={styles.lede}>{lede}</p>}
          </div>
          <div className={styles.body}>{children}</div>
        </div>
      </div>
      <footer className={styles.footer}>
        <div className={cx(styles.column, width === "wide" && styles.wide, styles.footerInner)}>
          {canGoBack && (
            <Button variant="ghost" icon={ArrowLeft} onClick={back}>
              {t.onboarding.window.back}
            </Button>
          )}
          <span className={styles.footerSpacer} />
          {secondary.map((action) => (
            <Button key={action.label} variant="ghost" icon={action.icon} disabled={action.disabled} loading={action.loading} onClick={action.onSelect}>
              {action.label}
            </Button>
          ))}
          {primary && (
            <Button type="submit" variant="primary" icon={primary.icon} disabled={primary.disabled} loading={primary.loading}>
              {primary.label}
            </Button>
          )}
        </div>
      </footer>
    </form>
  );
}
