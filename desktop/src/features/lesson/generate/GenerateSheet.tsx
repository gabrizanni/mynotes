import { Check, CircleAlert, CircleCheck, CircleStop, Send, TriangleAlert, Undo2, type LucideIcon } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";

import { useNow } from "../../../app/useNow";
import { Banner, Button, ChoiceList, Disclosure, Icon, Sheet, StageStepper, Stepper, TextArea, TextField, Toggle, type Stage } from "../../../components";
import { cx } from "../../../design/cx";
import { useI18n } from "../../../i18n";
import type { Job } from "../../../rpc";
import { useActions, useAppState, useRpc } from "../../../state";
import type { StageTiming } from "../../../state/slices/jobs";
import { courseFiles, courseRecord, reviewFirst, withPublicationMode } from "../../courses/model";
import { assistantName, copyText, openPath } from "../platform";
import { useRpcData } from "../useRpcData";
import {
  DEFAULT_OPTIONS,
  DONE_STAGE,
  flowView,
  jobDetails,
  lessonNumberOf,
  missingTranscripts,
  normalizeLessonList,
  NOTES_STAGES,
  runParams,
  sourceChecklist,
  stageTimeline,
  warningsOf,
  type GenerateOptions,
  type Outcome,
  type SourceRow,
} from "./model";
import { afterTranscriptions, completedToast, undoLastUpdate, watchNotesJob } from "./watch";
import styles from "./Generate.module.css";

export interface GenerateSheetProps {
  open: boolean;
  onClose: () => void;
  courseCode: string;
  /** Lessons of one course, e.g. ["STAT-L05", "STAT-L06"]. */
  lectureIds: readonly string[];
  /** The job to show when the sheet opens (running or finished); `null` starts a new run. */
  initialJobId: string | null;
}

const NO_TIMINGS: readonly StageTiming[] = [];

/**
 * "Notes for Lesson 7 · Statistics" (UX-SPEC §3 Generate notes flow): sources and options,
 * then the live stepper, then one outcome with one fix. The sheet can close at any time;
 * the job keeps going and its toast finds the student wherever they are.
 */
