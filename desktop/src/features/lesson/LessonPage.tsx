import { AudioLines, CircleAlert, Clock, LibraryBig } from "lucide-react";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState, type RefObject } from "react";

import { useNow } from "../../app/useNow";
import { Banner, Button, Card, ChoiceList, DropZone, EmptyState, Icon, PageHeader, StatusChip } from "../../components";
import { useI18n } from "../../i18n";
import type { Job, LibraryRecording } from "../../rpc";
import { routeToHash, shallowEqual, useActions, useAppState, useRpc, type AppState } from "../../state";
import type { Route } from "../../state/routes";
import { courseFiles, courseRecord, lessonAttachments, notesSummary } from "../courses/model";
import { AudioPlayer } from "./AudioPlayer";
import { GenerateSheet } from "./generate/GenerateSheet";
import { LessonActions } from "./LessonActions";
import { LessonRail, type Materials } from "./LessonRail";
import { deriveLesson, initialPlayer, keyBelongsToControl, parseTranscript, playerKey, playerReducer, type LessonDetail } from "./model";
import { openPath, revealPath } from "./platform";
import { TranscriptPanel, type TranscriptHandle } from "./TranscriptPanel";
import { useRpcData } from "./useRpcData";
import styles from "./Lesson.module.css";

const selectInput = (state: AppState) => ({
  library: state.library.library,
  libraryLoad: state.library.load,
  queue: state.queue.status,
  dashboard: state.capture.dashboard,
  config: state.config.config,
  jobsById: state.jobs.byId,
  jobOrder: state.jobs.order,
  stages: state.jobs.stages,
});

const EMPTY: never[] = [];

/**
 * Lesson detail (UX-SPEC §3): header with chips and the ⋯ menu, audio player, searchable
 * transcript with timestamps that seek, and the right rail with the one primary action.
 */
