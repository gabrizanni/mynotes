import { ArrowUp, X } from "lucide-react";
import { useId, type KeyboardEvent } from "react";

import { useT } from "../../i18n";
import { IconButton } from "../Button";
import styles from "./AssistantDock.module.css";

export interface AssistantDockProps {
  /** Current page, shown as the context chip ("Lesson 3 · Statistics"). */
  context: string;
  assistantName: string;
  company: string;
  draft: string;
  onDraftChange: (draft: string) => void;
  onClose: () => void;
  /** False until the backend streams chat (UX-SPEC §7 gap 1): the composer stays read-only. */
  available: boolean;
  onSend?: (text: string) => void;
}

/** Shell of the right-hand assistant panel (⌘J). Messages and tool rows arrive with the backend. */
export function AssistantDock({ context, assistantName, company, draft, onDraftChange, onClose, available, onSend }: AssistantDockProps) {
  const t = useT();
  const titleId = useId();
  const inputId = useId();
  const send = () => {
    if (!available || !draft.trim()) return;
    onSend?.(draft.trim());
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  };
  return (
    <aside className={styles.dock} aria-labelledby={titleId}>
      <div className={styles.header}>
        <div className={styles.heading}>
          <h2 id={titleId} className={styles.title}>
            {t.assistant.title}
          </h2>
          <span className={styles.context}>{t.assistant.context(context)}</span>
        </div>
        <IconButton label={t.assistant.close} icon={X} onClick={onClose} />
      </div>
      <div className={styles.body}>
        <p className={styles.suggestionsTitle}>{t.assistant.suggestions}</p>
        <ul className={styles.suggestions} role="list">
          {t.assistant.prompts.map((prompt) => (
            <li key={prompt}>
              <button type="button" className={styles.suggestion} onClick={() => onDraftChange(prompt)}>
                {prompt}
              </button>
            </li>
          ))}
        </ul>
        {!available && <p className={styles.note}>{t.assistant.unavailable}</p>}
      </div>
      <div className={styles.composer}>
        <label htmlFor={inputId} className="visually-hidden">
          {t.assistant.composerLabel}
        </label>
        <div className={styles.inputRow}>
          <textarea
            id={inputId}
            className={styles.input}
            rows={1}
            value={draft}
            placeholder={t.assistant.composerPlaceholder}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={onKeyDown}
          />
          <IconButton label={t.assistant.send} icon={ArrowUp} variant="soft" size="sm" disabled={!available || !draft.trim()} onClick={send} />
        </div>
        <p className={styles.footer}>{t.assistant.footer(assistantName, company)}</p>
      </div>
    </aside>
  );
}
