import { BookOpen, FilePen, FileText, FileSearch, Presentation } from "lucide-react";
import { useId, type ReactNode } from "react";

import { Button, Card, Icon, StatusChip } from "../../components";
import { useI18n } from "../../i18n";
import type { Job } from "../../rpc";
import { fileName } from "../courses/model";
import { flowView, stageTimeline } from "./generate/model";
import type { StageTiming } from "../../state/slices/jobs";
import type { LessonDetail } from "./model";
import styles from "./Lesson.module.css";

export interface Materials {
  slides: string[];
  notebookSection: string | null;
}

export interface LessonRailProps {
  lesson: LessonDetail;
  history: readonly StageTiming[];
  materials: Materials;
  now: Date;
  /** The page's one primary action lives here when the transcript exists. */
  onGenerate: () => void;
  onShowJob: (job: Job) => void;
  onOpenWord: () => void;
}

/** Right rail (320 px): Notes card, Materials found, recording metadata. */
export function LessonRail({ lesson, history, materials, now, onGenerate, onShowJob, onOpenWord }: LessonRailProps) {
  const { t, format } = useI18n();
  const r = t.lesson.rail;
  const notesId = useId();
  const materialsId = useId();
  const recordingId = useId();
  const job = lesson.notesJob;
  const view = flowView(job);
  const transcriptReady = lesson.state === "ready";

  let notesBody: string;
  let action: ReactNode = null;
  if (view.view === "running") {
    const timeline = stageTimeline(view.job, history, now);
    const label = t.lesson.generate.stages[timeline.current as keyof typeof t.lesson.generate.stages] ?? t.lesson.generate.stages.CHECKING;
    notesBody = r.running(label, format.clock(timeline.totalSeconds));
    action = (
      <Button variant="soft" fullWidth onClick={() => onShowJob(view.job)}>
        {r.showProgress}
      </Button>
    );
  } else if (view.view === "outcome") {
    const kind = view.outcome.kind;
    const bodies: Record<typeof kind, string> = {
      completed: r.notesReady,
      warnings: r.notesReady,
      awaitingApproval: t.lesson.outcome.awaitingApproval,
      needsTranscript: r.afterTranscript,
      pausedQuota: t.common.progressSaved,
      pausedAuth: t.common.progressSaved,
      pausedNetwork: t.common.progressSaved,
      pausedSleep: t.common.progressSaved,
      cancelled: t.common.progressSaved,
      conflict: t.lesson.outcome.needsReview,
      needsReview: t.lesson.outcome.needsReview,
    };
    notesBody = bodies[kind];
    if (kind === "completed" || kind === "warnings") {
      action = (
        <>
          <Button variant="soft" fullWidth onClick={onOpenWord}>
            {r.openInWord}
          </Button>
          <Button variant="ghost" fullWidth onClick={() => onShowJob(view.job)}>
            {r.seeDetails}
          </Button>
        </>
      );
    } else {
      action = (
        <Button variant="primary" fullWidth icon={kind === "awaitingApproval" ? FileSearch : undefined} onClick={() => onShowJob(view.job)}>
          {kind === "awaitingApproval" ? r.reviewNotes : r.seeDetails}
        </Button>
      );
    }
  } else if (lesson.chips.notes.status === "COMPLETED") {
    notesBody = r.notesReady;
    action = (
      <>
        <Button variant="soft" fullWidth onClick={onOpenWord}>
          {r.openInWord}
        </Button>
        <Button variant="ghost" fullWidth disabled={!transcriptReady} onClick={onGenerate}>
          {r.generateAgain}
        </Button>
      </>
    );
  } else if (transcriptReady) {
    notesBody = r.noNotes;
    action = (
      <Button variant="primary" fullWidth icon={FilePen} onClick={onGenerate}>
        {t.lesson.generateNotes}
      </Button>
    );
  } else {
    notesBody = r.afterTranscript;
  }

  const recording = lesson.recording;
  return (
    <aside className={styles.rail} aria-label={t.lesson.title}>
      <Card as="section" aria-labelledby={notesId} className={styles.railCard}>
        <div className={styles.railHead}>
          <h2 id={notesId} className={styles.cardTitle}>
            {r.notesTitle}
          </h2>
          <StatusChip kind="notes" status={lesson.chips.notes.status} />
        </div>
        <p className={styles.railBody}>{notesBody}</p>
        {action && <div className={styles.railActions}>{action}</div>}
      </Card>

      <Card as="section" aria-labelledby={materialsId} className={styles.railCard}>
        <h2 id={materialsId} className={styles.cardTitle}>
          {r.materialsTitle}
        </h2>
        <ul role="list" className={styles.facts}>
          {materials.slides.length === 0 && (
            <li className={styles.fact}>
              <Icon icon={Presentation} size={16} className={styles.factIcon} />
              <span className={styles.muted}>{r.noMaterials}</span>
            </li>
          )}
          {materials.slides.map((slide) => (
            <li key={slide} className={styles.fact}>
              <Icon icon={FileText} size={16} className={styles.factIcon} />
              <span className={styles.factText}>{fileName(slide) ?? slide}</span>
            </li>
          ))}
          <li className={styles.fact}>
            <Icon icon={BookOpen} size={16} className={styles.factIcon} />
            <span className={materials.notebookSection ? styles.factText : styles.muted}>
              {materials.notebookSection ? r.notebookSection(materials.notebookSection) : r.noNotebookSection}
            </span>
          </li>
        </ul>
      </Card>

      <Card as="section" aria-labelledby={recordingId} className={styles.railCard}>
        <h2 id={recordingId} className={styles.cardTitle}>
          {r.recordingTitle}
        </h2>
        {recording ? (
          <dl className={styles.meta}>
            <div>
              <dt>{r.length}</dt>
              <dd className={styles.tabular}>{format.minutes(recording.duration ?? 0)}</dd>
            </div>
            {recording.recorded_at && (
              <div>
                <dt>{r.recorded}</dt>
                <dd className={styles.tabular}>
                  {format.dayShort(new Date(recording.recorded_at))} · {format.time(new Date(recording.recorded_at))}
                </dd>
              </div>
            )}
            <div>
              <dt>{r.source}</dt>
              <dd>{r.origin(recording.origin ?? "")}</dd>
            </div>
            <div>
              <dt>{r.file}</dt>
              <dd className={styles.fileName}>{fileName(recording.path) ?? recording.path}</dd>
            </div>
          </dl>
        ) : (
          <p className={styles.muted}>{r.noRecording}</p>
        )}
      </Card>
    </aside>
  );
}