export function GenerateSheet({ open, onClose, courseCode, lectureIds, initialJobId }: GenerateSheetProps) {
  const { t } = useI18n();
  const g = t.lesson.generate;
  const actions = useActions();
  const rpc = useRpc();
  const now = useNow(1000);
  const config = useAppState((state) => state.config.config);
  const course = useMemo(() => courseRecord(config, courseCode), [config, courseCode]);
  const courseName = typeof course?.name === "string" ? course.name : courseCode;
  const assistant = assistantName(config?.providers?.default);
  const numbers = useMemo(() => lectureIds.map(lessonNumberOf).filter((n): n is number => n !== null).sort((a, b) => a - b), [lectureIds]);
  const lessons = t.lesson.lessonsLabel(numbers);
  const lectureKey = lectureIds.join(",");

  const [jobId, setJobId] = useState<string | null>(initialJobId);
  const [options, setOptions] = useState<GenerateOptions>(DEFAULT_OPTIONS);
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState(() => reviewFirst(course));
  const [contextError, setContextError] = useState(false);
  const shown = useRef({ open, jobId });
  shown.current = { open, jobId };

  useEffect(() => {
    if (!open) return;
    setJobId(initialJobId);
    setOptions(DEFAULT_OPTIONS);
    setContextError(false);
    // Each opening starts from the lesson's current job; edits made while open stay.
  }, [open, initialJobId, lectureKey]);
  useEffect(() => setReview(reviewFirst(course)), [course]);

  const job = useAppState((state) => (jobId ? (state.jobs.byId[jobId] ?? null) : null));
  const history = useAppState((state) => (jobId ? (state.jobs.stages[jobId] ?? NO_TIMINGS) : NO_TIMINGS));
  const starting = jobId !== null && job === null;
  const view = starting ? ({ view: "running" } as const) : flowView(job);

  const plan = useRpcData("notes.plan", open && view.view === "setup" ? { course: courseCode, lessons: numbers.join(",") } : null, `${courseCode}:${lectureKey}:${open}`);
  const rows = useMemo(() => sourceChecklist(plan.status === "ready" ? plan.data.data : null, lectureIds), [plan, lectureIds]);
  const missing = plan.status === "ready" ? missingTranscripts(rows) : [];

  const watchContext = useCallback(
    (lectureId: string) => ({
      rpc,
      actions,
      t,
      course: courseCode,
      lessons,
      count: numbers.length,
      lectureId,
      assistant,
      isShown: () => false,
    }),
    [rpc, actions, t, courseCode, lessons, numbers.length, assistant],
  );

  const start = useCallback(
    async (params: Parameters<typeof actions.jobs.runNotes>[0]) => {
      setBusy(true);
      try {
        const id = await actions.jobs.runNotes(params);
        watchNotesJob(id, { ...watchContext(lectureIds[0] ?? ""), isShown: () => shown.current.open && shown.current.jobId === id });
        setJobId(id);
      } catch {
        actions.ui.toast({ message: g.startError, tone: "danger" });
      } finally {
        setBusy(false);
      }
    },
    [actions, watchContext, lectureIds, g.startError],
  );

  const onGenerate = () => {
    if (options.contextLessons.trim() && normalizeLessonList(options.contextLessons) === null) {
      setContextError(true);
      return;
    }
    void start(runParams(courseCode, numbers, options));
  };

  const onReviewChange = async (next: boolean) => {
    if (!course) return;
    setReview(next);
    try {
      await rpc.call("course.edit", { course: withPublicationMode(course, next) });
      await actions.config.refresh();
    } catch {
      setReview(!next);
      actions.ui.toast({ message: g.reviewFirstError, tone: "danger" });
    }
  };

  const close = () => {
    if (view.view === "running") actions.ui.toast({ message: g.minimized, tone: "neutral", action: { label: g.openActivity, run: () => actions.ui.navigate({ name: "activity" }) } });
    onClose();
  };

  const stop = async () => {
    if (!jobId) return;
    try {
      await actions.jobs.cancel(jobId);
    } catch {
      actions.ui.toast({ message: g.stopError, tone: "danger" });
    }
  };

  const title = g.title(lessons, courseName);

  async function openFile(path: string) {
    try {
      const result = await openPath(path);
      if (result === "preview") actions.ui.toast({ message: t.lesson.outcome.opening(path.split(/[\\/]/).pop() ?? path), tone: "neutral" });
    } catch {
      actions.ui.toast({ message: t.lesson.outcome.openError, tone: "danger" });
    }
  }

  const needsTranscript: Outcome = { kind: "needsTranscript", status: "NEEDS_TRANSCRIPT", tone: "warning", fix: "transcribe", secondary: null, resumable: false };
  const outcomeArgs: OutcomeArgs | null =
    view.view === "outcome" || (view.view === "setup" && missing.length > 0)
      ? {
          outcome: view.view === "outcome" ? view.outcome : needsTranscript,
          job: view.view === "outcome" ? view.job : null,
          history,
          courseCode,
          courseName,
          lessons,
          count: numbers.length,
          missingLectures: view.view === "outcome" ? (view.job.lectureIds ?? []) : missing.map((row) => row.lectureId),
          assistant,
          now,
          numbers,
          options,
          onClose: close,
          onRun: start,
          openFile: (path) => void openFile(path),
        }
      : null;
  const outcomeParts = useOutcomeParts(outcomeArgs);

  let body: ReactNode;
  let footer: ReactNode;
  if (outcomeParts) {
    body = outcomeParts.body;
    footer = outcomeParts.footer;
  } else if (view.view === "running") {
    const timeline = job ? stageTimeline(job, history, now) : { current: NOTES_STAGES[0], timings: {}, totalSeconds: 0 };
    body = <RunningView current={timeline.current} timings={timeline.timings} totalSeconds={timeline.totalSeconds} now={now} />;
    footer = (
      <>
        <Button variant="ghost" icon={CircleStop} disabled={starting} onClick={() => void stop()}>
          {g.stop}
        </Button>
        <Button variant="primary" onClick={close}>
          {g.minimize}
        </Button>
      </>
    );
  } else {
    body = (
      <SetupView
        rows={rows}
        planStatus={plan.status}
        onRetryPlan={plan.reload}
        options={options}
        setOptions={(next) => {
          setOptions(next);
          if (contextError) setContextError(false);
        }}
        contextError={contextError}
        review={review}
        onReviewChange={(next) => void onReviewChange(next)}
        reviewDisabled={!course}
        courseCode={courseCode}
      />
    );
    footer = (
      <>
        <p className={styles.disclosureLine}>
          <Icon icon={Send} size={16} />
          <span>{g.provider(assistant)}</span>
        </p>
        <Button variant="ghost" onClick={close}>
          {t.common.cancel}
        </Button>
        <Button variant="primary" loading={busy} disabled={plan.status !== "ready"} onClick={onGenerate}>
          {g.start}
        </Button>
      </>
    );
  }

  return (
    <Sheet open={open} onClose={close} title={title} footer={footer}>
      {body}
    </Sheet>
  );
}

