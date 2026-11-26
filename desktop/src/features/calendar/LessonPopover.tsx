import { CalendarClock, CircleSlash, Clock, Globe, History, Mic, PencilLine, Split, Tag, X } from "lucide-react";

import { Button, Icon, IconButton, StatusChip } from "../../components";
import { useT } from "../../i18n";
import { routeToHash } from "../../state";
import type { CalendarFormat } from "./format";
import { originalTimeLabel, type CalendarLesson } from "./model";
import styles from "./Calendar.module.css";

export interface LessonPopoverProps {
  lesson: CalendarLesson;
  overlaps: readonly CalendarLesson[];
  fmt: CalendarFormat;
  zone: string;
  locale: string;
  busy: boolean;
  now: Date;
  onClose: () => void;
  onRestore: () => void;
  onEdit: () => void;
  onToggleRecording: (record: boolean) => void;
}

/** Lesson details from the calendar grid: what will happen, what clashes, and one fix each. */
export function LessonPopover({ lesson, overlaps, fmt, zone, locale, busy, now, onClose, onRestore, onEdit, onToggleRecording }: LessonPopoverProps) {
  const t = useT();
  const p = t.calendar.popover;
  const original = originalTimeLabel(lesson.start, lesson.timezone, zone, locale);
  const future = lesson.start.getTime() > now.getTime();
  const canToggle = future && !lesson.needsCourse;
  return (
    <div className={styles.popover}>
      <div className={styles.popoverHead}>
        <div className={styles.popoverHeading}>
          <h2 className={styles.popoverTitle}>{lesson.courseName}</h2>
          <p className={styles.popoverMeta}>
            {lesson.lessonNumber ? `${p.lesson(lesson.lessonNumber)} · ` : ""}
            {fmt.dayLong(lesson.start)}
          </p>
        </div>
        <IconButton label={p.close} icon={X} size="sm" onClick={onClose} />
      </div>

      <ul className={styles.facts} role="list">
        <li>
          <Icon icon={Clock} size={16} className={styles.factIcon} />
          <span className="tabular">{fmt.range(lesson.start, lesson.end)}</span>
        </li>
        {original && (
          <li>
            <Icon icon={Globe} size={16} className={styles.factIcon} />
            <span>{p.originalTime(original)}</span>
          </li>
        )}
        <li>
          <Icon icon={CalendarClock} size={16} className={styles.factIcon} />
          <span>{lesson.special ? p.addedHere : lesson.sourceLabel ? p.source(lesson.sourceLabel) : p.addedHere}</span>
        </li>
      </ul>

      <div className={styles.popoverChips}>
        <StatusChip kind={lesson.recording.kind} status={lesson.recording.status} />
        {lesson.transcription && <StatusChip kind={lesson.transcription.kind} status={lesson.transcription.status} />}
      </div>

      {lesson.clash && (
        <div className={styles.notice}>
          <Icon icon={Split} size={16} className={styles.noticeIcon} />
          <div>
            {overlaps.map((other) => (
              <p key={other.id}>{p.clashWith(other.courseName, fmt.range(other.start, other.end))}</p>
            ))}
            <p className={styles.noticeFix}>{p.clashFix}</p>
          </div>
        </div>
      )}
      {lesson.needsCourse && (
        <div className={styles.notice}>
          <Icon icon={Tag} size={16} className={styles.noticeIcon} />
          <p>{p.needsCourse}</p>
        </div>
      )}
      {lesson.localChanges && (
        <div className={styles.noticeQuiet}>
          <Icon icon={PencilLine} size={16} className={styles.factIcon} />
          <p>{p.edited}</p>
        </div>
      )}

      <div className={styles.popoverActions}>
        {lesson.needsCourse && (
          <a className={styles.popoverLink} href={routeToHash({ name: "settings", section: "calendars" })} onClick={onClose}>
            {p.chooseCourse}
          </a>
        )}
        {lesson.localChanges && (
          <Button size="sm" variant="soft" icon={History} loading={busy} onClick={onRestore}>
            {p.restore}
          </Button>
        )}
        {future && (
          <Button size="sm" variant="outline" icon={PencilLine} disabled={busy} onClick={onEdit}>
            {p.editTime}
          </Button>
        )}
        {canToggle && (
          <Button size="sm" variant="ghost" icon={lesson.recordingEnabled ? CircleSlash : Mic} disabled={busy} onClick={() => onToggleRecording(!lesson.recordingEnabled)}>
            {lesson.recordingEnabled ? p.skip : p.record}
          </Button>
        )}
        {lesson.lectureId && !future && (
          <a className={styles.popoverLink} href={routeToHash({ name: "lesson", id: lesson.lectureId })} onClick={onClose}>
            {p.openLesson}
          </a>
        )}
      </div>
    </div>
  );
}
