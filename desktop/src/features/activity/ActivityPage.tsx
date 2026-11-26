import { AudioLines, CircleCheck, Clock, FileAudio, FilePen, ListX, Pause, Play, TriangleAlert, type LucideIcon } from "lucide-react";
import { useId, useMemo, useState, type ReactNode } from "react";

import { useNow } from "../../app/useNow";
import { Banner, Button, Card, EmptyState, Icon, List, ListRow, Menu, PageHeader, Segmented, StatusChip } from "../../components";
import { useI18n, type Messages } from "../../i18n";
import type { Job } from "../../rpc";
import { routeToHash, shallowEqual, useActions, useAppState, type AppState } from "../../state";
import { attempt, type ActionResult } from "../../state/result";
import { requestImport } from "../library/importRequest";
import { deriveActivity, type ActivityLesson, type NotesRow, type QueueRow, type RowAction } from "./model";
import styles from "./Activity.module.css";

const select = (state: AppState) => ({
  queue: state.queue.status,
  queueLoad: state.queue.load,
  library: state.library.library,
  jobsById: state.jobs.byId,
  jobOrder: state.jobs.order,
  courses: state.capture.dashboard?.courses ?? state.config.config?.courses ?? null,
});

const PARALLEL = ["1", "2", "3", "4"] as const;

export function activityLessonLabel(t: Messages, lesson: ActivityLesson): string {
  if (lesson.courseName && lesson.lessonNumber) return t.common.courseLesson(lesson.courseName, lesson.lessonNumber);
  return lesson.courseName ?? lesson.title;
}

function actionLabel(t: Messages, action: RowAction): string {
  return t.activity.actions[action];
}

function Section({ title, count, icon, children }: { title: string; count: number; icon: LucideIcon; children: ReactNode }) {
  const id = useId();
  return (
    <section className={styles.section} aria-labelledby={id}>
      <div className={styles.sectionHead}>
        <Icon icon={icon} size={20} className={styles.sectionIcon} />
        <h2 id={id} className={styles.sectionTitle}>
          {title}
        </h2>
        <span className={styles.count}>{count}</span>
      </div>
      <Card padding="none">
        <List>{children}</List>
      </Card>
    </section>
  );
}