// ---------------------------------------------------------------------------------------------

interface SetupViewProps {
  rows: readonly SourceRow[];
  planStatus: "loading" | "ready" | "error";
  onRetryPlan: () => void;
  options: GenerateOptions;
  setOptions: (options: GenerateOptions) => void;
  contextError: boolean;
  review: boolean;
  onReviewChange: (review: boolean) => void;
  reviewDisabled: boolean;
  courseCode: string;
}

function SetupView({ rows, planStatus, onRetryPlan, options, setOptions, contextError, review, onReviewChange, reviewDisabled, courseCode }: SetupViewProps) {
  const { t } = useI18n();
  const g = t.lesson.generate;
  const actions = useActions();
  const sourcesId = useId();
  const many = rows.length > 1;
  const addSlides = () => actions.ui.navigate({ name: "course", code: courseCode, tab: "files" });

  return (
    <div className={styles.stack}>
      <section className={styles.group} aria-labelledby={sourcesId} aria-busy={planStatus === "loading" || undefined}>
        <h3 id={sourcesId} className={styles.groupTitle}>
          {g.sources}
        </h3>
        {planStatus === "error" ? (
          <Banner tone="danger" action={{ label: t.common.retry, onSelect: onRetryPlan }}>
            {g.planError}
          </Banner>
        ) : planStatus === "loading" ? (
          <p className={styles.muted}>{g.checkingSources}</p>
        ) : (
          rows.map((row) => (
            <div key={row.lectureId} className={styles.sourceBlock}>
              {many && row.lessonNumber !== null && <p className={styles.sourceLesson}>{t.common.lesson(row.lessonNumber)}</p>}
              <ul role="list" className={styles.sources}>
                <SourceItem ok={row.transcript} label={g.transcript} value={row.transcript ? null : g.missingTranscript} />
                <SourceItem
                  ok={row.slides.length > 0}
                  label={g.slides}
                  value={row.slides.length > 0 ? row.slides.join(", ") : g.noneFound}
                  action={row.slides.length === 0 ? { label: g.addSlides, onSelect: addSlides } : undefined}
                />
                <SourceItem ok={row.notebookSection !== null} label={g.notebook} value={row.notebookSection ?? "—"} optional />
              </ul>
            </div>
          ))
        )}
      </section>

      <ChoiceList
        label={g.quality}
        value={options.quality}
        onChange={(quality) => setOptions({ ...options, quality })}
        choices={[
          { value: "standard", label: g.standard, description: g.standardHint },
          { value: "approfondito", label: g.thorough, description: g.thoroughHint },
          { value: "revisione", label: g.fullReview, description: g.fullReviewHint },
        ]}
      />

      <Toggle label={g.reviewFirst} description={g.reviewFirstHint} checked={review} disabled={reviewDisabled} onChange={onReviewChange} />

      <Disclosure label={g.more} summary={g.moreSummary}>
        <TextArea label={g.instructions} hint={g.instructionsHint} optional rows={3} value={options.instructions} onChange={(event) => setOptions({ ...options, instructions: event.target.value })} />
        <Stepper
          label={g.imageLimit}
          value={options.imageLimit ?? 4}
          min={0}
          max={12}
          onChange={(imageLimit) => setOptions({ ...options, imageLimit })}
          valueText={g.imageLimitText}
        />
        <Toggle label={g.focusLecturer} description={g.focusLecturerHint} checked={options.focusLecturer} onChange={(focusLecturer) => setOptions({ ...options, focusLecturer })} />
        <TextField
          label={g.contextLessons}
          hint={g.contextLessonsHint}
          optional
          inputMode="numeric"
          value={options.contextLessons}
          error={contextError ? g.contextLessonsError : undefined}
          onChange={(event) => setOptions({ ...options, contextLessons: event.target.value })}
        />
      </Disclosure>
    </div>
  );
}

