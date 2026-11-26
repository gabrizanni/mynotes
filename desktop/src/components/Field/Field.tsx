import { CircleAlert } from "lucide-react";
import { useId, type ReactNode } from "react";

import { useT } from "../../i18n";
import { Icon } from "../Icon";
import styles from "./Field.module.css";

export { styles as fieldStyles };

export interface FieldA11y {
  id: string;
  "aria-describedby": string | undefined;
  "aria-invalid": true | undefined;
}

export interface FieldProps {
  label: ReactNode;
  hint?: ReactNode;
  /** Shown below the control and announced; also sets aria-invalid. */
  error?: ReactNode;
  optional?: boolean;
  id?: string;
  /** Render the control with the ids it must carry. */
  children: (a11y: FieldA11y) => ReactNode;
  /** Use a <span> label for controls that are labelled by aria-labelledby (radiogroups). */
  labelAs?: "label" | "span";
  /** Keep the label for screen readers only (search fields with a placeholder and an icon). */
  hideLabel?: boolean;
}

/** Label, hint and error wiring shared by every input (one place for the a11y rules). */
export function Field({ label, hint, error, optional = false, id, children, labelAs = "label", hideLabel = false }: FieldProps) {
  const t = useT();
  const generated = useId();
  const controlId = id ?? `field-${generated}`;
  const hintId = hint ? `${controlId}-hint` : undefined;
  const errorId = error ? `${controlId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  const labelContent = (
    <>
      {label}
      {optional && <span className={styles.optional}>{t.common.optional}</span>}
    </>
  );
  return (
    <div className={styles.field}>
      {labelAs === "label" ? (
        <label className={hideLabel ? "visually-hidden" : styles.label} htmlFor={controlId}>
          {labelContent}
        </label>
      ) : (
        <span className={hideLabel ? "visually-hidden" : styles.label} id={`${controlId}-label`}>
          {labelContent}
        </span>
      )}
      {children({ id: controlId, "aria-describedby": describedBy, "aria-invalid": error ? true : undefined })}
      {hint && (
        <p className={styles.hint} id={hintId}>
          {hint}
        </p>
      )}
      {error && (
        <p className={styles.error} id={errorId} role="alert">
          <Icon icon={CircleAlert} size={14} />
          <span>{error}</span>
        </p>
      )}
    </div>
  );
}
