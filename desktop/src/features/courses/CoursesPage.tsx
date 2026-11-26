import { CalendarDays, FilePen, FolderOpen, Hand, Plus } from "lucide-react";
import { useMemo } from "react";

import { Banner, Button, EmptyState, Icon, PageHeader } from "../../components";
import { useI18n } from "../../i18n";
import { routeToHash, useActions } from "../../state";
import { courseCards, type CourseCard } from "./model";
import { useCoursesInput } from "./useCourses";
import styles from "./Courses.module.css";

/** Course list (UX-SPEC §3 Courses): one card per course, the whole card is the link. */
export function CoursesPage() {
  const { t } = useI18n();
  const actions = useActions();
  const { input, ready, failed } = useCoursesInput();
  const cards = useMemo(() => courseCards(input), [input]);
  const addCourse = (
    <Button variant="soft" icon={Plus} onClick={() => actions.ui.navigate({ name: "welcome", step: "course" })}>
      {t.courses.addCourse}
    </Button>
  );

  return (
    <>
      <PageHeader title={t.courses.title} subtitle={ready ? t.courses.list.subtitle(cards.length) : undefined} actions={cards.length > 0 ? addCourse : undefined} />
      {failed ? (
        <Banner tone="danger" action={{ label: t.common.retry, onSelect: () => void actions.config.refresh() }}>
          {t.courses.list.loadError}
        </Banner>
      ) : !ready ? (
        <div className={styles.cards} aria-busy="true" aria-label={t.common.loading}>
          {[0, 1, 2, 3].map((index) => (
            <div key={index} className={styles.skeletonCard} />
          ))}
        </div>
      ) : cards.length === 0 ? (
        <EmptyState icon={FolderOpen} title={t.courses.list.empty} action={addCourse} />
      ) : (
        <ul role="list" className={styles.cards}>
          {cards.map((card) => (
            <li key={card.code}>
              <CourseCardView card={card} now={input.now} />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

function CourseCardView({ card, now }: { card: CourseCard; now: Date }) {
  const { t, format } = useI18n();
  const l = t.courses.list;
  const next = card.nextLesson;
  const nextText = next ? t.courses.nextLesson(`${format.dayShort(next.start)} · ${format.time(next.start)}`) : l.noNext;
  const notes = card.notes;
  const notesText =
    notes.updatedAt && notes.lessonNumber !== null ? t.courses.notesUpdated(format.relative(notes.updatedAt, now), t.common.lesson(notes.lessonNumber)) : l.noNotes;
  return (
    <a className={styles.card} href={routeToHash({ name: "course", code: card.code, tab: "lessons" })}>
      <span className={styles.cardHead}>
        <span className={styles.cardTitle}>{card.name}</span>
        <span className={styles.cardCount}>{l.lessons(card.lessonCount)}</span>
      </span>
      <span className={styles.cardFacts}>
        <span className={styles.cardFact}>
          <Icon icon={CalendarDays} size={16} className={styles.cardIcon} />
          <span className={next ? undefined : styles.mutedText}>{nextText}</span>
        </span>
        <span className={styles.cardFact}>
          <Icon icon={FilePen} size={16} className={styles.cardIcon} />
          <span className={notes.updatedAt ? undefined : styles.mutedText}>{notesText}</span>
        </span>
      </span>
      {card.attention > 0 && (
        <span className={styles.cardAttention}>
          <Icon icon={Hand} size={16} />
          {l.attention(card.attention)}
        </span>
      )}
    </a>
  );
}
