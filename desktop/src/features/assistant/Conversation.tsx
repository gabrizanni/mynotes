import { Ban, ChevronDown, ChevronRight, CircleAlert, CircleCheck, LoaderCircle, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { Banner, Icon } from "../../components";
import { cx } from "../../design/cx";
import { useT, type Messages } from "../../i18n";
import {
  durationParts,
  errorView,
  splitBlocks,
  splitInline,
  toolLabel,
  type Block,
  type ErrorFix,
  type ProviderInfo,
  type ToolRow,
  type ToolStatus,
  type Turn,
  type TurnError,
} from "./model";
import styles from "./Conversation.module.css";

const STATUS_ICONS: Record<ToolStatus, LucideIcon> = { running: LoaderCircle, completed: CircleCheck, failed: CircleAlert, denied: Ban };

/** Inline spans as text nodes: no HTML is ever parsed from a reply. */
function InlineText({ text }: { text: string }) {
  return (
    <>
      {splitInline(text).map((part, index) => {
        if (part.kind === "code") return <code key={index} className={styles.inlineCode}>{part.text}</code>;
        if (part.kind === "strong") return <strong key={index}>{part.text}</strong>;
        if (part.kind === "em") return <em key={index}>{part.text}</em>;
        return <span key={index}>{part.text}</span>;
      })}
    </>
  );
}

function MessageBlock({ block, codeLabel }: { block: Block; codeLabel: string }) {
  switch (block.kind) {
    case "heading":
      return <p className={styles.heading}><InlineText text={block.text} /></p>;
    case "list": {
      const items = block.items.map((item, index) => <li key={index}><InlineText text={item} /></li>);
      return block.ordered ? <ol className={styles.list}>{items}</ol> : <ul className={styles.list}>{items}</ul>;
    }
    case "code":
      return (
        <pre className={styles.code} aria-label={block.lang ? `${codeLabel} · ${block.lang}` : codeLabel} tabIndex={0}>
          <code>{block.code}</code>
        </pre>
      );
    case "paragraph":
      return <p className={styles.paragraph}><InlineText text={block.text} /></p>;
  }
}

export function MessageText({ text, codeLabel }: { text: string; codeLabel: string }) {
  return (
    <div className={styles.text}>
      {splitBlocks(text).map((block, index) => <MessageBlock key={index} block={block} codeLabel={codeLabel} />)}
    </div>
  );
}

export function toolTitle(tool: ToolRow, t: Messages): string {
  const running = tool.status === "running";
  const label = toolLabel(tool);
  switch (label.kind) {
    case "notes":
      return t.assistant.tool.notes(running, label.course, label.lessons);
    case "transcribe":
      return t.assistant.tool.transcribe(running, label.course, label.lesson);
    case "instructions":
      return t.assistant.tool.instructions(running);
    case "check":
      return t.assistant.tool.check(running);
    case "undo":
      return t.assistant.tool.undo(running, label.course);
    case "calendar":
      return t.assistant.tool.calendar(running);
    case "read":
      return t.assistant.tool.read(running, label.file);
    case "command":
      return label.text;
  }
}

function duration(ms: number | null, t: Messages): string | null {
  const parts = durationParts(ms);
  if (!parts) return null;
  return "minutes" in parts ? t.assistant.duration.minutes(parts.minutes) : t.assistant.duration.seconds(parts.seconds);
}

/** Collapsed: "Prepared notes for STAT lesson 3 · 2 min"; expanded: the raw command. */
export function ToolRowView({ tool, defaultOpen = false }: { tool: ToolRow; defaultOpen?: boolean }) {
  const t = useT();
  const [open, setOpen] = useState(defaultOpen);
  const panelId = useId();
  const time = tool.status === "running" ? null : duration(tool.durationMs, t);
  const status = tool.status === "completed" ? null : t.assistant.tool.status[tool.status];
  const title = [toolTitle(tool, t), time].filter(Boolean).join(" · ");
  const expandable = Boolean(tool.command);
  return (
    <li className={cx(styles.tool, styles[tool.status])}>
      <button
        type="button"
        className={styles.toolButton}
        aria-expanded={expandable ? open : undefined}
        aria-controls={expandable ? panelId : undefined}
        disabled={!expandable}
        onClick={() => setOpen((value) => !value)}
        title={expandable ? (open ? t.assistant.tool.hide : t.assistant.tool.show) : undefined}
      >
        <Icon icon={STATUS_ICONS[tool.status]} size={16} className={cx(styles.toolIcon, tool.status === "running" && styles.spin)} />
        <span className={styles.toolTitle}>{title}</span>
        {status && <span className={styles.toolStatus}>{status}</span>}
        {expandable && <Icon icon={open ? ChevronDown : ChevronRight} size={16} className={styles.chevron} />}
      </button>
      {expandable && open && (
        <pre id={panelId} className={styles.command}>
          <code>{tool.command}</code>
        </pre>
      )}
    </li>
  );
}

interface ErrorProps {
  error: TurnError;
  provider: ProviderInfo;
  onFix: (fix: ErrorFix) => void;
}

function TurnErrorBanner({ error, provider, onFix }: ErrorProps) {
  const t = useT();
  const { kind, fix } = errorView(error);
  const problems: Record<typeof kind, string> = {
    quota: t.assistant.errors.quota(provider.name),
    auth: t.assistant.errors.auth(provider.name),
    network: t.assistant.errors.network,
    timeout: t.assistant.errors.timeout,
    unavailable: t.assistant.errors.unavailable(provider.name),
    interrupted: t.assistant.errors.interrupted,
    busy: t.assistant.errors.busy,
    other: t.assistant.errors.other,
  };
  const action = fix === "none" ? undefined : { label: t.assistant.fixes[fix], onSelect: () => onFix(fix) };
  return (
    <Banner tone={kind === "quota" || kind === "busy" ? "warning" : "danger"} action={action}>
      {problems[kind]}
    </Banner>
  );
}

export interface ConversationProps {
  turns: readonly Turn[];
  provider: ProviderInfo;
  onFix: (fix: ErrorFix, turn: Turn) => void;
  /** Screenshots and tests: open these tool rows. */
  openTools?: readonly string[];
}

export function Conversation({ turns, provider, onFix, openTools = [] }: ConversationProps) {
  const t = useT();
  const end = useRef<HTMLDivElement>(null);
  const last = turns.at(-1);
  const busy = last?.state === "sending" || last?.state === "running";
  const size = turns.reduce((total, turn) => total + turn.text.length + turn.tools.length, 0);
  useEffect(() => {
    end.current?.scrollIntoView?.({ block: "end" });
  }, [size, turns.length]);
  return (
    <div className={styles.conversation} role="log" aria-label={t.assistant.conversationLabel} aria-busy={busy}>
      {turns.map((turn) => (
        <article key={turn.id} className={styles.turn} aria-label={turn.message.slice(0, 60)}>
          <div className={styles.user}>
            <span className="visually-hidden">{t.assistant.you}: </span>
            <MessageText text={turn.message} codeLabel={t.assistant.codeLabel} />
          </div>
          <div className={styles.reply}>
            <span className="visually-hidden">{provider.name}: </span>
            {turn.tools.length > 0 && (
              <ul className={styles.tools} role="list">
                {turn.tools.map((tool) => (
                  <ToolRowView key={tool.toolId} tool={tool} defaultOpen={openTools.includes(tool.toolId)} />
                ))}
              </ul>
            )}
            {turn.text && <MessageText text={turn.text} codeLabel={t.assistant.codeLabel} />}
            {(turn.state === "sending" || turn.state === "running") && (
              <p className={styles.working} aria-live="polite">
                <Icon icon={LoaderCircle} size={16} className={styles.spin} />
                {t.assistant.working}
              </p>
            )}
            {turn.state === "cancelled" && <p className={styles.note}>{t.assistant.stopped}</p>}
            {turn.error && <TurnErrorBanner error={turn.error} provider={provider} onFix={(fix) => onFix(fix, turn)} />}
          </div>
        </article>
      ))}
      <div ref={end} />
    </div>
  );
}