/** Activity (UX-SPEC §3): the transcription queue by section, plus notes jobs. */
export function ActivityPage() {
  const { t, format } = useI18n();
  const actions = useActions();
  const now = useNow(30_000);
  const input = useAppState(select, shallowEqual);
  const [busy, setBusy] = useState<string | null>(null);

  const jobs = useMemo(() => input.jobOrder.map((id) => input.jobsById[id]).filter((job): job is Job => job !== undefined), [input.jobOrder, input.jobsById]);
  const model = useMemo(
    () => deriveActivity({ queue: input.queue, library: input.library, jobs, courses: input.courses ?? [] }),
    [input.queue, input.library, jobs, input.courses],
  );

  const run = async (key: string, task: () => Promise<ActionResult<unknown>>, success: string) => {
    setBusy(key);
    const result = await task();
    setBusy(null);
    actions.ui.toast(result.ok ? { message: success, tone: "neutral" } : { message: t.activity.failed, tone: "danger" });
  };

  const header = <PageHeader title={t.activity.title} subtitle={model.ready ? t.activity.summary(model.finishing, model.toCheck.length) : undefined} />;

  if (!model.ready) {
    return (
      <>
        {header}
        {input.queueLoad.status === "error" ? (
          <Banner tone="danger" action={{ label: t.common.retry, onSelect: () => void actions.queue.refresh() }}>
            {t.activity.loadError}
          </Banner>
        ) : (
          <div className={styles.skeleton} aria-busy="true" aria-label={t.common.loading} />
        )}
      </>
    );
  }

  const togglePause = () =>
    void run("pause", () => actions.queue.setPaused(!model.paused), model.paused ? t.activity.done.resumed : t.activity.done.paused);
  const readyIds = model.readyToTranscribe.map((row) => row.recordingId);

  const queueRow = (row: QueueRow, extra?: ReactNode) => {
    const label = activityLessonLabel(t, row.lesson);
    let description: string;
    if (row.chip.status === "RUNNING") description = row.elapsedSeconds !== null ? t.activity.elapsed(format.minutes(row.elapsedSeconds)) : t.status.transcription.running;
    else if (row.chip.status === "QUEUED") description = row.queuePosition ? t.activity.position(row.queuePosition) : t.activity.queued;
    else if (row.chip.status === "CANCELLING") description = t.activity.stopping;
    else if (row.reason) description = t.activity.reasons[row.reason];
    else if (row.chip.status === "COMPLETED") description = row.elapsedSeconds ? t.activity.historyDone(format.minutes(row.elapsedSeconds)) : t.activity.historyDoneShort;
    else if (row.chip.status === "CANCELLED") description = t.activity.historyRemoved;
    else description = row.lesson.courseCode ? t.activity.readyBody : `${t.activity.noCourse} · ${t.activity.readyBody}`;
    const [first, ...rest] = row.actions;
    const act = (action: RowAction) => {
      if (action === "finish") return void run(row.id, () => actions.queue.finishSaving(row.recordingId), t.activity.done.action);
      if (row.jobId) {
        const jobId = row.jobId;
        void run(row.id, () => actions.queue.jobAction(jobId, action), t.activity.done.action);
      }
    };
    return (
      <li key={row.id}>
        <ListRow
          title={label}
          description={description}
          meta={
            <>
              <StatusChip kind={row.chip.kind} status={row.chip.status} />
              {extra}
              {first && (
                <Button size="sm" variant={first === "remove" || first === "cancel" ? "ghost" : "outline"} loading={busy === row.id} onClick={() => act(first)}>
                  {actionLabel(t, first)}
                </Button>
              )}
              {rest.length > 0 && (
                <Menu
                  trigger="icon"
                  size="sm"
                  align="end"
                  label={t.activity.actions.more(label)}
                  items={rest.map((action) => ({ id: action, label: actionLabel(t, action), tone: action === "remove" ? "danger" : undefined, onSelect: () => act(action) }))}
                />
              )}
            </>
          }
        />
      </li>
    );
  };

  const notesRow = (row: NotesRow) => {
    const label = row.lessons.length > 0 ? row.lessons.map((lesson) => activityLessonLabel(t, lesson)).join(", ") : t.activity.notesJobs;
    const when = row.running ? t.activity.notes.started(format.relative(new Date(row.startedAt), now)) : t.activity.notes.finished(format.relative(new Date(row.finishedAt ?? row.startedAt), now));
    const lectureId = row.lessons[0]?.lectureId ?? null;
    return (
      <li key={row.jobId}>
        <ListRow
          title={label}
          description={when}
          meta={
            <>
              <StatusChip kind="notes" status={row.status} />
              {row.running ? (
                <Button size="sm" variant="ghost" loading={busy === row.jobId} onClick={() => void run(row.jobId, () => attempt(() => actions.jobs.cancel(row.jobId)), t.activity.notes.stopped)}>
                  {t.activity.notes.stop}
                </Button>
              ) : (
                lectureId && (
                  <a className={styles.rowLink} href={routeToHash({ name: "lesson", id: lectureId })}>
                    {t.activity.notes.open}
                  </a>
                )
              )}
            </>
          }
        />
      </li>
    );
  };

  const nothing = model.inProgress.length + model.readyToTranscribe.length + model.toCheck.length + model.history.length + model.notes.length === 0;

  return (
    <>
      {header}
      <div className={styles.page}>
        <section className={styles.controls} aria-label={t.activity.controls}>
          <div className={styles.controlsGroup}>
            <Button variant="outline" icon={model.paused ? Play : Pause} loading={busy === "pause"} onClick={togglePause}>
              {model.paused ? t.activity.resumeAll : t.activity.pauseAll}
            </Button>
            <div className={styles.parallel} title={t.activity.atTheSameTimeHint}>
              <span className={styles.parallelLabel} aria-hidden="true">
                {t.activity.atTheSameTime}
              </span>
              <Segmented
                label={t.activity.atTheSameTime}
                hideLabel
                value={String(model.parallel) as (typeof PARALLEL)[number]}
                options={PARALLEL.map((value) => ({ value, label: value }))}
                onChange={(value) => void run("parallel", () => actions.queue.setParallel(Number(value)), t.activity.done.parallel(Number(value)))}
              />
            </div>
          </div>
          <div className={styles.controlsGroup}>
            <Button variant="ghost" icon={ListX} disabled={model.waiting === 0} loading={busy === "clear"} onClick={() => void run("clear", () => actions.queue.clearWaiting(), t.activity.done.cleared)}>
              {t.activity.clearWaiting}
            </Button>
            <Button
              variant="primary"
              icon={AudioLines}
              disabled={readyIds.length === 0}
              loading={busy === "all"}
              onClick={() => void run("all", () => actions.queue.enqueue(), t.activity.done.enqueued(readyIds.length))}
            >
              {t.activity.transcribeAll}
            </Button>
          </div>
        </section>

        {model.paused && (
          <Banner tone="warning">
            <strong className={styles.bannerStrong}>{t.activity.paused(model.waiting, model.finishing)}</strong> {t.activity.pausedBody}
          </Banner>
        )}

        {nothing && (
          <div className={styles.emptyCard}>
            <EmptyState
              icon={FileAudio}
              title={t.activity.empty}
              body={t.activity.emptyBody}
              align="start"
              action={
                <Button
                  variant="soft"
                  onClick={() => {
                    requestImport();
                    actions.ui.navigate({ name: "library", course: null, q: "", unassigned: false });
                  }}
                >
                  {t.activity.importAudio}
                </Button>
              }
            />
          </div>
        )}

        {model.inProgress.length > 0 && (
          <Section title={t.activity.inProgress} count={model.inProgress.length} icon={AudioLines}>
            {model.inProgress.map((row) => queueRow(row))}
          </Section>
        )}
        {model.toCheck.length > 0 && (
          <Section title={t.activity.toCheck} count={model.toCheck.length} icon={TriangleAlert}>
            {model.toCheck.map((row) => queueRow(row))}
          </Section>
        )}
        {model.readyToTranscribe.length > 0 && (
          <Section title={t.activity.readyToTranscribe} count={model.readyToTranscribe.length} icon={Clock}>
            {model.readyToTranscribe.map((row) =>
              queueRow(
                row,
                <Button size="sm" variant="outline" loading={busy === row.id} onClick={() => void run(row.id, () => actions.queue.enqueue([row.recordingId]), t.activity.done.enqueued(1))}>
                  {t.activity.actions.transcribe}
                </Button>,
              ),
            )}
          </Section>
        )}
        {model.notes.length > 0 && (
          <Section title={t.activity.notesJobs} count={model.notes.length} icon={FilePen}>
            {model.notes.map(notesRow)}
          </Section>
        )}
        {model.history.length > 0 && (
          <Section title={t.activity.history} count={model.history.length} icon={CircleCheck}>
            {model.history.map((row) => queueRow(row))}
          </Section>
        )}
      </div>
    </>
  );
}