function SourceItem({ ok, label, value, action, optional = false }: { ok: boolean; label: string; value: string | null; action?: { label: string; onSelect: () => void }; optional?: boolean }) {
  return (
    <li className={styles.source}>
      <span className={cx(styles.sourceMark, ok ? styles.sourceOk : optional ? styles.sourceNone : styles.sourceMissing)} aria-hidden="true">
        {ok ? <Icon icon={Check} size={14} /> : optional ? null : <Icon icon={CircleAlert} size={14} />}
      </span>
      <span className={styles.sourceText}>
        <span className={styles.sourceLabel}>{label}</span>
        {value && <span className={styles.sourceValue}>{value}</span>}
      </span>
      {action && (
        <Button variant="ghost" size="sm" onClick={action.onSelect}>
          {action.label}
        </Button>
      )}
    </li>
  );
}

// ---------------------------------------------------------------------------------------------

function useStages(): Stage[] {
  const { t } = useI18n();
  const labels = t.lesson.generate.stages;
  return [...NOTES_STAGES.map((id) => ({ id, label: labels[id] })), { id: DONE_STAGE, label: labels.DONE }];
}

function RunningView({ current, timings, totalSeconds, now }: { current: string; timings: Record<string, { startedAt: string; finishedAt: string | null }>; totalSeconds: number; now: Date }) {
  const { t, format } = useI18n();
  const g = t.lesson.generate;
  const stages = useStages();
  return (
    <div className={styles.stack}>
      <div className={styles.runHead}>
        <p className={styles.elapsed} role="timer" aria-live="off">
          {g.elapsed(format.clock(totalSeconds))}
        </p>
        <p className={styles.muted}>{g.runningBody}</p>
      </div>
      {/* The stage label is announced politely when it changes; the clock is not. */}
      <p className="visually-hidden" aria-live="polite">
        {stages.find((stage) => stage.id === current)?.label ?? ""}
      </p>
      <StageStepper label={g.stagesLabel} stages={stages} current={current} timings={timings} now={now} />
    </div>
  );
}

// ---------------------------------------------------------------------------------------------

interface OutcomeArgs {
  outcome: Outcome;
  job: Job | null;
  history: readonly StageTiming[];
  courseCode: string;
  courseName: string;
  lessons: string;
  count: number;
  missingLectures: readonly string[];
  assistant: string;
  now: Date;
  numbers: readonly number[];
  options: GenerateOptions;
  onClose: () => void;
  onRun: (params: ReturnType<typeof runParams> & { resume?: string }) => Promise<void>;
  openFile: (path: string) => void;
}

const toneIcon: Record<Outcome["tone"], LucideIcon> = { success: CircleCheck, warning: TriangleAlert, danger: CircleAlert };

