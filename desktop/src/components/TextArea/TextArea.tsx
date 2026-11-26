import type { ReactNode, TextareaHTMLAttributes } from "react";

import { cx } from "../../design/cx";
import { Field, fieldStyles } from "../Field";
import styles from "./TextArea.module.css";

export interface TextAreaProps extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id"> {
  label: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  optional?: boolean;
  id?: string;
}

export function TextArea({ label, hint, error, optional, id, className, rows = 4, ...rest }: TextAreaProps) {
  return (
    <Field label={label} hint={hint} error={error} optional={optional} id={id}>
      {(a11y) => <textarea rows={rows} className={cx(fieldStyles.control, styles.area, className)} {...a11y} {...rest} />}
    </Field>
  );
}
