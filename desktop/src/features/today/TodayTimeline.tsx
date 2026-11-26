import { FileAudio } from "lucide-react";
import { Fragment, useId } from "react";

import { Button, EmptyState, StatusChip } from "../../components";
import { cx } from "../../design/cx";
import { useI18n } from "../../i18n";
import type { TodayLesson } from "./model";
import styles from "./Today.module.css";

export interface TodayTimelineProps {
  lessons: readonly TodayLesson[];
  now: Date;
  onImport: () => void;
}

/** Today's lessons on a vertical rail, with a "now" line between past and upcoming. */
export function TodayTimeline({ lessons, now, onImport }: TodayTimelineProps) {
  const { t, format } = useI18n();
  const titleId = useId();
  const nowMs = now.getTime();
  // The now-line sits before the first lesson that has not started (none while one is live).
  const live = lessons.some((lesson) => lesson.phase === "live");
  const nowIndex = live ? -1 : lessons.findIndex((lesson) => lesson.start.getTime() > nowMs);
  const nowAt = nowIndex === -1 && !live && lessons.length > 0 ? lessons.length : nowIndex;

  const nowMarker = (
    <li className={styles.now}>
      <span className={styles.nowTime}>
        <span className="visually-hidden">{t.today.timeline.now} </span>
        {format.time(now)}
      </span>
      <span className={styles.nowDot} aria-hidden="true" />
      <span className={styles.nowLine} aria-hidden="true" />
    </li>
  );

  return (
    <section className={styles.section} aria-labelledby={titleId}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionTitleGroup}>
          <h2 id={titleId} className={styles.sectionTitle}>
            {t.today.timeline.title}
          </h2>
          {lessons.length > 0 && <span className={styles.count}>{t.today.timeline.count(lessons.length)}</span>}
        </div>
      </div>
      {lessons.length === 0 ? (
        <div className={styles.emptyCard}>
          <EmptyState
            icon={FileAudio}
            title={t.today.empty.title}
            align="start"
            action={
              <Button variant="soft" onClick={onImport}>
                {t.today.empty.action}
              </Button>
            }
          />
        </div>
      ) : (
        <ol className={styles.timeline} role="list">
          {lessons.map((lesson, index) => (
            <Fragment key={lesson.eventId}>
              {index === nowAt && nowMarker}
              <li className={cx(styles.slot, styles[lesson.phase])} aria-current={lesson.phase === "live" ? "time" : undefined}>
                <span className={styles.slotTime}>
                  <time dateTime={lesson.start.toISOString()}>{format.time(lesson.start)}</time>
                  <time className={styles.slotEnd} dateTime={lesson.end.toISOString()}>
                    {format.time(lesson.end)}
                  </time>
                </span>
                <span className={styles.rail} aria-hidden="true">
                  <span className={styles.node} />
                </span>
                <span className={styles.slotBody}>
                  <span className={styles.slotCourse}>{lesson.courseName}</span>
                  <span className={styles.slotMeta}>{lesson.lessonNumber ? t.common.lesson(lesson.lessonNumber) : t.common.unassigned}</span>
                </span>
                <StatusChip kind={lesson.chip.kind} status={lesson.chip.status} />
              </li>
            </Fragment>
          ))}
          {nowAt === lessons.length && nowMarker}
        </ol>
      )}
    </section>
  );
}
