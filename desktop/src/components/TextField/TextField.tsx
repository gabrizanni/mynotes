import type { LucideIcon } from "lucide-react";
import type { InputHTMLAttributes, ReactNode } from "react";

import { cx } from "../../design/cx";
import { Field, fieldStyles } from "../Field";
import { Icon } from "../Icon";

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  optional?: boolean;
  id?: string;
  /** Screen-reader-only label, for search fields that carry an icon and a placeholder. */
  hideLabel?: boolean;
  /** Decorative glyph inside the field, before the text (e.g. Search). */
  leadingIcon?: LucideIcon;
}

export function TextField({ label, hint, error, optional, id, className, type = "text", hideLabel, leadingIcon, ...rest }: TextFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} optional={optional} id={id} hideLabel={hideLabel}>
      {(a11y) =>
        leadingIcon ? (
          <div className={fieldStyles.withIcon}>
            <Icon icon={leadingIcon} size={16} className={fieldStyles.leadingIcon} />
            <input type={type} className={cx(fieldStyles.control, fieldStyles.controlWithIcon, className)} {...a11y} {...rest} />
          </div>
        ) : (
          <input type={type} className={cx(fieldStyles.control, className)} {...a11y} {...rest} />
        )
      }
    </Field>
  );
}
