import { CircleAlert, CircleCheck, FilePen, FileSearch, HardDrive, Mic, Settings, Square, type LucideIcon } from "lucide-react";
import { useId } from "react";

import { Button, Icon, StatusChip } from "../../components";
import { cx } from "../../design/cx";
import { useI18n, type Messages } from "../../i18n";
import type { Hero, HeroAction, LessonRef } from "./model";
import styles from "./Today.module.css";

interface ActionCopy {
  label: string;
  body: string;
  /** Icon of the button. */
  icon: LucideIcon;
  /** Icon in front of the sentence; problems always use the alert glyph. */
  note: LucideIcon;
  problem: boolean;
}

export function lessonLabel(t: Messages, lesson: LessonRef): string {
  return lesson.lessonNumber ? t.common.courseLesson(lesson.courseName, lesson.lessonNumber) : lesson.courseName;
}

function actionCopy(t: Messages, action: HeroAction): ActionCopy {
  const a = t.today.action;
  switch (action.kind) {
    case "stop":
      return { label: a.stop, body: a.stopBody, icon: Square, note: HardDrive, problem: false };
    case "fix":
      if (action.problem === "microphone") return { label: a.fixMic, body: a.fixMicBody, icon: Settings, note: CircleAlert, problem: true };
      if (action.problem === "recorderStopped") return { label: a.fixRecorder, body: a.fixRecorderBody, icon: Mic, note: CircleAlert, problem: true };
      return { label: a.fixFailed, body: a.fixFailedBody, icon: CircleAlert, note: CircleAlert, problem: true };
    case "review":
      return { label: a.review, body: a.reviewBody(lessonLabel(t, action.lesson)), icon: FileSearch, note: FilePen, problem: false };
    case "generate":
      return { label: a.generate, body: a.generateBody(lessonLabel(t, action.lesson)), icon: FilePen, note: CircleCheck, problem: false };
    case "record":
      return { label: a.record, body: a.recordBody, icon: Mic, note: CircleCheck, problem: false };
  }
}

export interface TodayHeroProps {
  hero: Hero;
  now: Date;
  busy: boolean;
  onAction: (action: HeroAction) => void;
}

/** Light paper panel with the coral bookmark: the next (or live) lesson and the screen's one primary action. */
export function TodayHero({ hero, now, busy, onAction }: TodayHeroProps) {
  const { t, format } = useI18n();
  const titleId = useId();
  const copy = actionCopy(t, hero.action);
  const lesson = hero.lesson;

  let label: string;
  if (hero.mode === "live") label = t.today.hero.recordingNow;
  else if (hero.mode === "none") label = t.today.hero.noneScheduled;
  else if (lesson && !hero.isToday) label = t.today.hero.nextOn(format.dayLong(lesson.start));
  else label = t.today.hero.nextLesson;

  const meta: string[] = [];
  if (lesson?.lessonNumber) meta.push(t.common.lesson(lesson.lessonNumber));
  if (lesson && hero.mode === "live" && hero.startedAt) meta.push(t.today.hero.startedAt(format.time(hero.startedAt)));
  else if (lesson) meta.push(format.timeRange(lesson.start, lesson.end));
  if (lesson && hero.mode === "next" && hero.isToday) meta.push(t.today.hero.startsIn(format.relative(lesson.start, now)));

  const elapsed = hero.mode === "live" && hero.startedAt ? format.clock((now.getTime() - hero.startedAt.getTime()) / 1000) : null;

  return (
    <section className={cx(styles.hero, "surface-inverse")} aria-labelledby={titleId}>
      <span className={styles.bookmark} aria-hidden="true" />
      <div className={styles.heroTop}>
        {/* Live: the pulsing chip says it all; a text label would repeat it. */}
        {hero.mode !== "live" && <p className={styles.heroLabel}>{label}</p>}
        {hero.mode === "live" && <span className="visually-hidden">{label}</span>}
        {hero.mode !== "none" && <StatusChip kind={hero.recorderChip.kind} status={hero.recorderChip.status} />}
      </div>
      <div className={styles.heroMain}>
        <div>
          <h2 id={titleId} className={styles.heroTitle}>
            {lesson ? lesson.courseName : t.today.hero.noneScheduled}
          </h2>
          <p className={styles.heroMeta}>
            {lesson
              ? meta.map((part, index) => (
                  <span key={part}>
                    {index > 0 && (
                      <span className={styles.sep} aria-hidden="true">
                        ·
                      </span>
                    )}
                    {part}
                  </span>
                ))
              : t.today.hero.noneScheduledBody}
          </p>
        </div>
        {elapsed && (
          <div className={styles.timer}>
            <span className={styles.timerValue} role="timer" aria-label={`${t.today.hero.elapsed} ${elapsed}`}>
              {elapsed}
            </span>
            <span className={styles.timerLabel} aria-hidden="true">
              {t.today.hero.elapsed}
            </span>
          </div>
        )}
      </div>
      <div className={styles.heroFooter}>
        <p className={styles.heroNext}>
          <Icon icon={copy.note} size={20} className={cx(styles.heroNextIcon, copy.problem && styles.heroNextProblem)} />
          <span>{copy.body}</span>
        </p>
        <Button variant="primary" icon={copy.icon} loading={busy} onClick={() => onAction(hero.action)}>
          {copy.label}
        </Button>
      </div>
    </section>
  );
}
