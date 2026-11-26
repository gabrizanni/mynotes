import { ArrowUp, SquarePen, Square, X } from "lucide-react";
import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from "react";

import { useT } from "../../i18n";
import { Button, IconButton } from "../Button";
import styles from "./AssistantDock.module.css";

export interface AssistantDockProps {
  /** Current page, shown as the context chip ("Lesson 3 · Statistics"). */
  context: string;
  assistantName: string;
  company: string;
  draft: string;
  onDraftChange: (draft: string) => void;
  onClose: () => void;
  onSend?: (text: string) => void;
  /** Stop the running reply. */
  onStop?: () => void;
  onNewConversation?: () => void;
  /** A reply is being sent or streams: Send becomes Stop. */
  busy?: boolean;
  /** The conversation; null shows the suggested prompts. */
  conversation?: ReactNode | null;
  /** False: the composer is read-only (component gallery, no backend). */
  available?: boolean;
  /** Shown instead of the suggestions while the last conversation loads. */
  status?: string;
}

/** The right-hand assistant panel (⌘J): context chip, conversation or suggestions, composer, footer. */
export function AssistantDock({
  context,
  assistantName,
  company,
  draft,
  onDraftChange,
  onClose,
  onSend,
  onStop,
  onNewConversation,
  busy = false,
  conversation = null,
  status,
  available = true,
}: AssistantDockProps) {
  const t = useT();
  const titleId = useId();
  const inputId = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const canSend = available && !busy && draft.trim().length > 0;

  useEffect(() => {
    if (available) input.current?.focus(); // Opening the panel (⌘J or a handoff) puts the caret in the composer.
  }, [available]);

  const send = () => {
    if (!canSend) return;
    onSend?.(draft.trim());
  };
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      send();
    }
  };
  const pick = (prompt: string) => {
    onDraftChange(prompt);
    input.current?.focus();
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
        <div className={styles.headerActions}>
          {conversation !== null && (
            <IconButton label={t.assistant.newConversation} icon={SquarePen} disabled={busy} onClick={() => onNewConversation?.()} />
          )}
          <IconButton label={t.assistant.close} icon={X} onClick={onClose} />
        </div>
      </div>
      <div className={styles.body}>
        {conversation ?? (
          <>
            {status ? (
              <p className={styles.note} role="status">
                {status}
              </p>
            ) : (
              <>
                <p className={styles.suggestionsTitle}>{t.assistant.suggestions}</p>
                <ul className={styles.suggestions} role="list">
                  {t.assistant.prompts.map((prompt) => (
                    <li key={prompt}>
                      <button type="button" className={styles.suggestion} onClick={() => pick(prompt)}>
                        {prompt}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
      <div className={styles.composer}>
        <label htmlFor={inputId} className="visually-hidden">
          {t.assistant.composerLabel}
        </label>
        <div className={styles.inputRow}>
          <textarea
            ref={input}
            id={inputId}
            className={styles.input}
            rows={2}
            value={draft}
            maxLength={20_000}
            readOnly={!available}
            placeholder={t.assistant.composerPlaceholder}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={onKeyDown}
          />
          {busy ? (
            <Button variant="outline" size="sm" icon={Square} onClick={() => onStop?.()}>
              {t.assistant.stop}
            </Button>
          ) : (
            <Button variant="primary" size="sm" icon={ArrowUp} disabled={!canSend} onClick={send}>
              {t.assistant.send}
            </Button>
          )}
        </div>
        <p className={styles.footer}>{t.assistant.footer(assistantName, company)}</p>
      </div>
    </aside>
  );
}
