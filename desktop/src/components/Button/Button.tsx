import { LoaderCircle, type LucideIcon } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

import { cx } from "../../design/cx";
import { Icon } from "../Icon";
import styles from "./Button.module.css";

export type ButtonVariant = "primary" | "soft" | "ghost" | "outline" | "destructive" | "onInverse" | "ghostOnInverse";
export type ButtonSize = "md" | "sm";

export interface ButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** One `primary` per screen (UX-SPEC). `onInverse` renders the same lavender primary on the feature surface. */
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  trailingIcon?: LucideIcon;
  /** Shows a spinner, keeps the label for screen readers and blocks repeat clicks. */
  loading?: boolean;
  fullWidth?: boolean;
  ref?: Ref<HTMLButtonElement>;
  children: ReactNode;
}

export function Button({
  variant = "soft",
  size = "md",
  icon,
  trailingIcon,
  loading = false,
  fullWidth = false,
  className,
  type = "button",
  disabled,
  children,
  ref,
  ...rest
}: ButtonProps) {
  const iconSize = size === "sm" ? 16 : 20;
  return (
    <button
      ref={ref}
      type={type}
      className={cx(styles.button, styles[variant], size === "sm" && styles.sm, fullWidth && styles.full, className)}
      disabled={disabled === true || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Icon icon={LoaderCircle} size={iconSize} className={styles.spinner} /> : icon && <Icon icon={icon} size={iconSize} />}
      <span>{children}</span>
      {trailingIcon && <Icon icon={trailingIcon} size={iconSize} />}
    </button>
  );
}

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children" | "aria-label"> {
  /** Required: icon-only buttons need an accessible name. Also shown as a tooltip. */
  label: string;
  icon: LucideIcon;
  variant?: Extract<ButtonVariant, "ghost" | "soft" | "outline" | "ghostOnInverse">;
  size?: ButtonSize;
  /** Lets menus and popovers return focus to the trigger. */
  ref?: Ref<HTMLButtonElement>;
}

export function IconButton({ label, icon, variant = "ghost", size = "md", className, type = "button", ref, ...rest }: IconButtonProps) {
  return (
    <button
      ref={ref}
      type={type}
      aria-label={label}
      title={label}
      className={cx(styles.button, styles.icon, styles[variant], size === "sm" && styles.sm, className)}
      {...rest}
    >
      <Icon icon={icon} size={size === "sm" ? 16 : 20} />
    </button>
  );
}