/** Body and footer of the outcome face; a hook so the Sheet stays mounted across faces. */
function useOutcomeParts(args: OutcomeArgs | null): { body: ReactNode; footer: ReactNode } | null {
  const { t, format } = useI18n();
  const o = t.lesson.outcome;
  const actions = useActions();
  const rpc = useRpc();
  const config = useAppState((state) => state.config.config);
  const stages = useStages();
  const [listOpen, setListOpen] = useState(false);
  const [previewed, setPreviewed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [whenReady, setWhenReady] = useState(true);
  const [transcribing, setTranscribing] = useState(false);
  const resetKey = args ? `${args.job?.jobId ?? "plan"}:${args.outcome.kind}` : "";
  useEffect(() => {
    setListOpen(false);
    setPreviewed(false);
    setTranscribing(false);
  }, [resetKey]);
  if (!args) return null;
  const { outcome, job, history, courseCode, courseName, lessons, count, missingLectures, assistant, now, numbers, options, onClose, onRun, openFile } = args;
  const files = courseFiles(courseRecord(config, courseCode));
  const warnings = warningsOf(job?.result ?? null);

  const open = (path: string | null) => {
    if (!path) {
      actions.ui.toast({ message: t.courses.notes.noFile, tone: "warning" });
      return;
    }
    openFile(path);
  };

  const candidate = async (): Promise<string | null> => {
    if (!job?.batchId) return null;
    try {
      const { data } = await rpc.call("notes.batchStatus", { batchId: job.batchId });
      return data.lessons.find((lesson) => lesson.word_candidate)?.word_candidate ?? null;
    } catch {
      return null;
    }
  };

  const ask = (text: string) => {
    actions.assistant.prefill(text);
    actions.ui.setAssistantOpen(true);
    onClose();
  };

  const run = async (fix: Outcome["fix"] | "approve" | "copyDetails" | "resume" | "undo") => {
    switch (fix) {
      case "undo":
        undoLastUpdate({ rpc, actions, t, course: courseCode });
        return;
      case "openWord":
        open(files.handout);
        return;
      case "viewWarnings":
        setListOpen((value) => !value);
        return;
      case "resume":
        if (job?.batchId) await onRun({ ...runParams(courseCode, numbers, options), resume: job.batchId });
        return;
      case "signIn":
        actions.ui.navigate({ name: "settings", section: "assistant" });
        onClose();
        return;
      case "openPreview": {
        const path = await candidate();
        open(path ?? files.handout);
        setPreviewed(true);
        return;
      }
      case "approve": {
        setBusy(true);
        try {
          const path = await candidate();
          for (const lectureId of job?.lectureIds ?? []) {
            await rpc.call("notes.publish", path ? { lectureId, candidate: path } : { lectureId });
          }
          // The job record keeps AWAITING_APPROVAL; publishing is what completes it.
          if (job) actions.jobs.finished({ jobId: job.jobId, state: "succeeded", exitCode: 0, result: { status: "COMPLETED" }, at: new Date().toISOString() });
          completedToast({ rpc, actions, t, course: courseCode, lessons, count });
        } catch {
          actions.ui.toast({ message: o.approveError, tone: "danger" });
        } finally {
          setBusy(false);
        }
        return;
      }
      case "askAssistant":
        ask(outcome.kind === "conflict" ? o.mergePrompt(lessons, courseName) : o.reviewPrompt(lessons, courseName));
        return;
      case "copyDetails": {
        const copied = job ? await copyText(jobDetails(job)) : false;
        actions.ui.toast({ message: copied ? o.copied : o.copyError, tone: copied ? "neutral" : "danger" });
        return;
      }
      case "transcribe": {
        setBusy(true);
        try {
          const ids: string[] = [];
          for (const lectureId of missingLectures) {
            const { jobId } = await rpc.call("transcription.run", { lectureId });
            ids.push(jobId);
          }
          setTranscribing(true);
          actions.ui.toast({ message: t.lesson.states.started, tone: "neutral" });
          if (whenReady) {
            afterTranscriptions(rpc, ids, () => void onRun(runParams(courseCode, numbers, options)), () => actions.ui.toast({ message: t.lesson.states.startError, tone: "danger" }));
          }
        } catch {
          actions.ui.toast({ message: t.lesson.states.startError, tone: "danger" });
        } finally {
          setBusy(false);
        }
        return;
      }
    }
  };

  let headline: string;
  let detail: string | null = null;
  switch (outcome.kind) {
    case "completed":
      headline = o.completed(lessons, count);
      break;
    case "warnings":
      headline = o.warnings(warnings.length || 1);
      break;
    case "needsTranscript":
      headline = o.needsTranscript(lessons, count);
      break;
    case "pausedQuota":
      headline = o.pausedQuota(assistant);
      detail = o.resumeHint;
      break;
    case "pausedAuth":
      headline = o.pausedAuth(assistant);
      detail = o.pausedAuthBody;
      break;
    case "pausedNetwork":
      headline = o.pausedNetwork;
      detail = o.resumeHint;
      break;
    case "pausedSleep":
      headline = o.pausedSleep;
      detail = o.resumeHint;
      break;
    case "awaitingApproval":
      headline = o.awaitingApproval;
      break;
    case "conflict":
      headline = o.conflict(lessons, count);
      break;
    case "needsReview":
      headline = o.needsReview;
      break;
    case "cancelled":
      headline = o.cancelled;
      detail = outcome.resumable ? o.resumeHint : null;
      break;
  }

  const fixLabel: Record<Outcome["fix"], string> = {
    openWord: o.openInWord,
    viewWarnings: listOpen ? o.hideList : o.viewList,
    transcribe: o.transcribeNow,
    resume: o.resume,
    signIn: o.signIn,
    openPreview: previewed ? o.approve : o.openPreview,
    askAssistant: outcome.kind === "conflict" ? o.askToMerge : o.askAssistant,
  };
  // After the preview, approving becomes the one primary action and the preview steps back.
  const primary: Outcome["fix"] | "approve" = outcome.fix === "openPreview" && previewed ? "approve" : outcome.fix;
  const secondary = outcome.fix === "openPreview" ? (previewed ? "openPreview" : null) : outcome.secondary;
  const secondaryLabel =
    secondary === "openPreview" ? o.openPreviewAgain : secondary === "copyDetails" ? o.copyDetails : secondary === "resume" ? o.resume : secondary === "approve" ? o.approve : secondary === "undo" ? o.undo : null;
  const canResume = outcome.fix !== "resume" || outcome.resumable;
  const timeline = job && (outcome.kind === "completed" || outcome.kind === "warnings") ? stageTimeline(job, history, now) : null;

  const footer = (
    <>
      <Button variant="ghost" onClick={onClose}>
        {t.common.close}
      </Button>
      {secondary && secondaryLabel && (secondary !== "resume" || outcome.resumable) && (
        <Button variant="soft" icon={secondary === "undo" ? Undo2 : undefined} onClick={() => void run(secondary)}>
          {secondaryLabel}
        </Button>
      )}
      {canResume && !(outcome.kind === "needsTranscript" && transcribing) && (
        <Button variant="primary" loading={busy} onClick={() => void run(primary)}>
          {primary === "approve" ? o.approve : fixLabel[outcome.fix]}
        </Button>
      )}
    </>
  );

  const body = (
    <div className={styles.stack}>
      <div className={cx(styles.outcome, styles[outcome.tone])} role={outcome.tone === "danger" ? "alert" : "status"}>
        <span className={styles.outcomeBadge} aria-hidden="true">
          <Icon icon={toneIcon[outcome.tone]} size={20} />
        </span>
        <div className={styles.outcomeText}>
          <p className={styles.outcomeHeadline}>{headline}</p>
          {detail && <p className={styles.outcomeDetail}>{detail}</p>}
          {outcome.kind === "needsTranscript" && transcribing && <p className={styles.outcomeDetail}>{o.transcribing}</p>}
        </div>
      </div>
      {outcome.kind === "warnings" && listOpen && (
        <ul className={styles.warnings}>
          {(warnings.length ? warnings : [o.needsReview]).map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
      {outcome.kind === "needsTranscript" && !transcribing && (
        <Toggle label={o.generateWhenReady} description={o.generateWhenReadyHint} checked={whenReady} onChange={setWhenReady} />
      )}
      {timeline && (
        <div className={styles.doneSteps}>
          <p className={styles.muted}>{t.lesson.generate.took(format.clock(timeline.totalSeconds))}</p>
          <StageStepper label={t.lesson.generate.stagesLabel} stages={stages} current="done" timings={timeline.timings} now={now} />
        </div>
      )}
    </div>
  );
  return { body, footer };
}
