import { CircleSlash, CornerDownRight, PencilLine, Split, Tag } from "lucide-react";
import { Fragment, useLayoutEffect, useRef } from "react";

import { chipFor, Icon } from "../../components";
import { cx } from "../../design/cx";
import { useI18n, useT, type Messages } from "../../i18n";
import type { CalendarFormat } from "./format";
import type { CalendarLesson, Placement, WeekColumn } from "./model";
import styles from "./Calendar.module.css";

/** 48 px per hour: tall enough for two lines of text in a 90-minute lesson block. */
export const HOUR_PX = 48;
const PX_PER_MINUTE = HOUR_PX / 60;
/** Blocks shorter than this hide their second line. */
const ROOMY_PX = 52;

export function lessonSummary(t: Messages, fmt: CalendarFormat, lesson: CalendarLesson): string {
  const parts = [lesson.courseName];
  if (lesson.lessonNumber) parts.push(t.calendar.popover.lesson(lesson.lessonNumber));
  parts.push(fmt.dayLong(lesson.start), fmt.range(lesson.start, lesson.end));
  if (lesson.clash) parts.push(t.calendar.flags.clash);
  if (lesson.needsCourse) parts.push(t.calendar.flags.needsCourse);
  if (lesson.localChanges) parts.push(t.calendar.flags.edited);
  if (!lesson.clash && !lesson.needsCourse) parts.push(chipFor(lesson.recording.kind, lesson.recording.status).label(t.status));
  return parts.join(", ");
}

export interface BlockProps {
  placement: Placement<CalendarLesson>;
  fmt: CalendarFormat;
  open: boolean;
  onOpen: (lesson: CalendarLesson, anchor: HTMLElement) => void;
}

