import { ChevronDown, ChevronUp, FileText, Search, X } from "lucide-react";
import { forwardRef, useEffect, useId, useImperativeHandle, useMemo, useRef, useState, type KeyboardEvent } from "react";

import { Banner, Button, Card, fieldStyles, Icon, IconButton } from "../../components";
import { cx } from "../../design/cx";
import { useI18n } from "../../i18n";
import type { TranscriptSegment } from "../../rpc";
import { shortcutLabel } from "../../app/platform";
import { activeSegment, findMatches, highlightParts, stepMatch } from "./model";
import styles from "./Lesson.module.css";

export interface TranscriptHandle {
  /** ⌘F / Ctrl+F: focus the search field and select its text. */
  focusSearch: () => void;
}

export interface TranscriptPanelProps {
  status: "loading" | "ready" | "error";
  segments: readonly TranscriptSegment[];
  position: number;
  onSeek: (seconds: number) => void;
  onRetry: () => void;
  onOpenFull: (() => void) | null;
  /** Start with this query (screenshots, deep links). */
  initialQuery?: string;
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

export const TranscriptPanel = forwardRef<TranscriptHandle, TranscriptPanelProps>(function TranscriptPanel(
  { status, segments, position, onSeek, onRetry, onOpenFull, initialQuery = "" },
  ref,
) {
  const { t, format } = useI18n();
  const s = t.lesson.search;
  const [query, setQuery] = useState(initialQuery);
  const [current, setCurrent] = useState(-1);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLOListElement>(null);
  const titleId = useId();
  const countId = useId();

  useImperativeHandle(ref, () => ({
    focusSearch: () => {
      input.current?.focus();
      input.current?.select();
    },
  }));

  const matches = useMemo(() => findMatches(segments, query), [segments, query]);
  useEffect(() => setCurrent(matches.length > 0 ? 0 : -1), [matches]);
  const playing = position > 0 ? activeSegment(segments, position) : -1;

  useEffect(() => {
    if (current < 0) return;
    const container = list.current;
    const mark = container?.querySelector<HTMLElement>(`[data-match="${current}"]`);
    if (!container || !mark) return;
    // Scroll the transcript only, never the page around it.
    const top = mark.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - container.clientHeight / 2;
    container.scrollTo({ top: Math.max(0, top), behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [current]);

  const step = (direction: 1 | -1) => setCurrent((value) => stepMatch(value, matches.length, direction));

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      step(event.key === "ArrowUp" || (event.key === "Enter" && event.shiftKey) ? -1 : 1);
    } else if (event.key === "Escape" && query) {
      event.preventDefault();
      setQuery("");
    }
  };

  const searching = query.trim().length >= 2;
  const countText = searching ? (matches.length > 0 ? s.count(current + 1, matches.length) : s.none) : "";

  return (
    <Card padding="none" as="section" aria-labelledby={titleId} className={styles.transcript}>
      <div className={styles.transcriptHead}>
        <h2 id={titleId} className={styles.cardTitle}>
          {t.lesson.transcript}
        </h2>
        {onOpenFull && (
          <Button variant="ghost" size="sm" icon={FileText} onClick={onOpenFull}>
            {t.lesson.openFullText}
          </Button>
        )}
      </div>
      <div className={styles.searchBar} role="search">
        <div className={styles.searchField}>
          <Icon icon={Search} size={16} className={styles.searchIcon} />
          <input
            ref={input}
            type="search"
            className={cx(fieldStyles.control, styles.searchInput)}
            aria-label={s.label}
            aria-describedby={countId}
            aria-keyshortcuts="Meta+F Control+F"
            placeholder={s.shortcut(shortcutLabel("F"))}
            value={query}
            disabled={status !== "ready"}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onKeyDown}
          />
          {query && <IconButton label={s.clear} icon={X} size="sm" onClick={() => setQuery("")} />}
        </div>
        <span id={countId} className={styles.matchCount} aria-live="polite">
          {countText}
        </span>
        <IconButton label={s.previous} icon={ChevronUp} size="sm" disabled={matches.length === 0} onClick={() => step(-1)} />
        <IconButton label={s.next} icon={ChevronDown} size="sm" disabled={matches.length === 0} onClick={() => step(1)} />
      </div>
      {status === "error" ? (
        <div className={styles.transcriptState}>
          <Banner tone="danger" action={{ label: t.common.retry, onSelect: onRetry }}>
            {t.lesson.transcriptPanel.error}
          </Banner>
        </div>
      ) : status === "loading" ? (
        <div className={styles.transcriptState} aria-busy="true">
          <p className={styles.muted}>{t.lesson.transcriptPanel.loading}</p>
          <div className={styles.skeletonLine} />
          <div className={styles.skeletonLine} />
          <div className={cx(styles.skeletonLine, styles.short)} />
        </div>
      ) : segments.length === 0 ? (
        <div className={styles.transcriptState}>
          <p className={styles.muted}>{t.lesson.transcriptPanel.empty}</p>
        </div>
      ) : (
        <ol ref={list} className={styles.segments} aria-label={t.lesson.transcript}>
          {segments.map((segment, index) => {
            const time = format.clock(segment.start_seconds);
            return (
              <li key={`${segment.start_seconds}-${index}`} className={cx(styles.segment, index === playing && styles.segmentPlaying)} aria-current={index === playing ? "true" : undefined}>
                <button type="button" className={styles.stamp} aria-label={t.lesson.transcriptPanel.playFrom(time)} onClick={() => onSeek(segment.start_seconds)}>
                  {time}
                </button>
                <p className={styles.segmentText}>
                  {highlightParts(segment.text, matches, index).map((part, partIndex) =>
                    part.match === null ? (
                      <span key={partIndex}>{part.text}</span>
                    ) : (
                      <mark key={partIndex} data-match={part.match} className={cx(styles.mark, part.match === current && styles.markCurrent)}>
                        {part.text}
                      </mark>
                    ),
                  )}
                </p>
              </li>
            );
          })}
        </ol>
      )}
    </Card>
  );
});
