import { Split, Tag } from "lucide-react";

import { Icon } from "../../components";
import { cx } from "../../design/cx";
import { useT } from "../../i18n";
import type { CalendarFormat } from "./format";
import type { CalendarLesson, MonthCell } from "./model";
import { lessonSummary } from "./WeekView";
import styles from "./Calendar.module.css";

export interface MonthViewProps {
  cells: readonly MonthCell[];
  fmt: CalendarFormat;
  openId: string | null;
  onOpen: (lesson: CalendarLesson, anchor: HTMLElement) => void;
  onShowDay: (date: Date) => void;
}

/** Six weeks of seven days. A day shows three lessons, then "n more" opens its week. */
export function MonthView({ cells, fmt, openId, onOpen, onShowDay }: MonthViewProps) {
  const t = useT();
  const weekdays = cells.slice(0, 7);
  return (
    <div className={styles.month}>
      <div className={styles.monthHead} aria-hidden="true">
        {weekdays.map((cell) => (
          <span key={cell.key} className={styles.monthWeekday}>
            {fmt.weekday(cell.date)}
          </span>
        ))}
      </div>
      <ol className={styles.monthGrid} role="list">
        {cells.map((cell) => {
          const day = fmt.dayLong(cell.date);
          return (
            <li key={cell.key} className={cx(styles.cell, !cell.inMonth && styles.outside, cell.isWeekend && styles.weekendCell, cell.isToday && styles.todayCell)}>
              <h3 className="visually-hidden">
                {day}, {t.calendar.dayCount(cell.lessons.length)}
              </h3>
              <button type="button" className={styles.cellDay} aria-label={t.calendar.openDay(day)} aria-current={cell.isToday ? "date" : undefined} onClick={() => onShowDay(cell.date)}>
                {cell.day}
              </button>
              {cell.visible.length > 0 && (
                <ul className={styles.cellLessons} role="list">
                  {cell.visible.map((lesson) => (
                    <li key={lesson.id}>
                      <button
                        type="button"
                        className={cx(styles.chipLesson, styles[`tint${lesson.tint}`], lesson.clash && styles.clash, lesson.excluded && styles.excluded)}
                        aria-label={lessonSummary(t, fmt, lesson)}
                        aria-haspopup="dialog"
                        aria-expanded={openId === lesson.id}
                        onClick={(event) => onOpen(lesson, event.currentTarget)}
                      >
                        {lesson.clash && <Icon icon={Split} size={14} className={styles.chipLessonGlyph} />}
                        {!lesson.clash && lesson.needsCourse && <Icon icon={Tag} size={14} className={styles.chipLessonGlyph} />}
                        <span className={styles.chipLessonTime}>{fmt.time(lesson.start)}</span>
                        <span className={styles.chipLessonTitle}>{lesson.courseName}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              {cell.more > 0 && (
                <button type="button" className={styles.cellMore} aria-label={t.calendar.moreLabel(cell.more, day)} onClick={() => onShowDay(cell.date)}>
                  {t.calendar.more(cell.more)}
                </button>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
