/**
 * Activity view-model: the transcription queue and notes jobs, sorted into the sections of
 * UX-SPEC §3 Activity. Pure; unit-tested in model.test.ts. Section rules follow the Swift
 * `WorkspaceActivity`: running before queued (queue order), ready and to-check oldest first,
 * hidden lessons never appear, transcribed recordings leave the list.
 */
import type { ChipKind } from "../../components/StatusChip";
import type { Job, Library, LibraryRecording, QueueJob, QueueJobAction, QueueStatus } from "../../rpc";
import { notesStatusOf, parseLectureId } from "../today/model";

export type RowAction = QueueJobAction | "finish";

export interface ActivityLesson {
  courseCode: string | null;
  courseName: string | null;
  lessonNumber: number | null;
  /** Fallback when no course is known: the recording's own title. */
  title: string;
  lectureId: string | null;
}

export type CheckReason = "failed" | "interrupted" | "review" | "outdated" | "waiting" | "archive";

export interface QueueRow {
  id: string;
  jobId: string | null;
  recordingId: string;
  lesson: ActivityLesson;
  chip: { kind: ChipKind; status: string };
  elapsedSeconds: number | null;
  queuePosition: number | null;
  reason: CheckReason | null;
  actions: RowAction[];
  createdAt: string | null;
}

export interface NotesRow {
  jobId: string;
  lessons: ActivityLesson[];
  status: string;
  running: boolean;
  startedAt: string;
  finishedAt: string | null;
}

export interface ActivityModel {
  ready: boolean;
  paused: boolean;
  parallel: number;
  inProgress: QueueRow[];
  readyToTranscribe: QueueRow[];
  toCheck: QueueRow[];
  history: QueueRow[];
  notes: NotesRow[];
  /** Jobs waiting for a free slot (QUEUED). */
  waiting: number;
  /** Jobs that keep running while paused (RUNNING, CANCELLING). */
  finishing: number;
  /** Notes jobs that need the student (paused, to review, conflicts). */
  notesNeedingYou: number;
}

export interface ActivityInput {
  queue: QueueStatus | null;
  library: Library | null;
  jobs: readonly Job[];
  courses: ReadonlyArray<{ code: string; name: string }>;
}

const ACTIVE = new Set(["RUNNING", "CANCELLING", "QUEUED"]);
const PROBLEMS: Record<string, CheckReason> = {
  FAILED: "failed",
  INTERRUPTED: "interrupted",
  NEEDS_REVIEW: "review",
  OUTDATED: "outdated",
  WAITING: "waiting",
};
const DONE = new Set(["COMPLETED", "CANCELLED"]);
const KNOWN_ACTIONS: ReadonlySet<string> = new Set<RowAction>(["retry", "restart", "recover", "remove", "cancel", "finish"]);
const NOTES_NEED_YOU = new Set([
  "AWAITING_APPROVAL",
  "PAUSED_QUOTA",
  "PAUSED_AUTH",
  "PAUSED_NETWORK",
  "PAUSED_SLEEP",
  "PERSONAL_CONTENT_CONFLICT",
  "NEEDS_REVIEW",
  "STOPPED",
  "NEEDS_TRANSCRIPT",
  "COMPLETED_WITH_WARNINGS",
]);

function isRowAction(value: string): value is RowAction {
  return KNOWN_ACTIONS.has(value);
}

function oldestFirst(a: QueueRow, b: QueueRow): number {
  return (a.createdAt ?? "").localeCompare(b.createdAt ?? "") || a.id.localeCompare(b.id);
}