function LessonBlock({ placement, fmt, open, onOpen }: BlockProps) {
  const { t, lang } = useI18n();
  const lesson = placement.item;
  const top = placement.top * PX_PER_MINUTE;
  const height = Math.max(placement.height * PX_PER_MINUTE - 2, 20);
  const roomy = height >= ROOMY_PX;
  // Side by side with another lesson (a clash): usually too narrow for a course name.
  const shared = placement.span < placement.columnCount;
  const flag = lesson.clash
    ? { icon: Split, label: t.calendar.flags.clash }
    : lesson.needsCourse
      ? { icon: Tag, label: t.calendar.flags.needsCourse }
      : lesson.excluded
        ? { icon: CircleSlash, label: t.calendar.flags.excluded }
        : null;
  return (
    <li
      className={styles.blockSlot}
      style={{
        top,
        height,
        left: `calc(${(placement.column / placement.columnCount) * 100}% + 2px)`,
        width: `calc(${(placement.span / placement.columnCount) * 100}% - 4px)`,
      }}
    >
      <button
        type="button"
        className={cx(
          styles.block,
          styles[`tint${lesson.tint}`],
          lesson.clash && styles.clash,
          lesson.needsCourse && styles.attention,
          lesson.excluded && styles.excluded,
          !roomy && styles.compact,
          flag && styles.flagged,
          shared && styles.shared,
        )}
        aria-label={lessonSummary(t, fmt, lesson)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={shared ? lesson.courseName : undefined}
        onClick={(event) => onOpen(lesson, event.currentTarget)}
      >
        <span className={styles.blockFull}>
          {/* Course names are typed by the student; the UI language is the best hyphenation hint we have.
              Each word is its own box, so lines break between words and a word is only hyphenated
              (or ellipsized, where the engine has no dictionary) when it is wider than the block. */}
          <span className={styles.blockTitle} lang={lang}>
            {placement.continued && <Icon icon={CornerDownRight} size={14} className={styles.blockGlyph} />}
            {lesson.courseName.split(/\s+/).map((word, index) => (
              <Fragment key={index}>
                {index > 0 && " "}
                <span className={styles.blockWord}>{word}</span>
              </Fragment>
            ))}
            {/* No break between the dot and the number: the pair moves to the next line together. */}
            {lesson.lessonNumber ? <span className={styles.blockNumber}>{" · "}{lesson.lessonNumber}</span> : null}
          </span>
          {roomy && (
            <span className={styles.blockTime}>
              {/* Same text as fmt.range; the end time is its own box so a narrow block can drop it. */}
              <span>
                {fmt.time(lesson.start)}
                <span className={styles.blockTimeEnd}>–{fmt.time(lesson.end)}</span>
              </span>
              {lesson.localChanges && <Icon icon={PencilLine} size={14} className={styles.blockEdited} />}
            </span>
          )}
          {roomy && flag && (
            <span className={styles.blockFlag}>
              <Icon icon={flag.icon} size={14} />
              <span className={styles.blockFlagLabel}>{flag.label}</span>
            </span>
          )}
        </span>
        {/* Narrow face of a side-by-side block: start time and the flag icon. The full name stays in the
            accessible name, the tooltip and the popover; the CSS swaps faces by the block's real width. */}
        {shared && (
          <span className={styles.blockBrief}>
            <span className={styles.blockBriefTime}>{fmt.time(lesson.start)}</span>
            {flag && <Icon icon={flag.icon} size={14} className={styles.blockBriefFlag} />}
          </span>
        )}
      </button>
    </li>
  );
}

export interface WeekViewProps {
  columns: readonly WeekColumn[];
  fmt: CalendarFormat;
  openId: string | null;
  firstHour: number;
  now: Date;
  onOpen: (lesson: CalendarLesson, anchor: HTMLElement) => void;
}

/** Seven day columns on one scroller; the left gutter carries the hours of a normal day. */
export function WeekView({ columns, fmt, openId, firstHour, now, onOpen }: WeekViewProps) {
  const t = useT();
  const scroller = useRef<HTMLDivElement>(null);
  const rows = Math.max(24, ...columns.map((column) => column.hours.length));
  const reference = columns.find((column) => !column.shifted) ?? columns[0];

  useLayoutEffect(() => {
    if (scroller.current) scroller.current.scrollTop = Math.max(0, firstHour * HOUR_PX - 12);
    // Only when the week changes: scrolling stays where the student left it otherwise.
  }, [firstHour, reference?.key]);

  return (
    <div className={styles.week}>
      <div className={styles.weekHead}>
        <span className={styles.gutterHead} aria-hidden="true" />
        {columns.map((column) => (
          <div key={column.key} className={cx(styles.dayHead, column.isToday && styles.today, column.isWeekend && styles.weekend)} aria-hidden="true">
            <span className={styles.dayHeadWeekday}>{fmt.weekday(column.date)}</span>
            <span className={styles.dayHeadNumber}>{fmt.dayNumber(column.date)}</span>
          </div>
        ))}
      </div>
      <div ref={scroller} className={styles.weekScroll} role="region" aria-label={t.calendar.week} tabIndex={0}>
        <div className={styles.weekBody} style={{ height: rows * HOUR_PX }}>
          <ol className={styles.gutter} aria-hidden="true">
            {(reference?.hours ?? []).map((mark) => (
              <li key={mark.minute} className={styles.gutterHour} style={{ top: mark.minute * PX_PER_MINUTE }}>
                {mark.minute === 0 ? "" : fmt.time(mark.at)}
              </li>
            ))}
          </ol>
          <div className={styles.lines} aria-hidden="true">
            {Array.from({ length: rows }, (_, index) => (
              <span key={index} className={styles.line} style={{ top: index * HOUR_PX }} />
            ))}
          </div>
          {columns.map((column, index) => {
            const day = fmt.dayLong(column.date);
            const count = column.placements.length;
            return (
              <div
                key={column.key}
                className={cx(styles.dayColumn, column.isWeekend && styles.weekendColumn)}
                style={{ height: column.minutes * PX_PER_MINUTE, gridColumn: index + 2 }}
              >
                <h3 className="visually-hidden">
                  {day}, {t.calendar.dayCount(count)}
                  {column.shifted ? `, ${t.calendar.clockChange}` : ""}
                </h3>
                {column.shifted &&
                  column.hours.map((mark, index) =>
                    mark.hour !== index % 24 ? (
                      <span key={mark.minute} className={styles.shiftedHour} style={{ top: mark.minute * PX_PER_MINUTE }} aria-hidden="true">
                        {fmt.time(mark.at)}
                      </span>
                    ) : null,
                  )}
                {count > 0 && (
                  <ol className={styles.blocks} role="list">
                    {column.placements.map((placement) => (
                      <LessonBlock key={placement.item.id} placement={placement} fmt={fmt} open={openId === placement.item.id} onOpen={onOpen} />
                    ))}
                  </ol>
                )}
                {column.nowMinute !== null && (
                  <div className={styles.nowLine} style={{ top: column.nowMinute * PX_PER_MINUTE }} role="img" aria-label={t.calendar.now(fmt.time(now))}>
                    <span className={styles.nowDot} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
