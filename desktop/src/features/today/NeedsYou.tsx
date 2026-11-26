import { ArrowRight, CircleCheck } from "lucide-react";
import { useId } from "react";

import { Card, Icon, List, ListRow, StatusChip } from "../../components";
import { useT, type Messages } from "../../i18n";
import { routeToHash } from "../../state";
import type { NeedsItem, NeedsReason } from "./model";
import { lessonLabel } from "./TodayHero";
import styles from "./Today.module.css";

/** The provider name stays explicit whenever data leaves the computer (UX-SPEC §5). */
const ASSISTANT_NAME = "Claude Code";

export function reasonText(t: Messages, reason: NeedsReason): string {
  const n = t.today.needsYou;
  switch (reason) {
    case "transcriptionToCheck":
      return n.transcriptionToCheck;
    case "transcriptionWaiting":
      return n.transcriptionWaiting;
    case "notesApproval":
      return n.notesApproval;
    case "notesPausedQuota":
      return n.notesPausedQuota(ASSISTANT_NAME);
    case "notesPausedAuth":
      return n.notesPausedAuth(ASSISTANT_NAME);
    case "notesPausedNetwork":
      return n.notesPausedNetwork;
    case "notesConflict":
      return n.notesConflict;
    case "notesWarnings":
      return n.notesWarnings;
    case "notesNeedsReview":
      return n.notesNeedsReview;
    case "notesNeedsTranscript":
      return n.notesNeedsTranscript;
    case "recordingMissed":
      return n.recordingMissed;
    case "recordingFailed":
      return n.recordingFailed;
    case "timeClash":
      return n.timeClash;
    case "needsCourse":
      return n.needsCourse;
  }
}

export function NeedsYou({ items, total }: { items: readonly NeedsItem[]; total: number }) {
  const t = useT();
  const titleId = useId();
  return (
    <section className={styles.section} aria-labelledby={titleId}>
      <div className={styles.sectionHead}>
        <div className={styles.sectionTitleGroup}>
          <h2 id={titleId} className={styles.sectionTitle}>
            {t.today.needsYou.title}
          </h2>
          {total > 0 && <span className={styles.count}>{t.today.needsYou.count(total)}</span>}
        </div>
        {total > 0 && (
          <a className={styles.seeAll} href={routeToHash({ name: "activity" })}>
            {t.common.seeAll}
            <Icon icon={ArrowRight} size={16} />
          </a>
        )}
      </div>
      {items.length === 0 ? (
        <p className={styles.calm}>
          <Icon icon={CircleCheck} size={20} className={styles.calmIcon} />
          {t.today.needsYou.empty}
        </p>
      ) : (
        <Card padding="none">
          <List>
            {items.map((item) => (
              <li key={item.id}>
                <ListRow
                  title={lessonLabel(t, item.lesson)}
                  description={reasonText(t, item.reason)}
                  meta={<StatusChip kind={item.chip.kind} status={item.chip.status} />}
                  href={routeToHash(item.target)}
                />
              </li>
            ))}
          </List>
        </Card>
      )}
    </section>
  );
}
