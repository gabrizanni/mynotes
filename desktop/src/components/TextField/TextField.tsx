import type { InputHTMLAttributes, ReactNode } from "react";

import { cx } from "../../design/cx";
import { Field, fieldStyles } from "../Field";

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  optional?: boolean;
  id?: string;
}

export function TextField({ label, hint, error, optional, id, className, type = "text", ...rest }: TextFieldProps) {
  return (
    <Field label={label} hint={hint} error={error} optional={optional} id={id}>
      {(a11y) => <input type={type} className={cx(fieldStyles.control, className)} {...a11y} {...rest} />}
    </Field>
  );
}