export function LessonPage({ route }: { route: Extract<Route, { name: "lesson" }> }) {
  const { t, format } = useI18n();
  const actions = useActions();
  const rpc = useRpc();
  const now = useNow(1000);
  const input = useAppState(selectInput, shallowEqual);
  const jobs = useMemo(() => input.jobOrder.map((id) => input.jobsById[id]).filter((job): job is Job => job !== undefined), [input.jobOrder, input.jobsById]);
  const history = useRpcData("notes.history", {}, "all");
  const backups = history.status === "ready" ? history.data.data : EMPTY;

  const base = useMemo(() => deriveLesson(route.id, { ...input, jobs }), [route.id, input, jobs]);
  const inNotes = base?.courseCode && base.number !== null ? notesSummary(base.courseCode, backups, jobs).lessons.includes(base.number) : false;
  const lesson = useMemo(() => (inNotes ? deriveLesson(route.id, { ...input, jobs }, true) : base), [inNotes, base, route.id, input, jobs]);

  const [sheet, setSheet] = useState<{ open: boolean; jobId: string | null }>({ open: false, jobId: null });
  const [player, dispatch] = useReducer(playerReducer, initialPlayer(lesson?.recording?.duration ?? 0));
  useEffect(() => dispatch({ type: "duration", duration: lesson?.recording?.duration ?? 0 }), [lesson?.recording?.duration]);
  const transcriptRef = useRef<TranscriptHandle>(null);

  const transcript = useRpcData("library.transcript", lesson?.state === "ready" ? { lectureId: route.id } : null, `${route.id}:${lesson?.transcriptId ?? ""}`);
  const plan = useRpcData(
    "notes.plan",
    lesson?.state === "ready" && lesson.courseCode && lesson.number !== null ? { course: lesson.courseCode, lessons: String(lesson.number) } : null,
    `${route.id}:${lesson?.state ?? ""}`,
  );

  const course = courseRecord(input.config, lesson?.courseCode ?? "");
  const materials: Materials = useMemo(() => {
    const planned = plan.status === "ready" ? plan.data.data.lessons?.find((entry) => entry.lecture_id === route.id) : undefined;
    const pinned = lessonAttachments(course).filter((item) => item.lectureId === route.id).map((item) => item.path);
    const slides = [...new Set([...pinned, ...(planned?.slides ?? [])].map((path) => path.split(/[\\/]/).pop() ?? path))];
    return { slides, notebookSection: planned?.notebook_section ?? null };
  }, [plan, course, route.id]);

  // Space / ← / → drive the player, ⌘F / Ctrl+F searches the transcript (UX-SPEC §3).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (document.querySelector("dialog[open]")) return;
      const mod = event.metaKey || event.ctrlKey;
      if (mod && event.key.toLowerCase() === "f" && !event.altKey && !event.shiftKey) {
        if (!transcriptRef.current) return;
        event.preventDefault();
        transcriptRef.current.focusSearch();
        return;
      }
      if (mod || event.altKey || keyBelongsToControl(event.target, event.key)) return;
      const action = playerKey(event.key);
      if (!action || !lesson?.recording) return;
      event.preventDefault();
      dispatch(action);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lesson?.recording]);

  const openFile = useCallback(
    async (path: string | null, reveal = false) => {
      if (!path) {
        actions.ui.toast({ message: t.courses.notes.noFile, tone: "warning" });
        return;
      }
      try {
        const result = reveal ? await revealPath(path) : await openPath(path);
        if (result === "preview") actions.ui.toast({ message: t.lesson.outcome.opening(path.split(/[\\/]/).pop() ?? path), tone: "neutral" });
      } catch {
        actions.ui.toast({ message: t.lesson.outcome.openError, tone: "danger" });
      }
    },
    [actions, t],
  );

  const transcribe = async (recording?: LibraryRecording) => {
    try {
      if (recording) await rpc.call("library.preferAudio", { recordingId: recording.id });
      await rpc.call("transcription.run", { lectureId: route.id, ...(recording ? { recordingId: recording.id } : {}) });
      actions.ui.toast({ message: t.lesson.states.started, tone: "neutral" });
    } catch {
      actions.ui.toast({ message: t.lesson.states.startError, tone: "danger" });
    }
  };

  const useForTranscription = async (recording: LibraryRecording) => {
    try {
      await rpc.call("library.preferAudio", { recordingId: recording.id });
      await actions.library.refresh();
      actions.ui.toast({ message: t.lesson.player.switched, tone: "neutral" });
    } catch {
      actions.ui.toast({ message: t.lesson.player.switchError, tone: "danger" });
    }
  };

  if (!lesson) {
    const loading = input.libraryLoad.status === "loading" || input.libraryLoad.status === "idle";
    return (
      <>
        <PageHeader title={t.lesson.title} />
        {input.libraryLoad.status === "error" ? (
          <Banner tone="danger" action={{ label: t.common.retry, onSelect: () => void actions.library.refresh() }}>
            {t.lesson.loadError}
          </Banner>
        ) : loading ? (
          <div className={styles.skeleton} aria-busy="true" aria-label={t.common.loading}>
            <div className={styles.skeletonBlock} />
          </div>
        ) : (
          <EmptyState
            icon={LibraryBig}
            title={t.lesson.notFound}
            action={
              <Button variant="soft" onClick={() => actions.ui.navigate({ name: "library", course: null, q: "", unassigned: false })}>
                {t.lesson.notFoundAction}
              </Button>
            }
          />
        )}
      </>
    );
  }

  const title = lesson.number !== null ? t.common.lesson(lesson.number) : lesson.courseName;
  const date = lesson.date ? new Date(`${lesson.date}T12:00:00`) : null;
  const files = courseFiles(course);
  const showJob = (job: Job) => setSheet({ open: true, jobId: job.jobId });

  return (
    <>
      <PageHeader
        title={title}
        subtitle={
          <span className={styles.subtitle}>
            {lesson.courseCode ? (
              <a className={styles.courseLink} href={routeToHash({ name: "course", code: lesson.courseCode, tab: "lessons" })}>
                {lesson.courseName}
              </a>
            ) : (
              <span>{lesson.courseName}</span>
            )}
            {date && (
              <>
                <span aria-hidden="true">·</span>
                <span>{format.dayLong(date)}</span>
              </>
            )}
          </span>
        }
        actions={<LessonActions lesson={lesson} />}
      />
      <ul role="list" className={styles.chips} aria-label={t.lesson.statusLabel}>
        <li>
          <StatusChip kind={lesson.chips.recording.kind} status={lesson.chips.recording.status} />
        </li>
        <li>
          <StatusChip kind={lesson.chips.transcript.kind} status={lesson.chips.transcript.status} />
        </li>
        <li>
          <StatusChip kind={lesson.chips.notes.kind} status={lesson.chips.notes.status} />
        </li>
      </ul>

      <div className={styles.layout}>
        <div className={styles.main}>
          {lesson.recording && (
            <AudioPlayer
              recording={lesson.recording}
              alternatives={lesson.state === "chooseSource" ? [] : lesson.recordings.filter((recording) => recording.id !== lesson.recording?.id)}
              state={player}
              dispatch={dispatch}
              onUseForTranscription={(recording) => void useForTranscription(recording)}
            />
          )}
          <MainState
            lesson={lesson}
            now={now}
            transcriptRef={transcriptRef}
            transcript={transcript}
            position={player.position}
            onSeek={(to) => dispatch({ type: "seek", to })}
            onTranscribe={(recording) => void transcribe(recording)}
            onOpenFull={lesson.transcriptPath ? () => void openFile(lesson.transcriptPath) : null}
          />
        </div>
        <LessonRail
          lesson={lesson}
          history={lesson.notesJob ? (input.stages[lesson.notesJob.jobId] ?? EMPTY) : EMPTY}
          materials={materials}
          now={now}
          onGenerate={() => setSheet({ open: true, jobId: null })}
          onShowJob={showJob}
          onOpenWord={() => void openFile(files.handout)}
        />
      </div>

      {lesson.courseCode && (
        <GenerateSheet
          open={sheet.open}
          onClose={() => setSheet((value) => ({ ...value, open: false }))}
          courseCode={lesson.courseCode}
          lectureIds={[lesson.id]}
          initialJobId={sheet.jobId}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------------------------

interface MainStateProps {
  lesson: LessonDetail;
  now: Date;
  transcriptRef: RefObject<TranscriptHandle | null>;
  transcript: ReturnType<typeof useRpcData<"library.transcript">>;
  position: number;
  onSeek: (seconds: number) => void;
  onTranscribe: (recording?: LibraryRecording) => void;
  onOpenFull: (() => void) | null;
}

function MainState({ lesson, now, transcriptRef, transcript, position, onSeek, onTranscribe, onOpenFull }: MainStateProps) {
  const { t, format } = useI18n();
  const text = transcript.status === "ready" ? transcript.data.data.text : null;
  const segments = useMemo(() => (text === null ? [] : parseTranscript(text)), [text]);
  const s = t.lesson.states;
  const actions = useActions();
  const firstSeen = useRef<{ id: string; at: number } | null>(null);
  const [choice, setChoice] = useState(lesson.recording?.id ?? "");
  useEffect(() => setChoice(lesson.recording?.id ?? ""), [lesson.recording?.id]);

  switch (lesson.state) {
    case "ready":
      return (
        <TranscriptPanel
          ref={transcriptRef}
          status={transcript.status}
          segments={segments}
          truncated={transcript.status === "ready" && transcript.data.data.truncated}
          position={position}
          onSeek={onSeek}
          onRetry={transcript.reload}
          onOpenFull={onOpenFull}
        />
      );
    case "noAudio":
      return <DropZone title={s.noAudioTitle} body={s.noAudioBody} accept="audio/*,.txt" multiple={false} onFiles={() => actions.ui.toast({ message: t.lesson.link.desktopOnly, tone: "neutral" })} />;
    case "transcribing": {
      const queued = lesson.transcription && (lesson.transcription.status === "QUEUED" || lesson.transcription.status === "WAITING") && !lesson.transcriptionJob;
      const key = lesson.transcription?.id ?? lesson.transcriptionJob?.jobId ?? lesson.id;
      if (firstSeen.current?.id !== key) firstSeen.current = { id: key, at: now.getTime() };
      const base = lesson.transcriptionJob ? (now.getTime() - Date.parse(lesson.transcriptionJob.startedAt)) / 1000 : (lesson.transcription?.elapsed_seconds ?? 0) + (now.getTime() - firstSeen.current.at) / 1000;
      return (
        <Card as="section" aria-label={queued ? s.queuedTitle : s.transcribingTitle} className={styles.stateCard}>
          <span className={styles.stateIcon} aria-hidden="true">
            <Icon icon={queued ? Clock : AudioLines} size={20} />
          </span>
          <div className={styles.stateText}>
            <p className={styles.stateTitle}>{queued ? s.queuedTitle : s.transcribingTitle}</p>
            <p className={styles.muted}>{queued ? s.queuedBody : s.transcribingBody}</p>
          </div>
          {!queued && (
            <div className={styles.stateTimer}>
              <span className={styles.timerValue} role="timer" aria-label={`${s.elapsed} ${format.clock(base)}`}>
                {format.clock(base)}
              </span>
              <span className={styles.timerLabel} aria-hidden="true">
                {s.elapsed}
              </span>
            </div>
          )}
        </Card>
      );
    }
    case "chooseSource":
      return (
        <Card as="section" aria-label={s.chooseTitle} className={styles.chooseCard}>
          <ChoiceList
            label={s.chooseTitle}
            value={choice}
            onChange={setChoice}
            choices={lesson.recordings.map((recording) => ({
              value: recording.id,
              label: recording.title,
              description: recording.recorded_at ? `${format.dayShort(new Date(recording.recorded_at))} · ${format.time(new Date(recording.recorded_at))} · ${t.lesson.rail.origin(recording.origin ?? "")}` : undefined,
              meta: format.clock(recording.duration ?? 0),
            }))}
          />
          <p className={styles.muted}>{s.chooseBody}</p>
          <div className={styles.stateActions}>
            <Button variant="primary" onClick={() => onTranscribe(lesson.recordings.find((recording) => recording.id === choice))}>
              {s.transcribeThis}
            </Button>
          </div>
        </Card>
      );
    case "transcriptProblem":
    case "notTranscribed": {
      const problem = lesson.state === "transcriptProblem";
      return (
        <Card as="section" aria-label={problem ? s.problemTitle : s.notTranscribedTitle} className={styles.stateCard}>
          <span className={problem ? styles.stateIconWarning : styles.stateIcon} aria-hidden="true">
            <Icon icon={problem ? CircleAlert : AudioLines} size={20} />
          </span>
          <div className={styles.stateText}>
            <p className={styles.stateTitle}>{problem ? s.problemTitle : s.notTranscribedTitle}</p>
            <p className={styles.muted}>{problem ? s.problemBody : s.notTranscribedBody}</p>
          </div>
          <Button variant="primary" onClick={() => onTranscribe()}>
            {problem ? s.transcribeAgain : s.transcribeNow}
          </Button>
        </Card>
      );
    }
  }
}
