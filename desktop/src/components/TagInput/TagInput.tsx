import { X } from "lucide-react";
import { useRef, useState, type KeyboardEvent, type ReactNode } from "react";

import { useT } from "../../i18n";
import { Field } from "../Field";
import { Icon } from "../Icon";
import styles from "./TagInput.module.css";

export interface TagInputProps {
  label: ReactNode;
  value: readonly string[];
  onChange: (tags: string[]) => void;
  hint?: ReactNode;
  placeholder?: string;
  optional?: boolean;
}

/** Free tags: Enter or comma adds, Backspace on an empty field removes the last one. */
export function TagInput({ label, value, onChange, hint, placeholder, optional }: TagInputProps) {
  const t = useT();
  const [draft, setDraft] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const commit = () => {
    const tag = draft.trim().replace(/,$/, "").trim();
    if (tag && !value.some((existing) => existing.toLocaleLowerCase() === tag.toLocaleLowerCase())) {
      onChange([...value, tag]);
    }
    setDraft("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && draft === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };

  return (
    <Field label={label} hint={hint} optional={optional}>
      {(a11y) => (
        <div className={styles.box} onClick={() => input.current?.focus()}>
          {value.length > 0 && (
            <ul className={styles.list} role="list">
              {value.map((tag) => (
                <li key={tag} className={styles.tag}>
                  <span>{tag}</span>
                  <button
                    type="button"
                    className={styles.remove}
                    aria-label={t.common.removeTag(tag)}
                    onClick={(event) => {
                      event.stopPropagation();
                      onChange(value.filter((existing) => existing !== tag));
                      input.current?.focus();
                    }}
                  >
                    <Icon icon={X} size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
          <input
            ref={input}
            className={styles.input}
            value={draft}
            placeholder={value.length === 0 ? placeholder : undefined}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={onKeyDown}
            onBlur={commit}
            {...a11y}
          />
        </div>
      )}
    </Field>
  );
}