export function deriveActivity({ queue, library, jobs, courses }: ActivityInput): ActivityModel {
  const names = new Map(courses.map((course) => [course.code, course.name]));
  const recordings = new Map((library?.recordings ?? []).map((recording) => [recording.id, recording]));
  const hiddenRecordings = new Set<string>();
  for (const lesson of library?.lessons ?? []) if (lesson.hidden) for (const id of lesson.recording_ids) hiddenRecordings.add(id);

  const lessonOf = (lectureId: string | null | undefined, courseCode: string | null | undefined, title: string): ActivityLesson => {
    const parsed = lectureId ? parseLectureId(lectureId) : parseLectureId(title);
    const code = parsed?.courseCode ?? courseCode ?? null;
    return {
      courseCode: code,
      courseName: code ? (names.get(code) ?? code) : null,
      lessonNumber: parsed?.lessonNumber ?? null,
      title,
      lectureId: parsed ? (lectureId ?? title) : null,
    };
  };
  const lessonForRecording = (recording: LibraryRecording | undefined, job?: QueueJob): ActivityLesson =>
    lessonOf(recording?.lecture_id ?? null, recording?.course_code ?? job?.course_code ?? null, recording?.title ?? job?.title ?? "");

  const inProgress: QueueRow[] = [];
  const toCheck: QueueRow[] = [];
  const history: QueueRow[] = [];
  const inQueue = new Set<string>();

  for (const job of queue?.jobs ?? []) {
    const recording = recordings.get(job.recording_id);
    const actions = (job.actions ?? []).filter(isRowAction);
    const row: QueueRow = {
      id: `job:${job.id}`,
      jobId: job.id,
      recordingId: job.recording_id,
      lesson: lessonForRecording(recording, job),
      chip: { kind: "transcription", status: job.status },
      elapsedSeconds: job.elapsed_seconds ?? null,
      queuePosition: job.queue_position ?? null,
      reason: PROBLEMS[job.status] ?? null,
      actions: job.status === "QUEUED" && actions.length === 0 ? ["remove"] : actions,
      createdAt: job.created_at ?? null,
    };
    if (ACTIVE.has(job.status)) {
      inQueue.add(job.recording_id);
      inProgress.push(row);
    } else if (row.reason) {
      inQueue.add(job.recording_id);
      toCheck.push(row);
    } else if (DONE.has(job.status)) {
      history.push(row);
    }
  }

  const readyToTranscribe: QueueRow[] = [];
  for (const recording of library?.recordings ?? []) {
    if (inQueue.has(recording.id) || hiddenRecordings.has(recording.id)) continue;
    const status = recording.transcription_status ?? "NOT_STARTED";
    const base = {
      id: `recording:${recording.id}`,
      jobId: null,
      recordingId: recording.id,
      lesson: lessonForRecording(recording),
      elapsedSeconds: null,
      queuePosition: null,
      createdAt: recording.recorded_at ?? recording.created_at,
    };
    if (PROBLEMS[status]) {
      toCheck.push({ ...base, chip: { kind: "transcription", status }, reason: PROBLEMS[status] ?? null, actions: [] });
    } else if (status === "NOT_STARTED" && recording.can_transcribe === true && recording.audio_available !== false) {
      readyToTranscribe.push({ ...base, chip: { kind: "transcription", status: "NOT_STARTED" }, reason: null, actions: [] });
    }
  }

  // Running first, then waiting jobs in queue order (Swift: QUEUED last, then rank).
  inProgress.sort((a, b) => {
    const queuedA = a.chip.status === "QUEUED";
    const queuedB = b.chip.status === "QUEUED";
    if (queuedA !== queuedB) return queuedA ? 1 : -1;
    return (a.queuePosition ?? 0) - (b.queuePosition ?? 0) || oldestFirst(a, b);
  });
  readyToTranscribe.sort(oldestFirst);
  toCheck.sort(oldestFirst);
  history.sort((a, b) => oldestFirst(b, a));

  const notes: NotesRow[] = jobs
    .filter((job) => job.kind === "notes" || job.kind === "lesson")
    .map((job) => ({
      jobId: job.jobId,
      lessons: (job.lectureIds ?? []).map((id) => lessonOf(id, job.courseCode, id)),
      status: notesStatusOf(job),
      running: job.state === "running",
      startedAt: job.startedAt,
      finishedAt: job.finishedAt ?? null,
    }))
    .sort((a, b) => Number(b.running) - Number(a.running) || b.startedAt.localeCompare(a.startedAt));

  return {
    ready: queue !== null,
    paused: queue?.paused ?? false,
    parallel: Math.min(4, Math.max(1, queue?.parallel ?? 1)),
    inProgress,
    readyToTranscribe,
    toCheck,
    history,
    notes,
    waiting: inProgress.filter((row) => row.chip.status === "QUEUED").length,
    finishing: inProgress.filter((row) => row.chip.status !== "QUEUED").length,
    notesNeedingYou: notes.filter((row) => !row.running && NOTES_NEED_YOU.has(row.status)).length,
  };
}
