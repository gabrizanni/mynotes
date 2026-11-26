/**
 * Today's view-model: a pure function of the store and the clock (unit-tested in model.test.ts).
 * No strings here; `TodayPage` localizes every reason and status.
 *
 * Hero primary action, in priority order (UX-SPEC §3 Today):
 *   live recording → Stop · blocking error → Fix · AWAITING_APPROVAL → Review ·
 *   transcribed lesson without notes → Generate notes · otherwise → Record now.
 */
import type { CaptureDashboard, CaptureEvent, Config, Job, Library, QueueStatus } from "../../rpc";
import type { ChipKind } from "../../components/StatusChip";
import type { Route } from "../../state/routes";

export interface LessonRef {
  lectureId: string | null;
  courseCode: string | null;
  courseName: string;
  lessonNumber: number | null;
}

export interface Chip {
  kind: ChipKind;
  status: string;
}

export type LessonPhase = "past" | "live" | "next" | "later";

export interface TodayLesson extends LessonRef {
  eventId: string;
  start: Date;
  end: Date;
  phase: LessonPhase;
  chip: Chip;
}

export type FixProblem = "microphone" | "recorderStopped" | "recordingFailed";

export type HeroAction =
  | { kind: "stop" }
  | { kind: "fix"; problem: FixProblem }
  | { kind: "review"; jobId: string; lesson: LessonRef }
  | { kind: "generate"; lesson: LessonRef }
  | { kind: "record" };

export interface Hero {
  /** `live`: recording now; `next`: an upcoming lesson; `none`: nothing scheduled. */
  mode: "live" | "next" | "none";
  lesson: TodayLesson | null;
  /** Start of the live session (for the elapsed clock). */
  startedAt: Date | null;
  recorderChip: Chip;
  isToday: boolean;
  action: HeroAction;
}

export type NeedsReason =
  | "transcriptionToCheck"
  | "transcriptionWaiting"
  | "notesApproval"
  | "notesPausedQuota"
  | "notesPausedAuth"
  | "notesPausedNetwork"
  | "notesConflict"
  | "notesWarnings"
  | "notesNeedsReview"
  | "notesNeedsTranscript"
  | "recordingMissed"
  | "recordingFailed"
  | "timeClash"
  | "needsCourse";

export interface NeedsItem {
  id: string;
  lesson: LessonRef;
  reason: NeedsReason;
  chip: Chip;
  severity: 0 | 1;
  target: Route;
}

export type SetupItemId = "profile" | "schedule" | "microphone" | "automatic";

export interface SetupItem {
  id: SetupItemId;
  done: boolean;
  target: Route;
}

export interface TodayModel {
  ready: boolean;
  hero: Hero;
  needsYou: NeedsItem[];
  needsYouTotal: number;
  lessons: TodayLesson[];
  setup: SetupItem[];
}

export interface TodayInput {
  dashboard: CaptureDashboard | null;
  queue: QueueStatus | null;
  library: Library | null;
  config: Config | null;
  jobs: readonly Job[];
  now: Date;
}

export const NEEDS_YOU_LIMIT = 5;
/** A transcript newer than this without notes is offered as "Generate notes". */
const GENERATE_WINDOW_MS = 7 * 86_400_000;

const pad2 = (n: number) => String(n).padStart(2, "0");

export function lectureIdOf(courseCode: string | null | undefined, lessonNumber: number | null | undefined): string | null {
  return courseCode && lessonNumber ? `${courseCode}-L${pad2(lessonNumber)}` : null;
}

export function parseLectureId(id: string): { courseCode: string; lessonNumber: number } | null {
  const match = /^([A-Z0-9]+)-L(\d{1,3})$/.exec(id);
  if (!match?.[1] || !match[2]) return null;
  return { courseCode: match[1], lessonNumber: Number(match[2]) };
}

function sameLocalDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

const NOTES_REASONS: Record<string, { reason: NeedsReason; severity: 0 | 1 }> = {
  AWAITING_APPROVAL: { reason: "notesApproval", severity: 1 },
  PAUSED_QUOTA: { reason: "notesPausedQuota", severity: 1 },
  PAUSED_AUTH: { reason: "notesPausedAuth", severity: 1 },
  PAUSED_NETWORK: { reason: "notesPausedNetwork", severity: 1 },
  PAUSED_SLEEP: { reason: "notesPausedNetwork", severity: 1 },
  PERSONAL_CONTENT_CONFLICT: { reason: "notesConflict", severity: 0 },
  COMPLETED_WITH_WARNINGS: { reason: "notesWarnings", severity: 1 },
  NEEDS_REVIEW: { reason: "notesNeedsReview", severity: 0 },
  STOPPED: { reason: "notesNeedsReview", severity: 0 },
  NEEDS_TRANSCRIPT: { reason: "notesNeedsTranscript", severity: 1 },
};

const TRANSCRIPTION_PROBLEMS = new Set(["FAILED", "INTERRUPTED", "NEEDS_REVIEW", "OUTDATED"]);
const NOTES_RUNNING_STAGES = new Set(["CHECKING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"]);

/** Status a notes job shows on a chip: its live stage while running, its outcome after. */
export function notesStatusOf(job: Job): string {
  if (job.state === "running") return job.stage && NOTES_RUNNING_STAGES.has(job.stage) ? job.stage : "GENERATING";
  return job.result?.status ?? (job.state === "succeeded" ? "COMPLETED" : "NEEDS_REVIEW");
}

export function deriveToday(input: TodayInput): TodayModel {
  const { dashboard, queue, library, config, now } = input;
  const nowMs = now.getTime();
  const courseNames = new Map<string, string>();
  for (const course of config?.courses ?? []) courseNames.set(course.code, course.name);
  for (const course of dashboard?.courses ?? []) courseNames.set(course.code, course.name);

  const ref = (courseCode: string | null | undefined, lessonNumber: number | null | undefined, fallback = ""): LessonRef => ({
    lectureId: lectureIdOf(courseCode, lessonNumber),
    courseCode: courseCode ?? null,
    courseName: (courseCode && courseNames.get(courseCode)) || fallback || courseCode || "",
    lessonNumber: lessonNumber ?? null,
  });
  const refFromLecture = (lectureId: string | null | undefined, fallback = ""): LessonRef => {
    const parsed = lectureId ? parseLectureId(lectureId) : null;
    return parsed ? ref(parsed.courseCode, parsed.lessonNumber, fallback) : { lectureId: null, courseCode: null, courseName: fallback, lessonNumber: null };
  };

  const notesJobs = input.jobs.filter((job) => job.kind === "notes" || job.kind === "lesson");
  const notesByLecture = new Map<string, Job>();
  for (const job of notesJobs) {
    for (const id of job.lectureIds ?? []) if (!notesByLecture.has(id)) notesByLecture.set(id, job);
  }

  const recordings = library?.recordings ?? [];
  const recordingById = new Map(recordings.map((recording) => [recording.id, recording]));
  const transcriptionByLecture = new Map<string, string>();
  for (const recording of recordings) {
    if (recording.lecture_id && recording.transcription_status) transcriptionByLecture.set(recording.lecture_id, recording.transcription_status);
  }
  for (const job of queue?.jobs ?? []) {
    const lecture = recordingById.get(job.recording_id)?.lecture_id;
    if (lecture) transcriptionByLecture.set(lecture, job.status);
  }

  // Recorder state -----------------------------------------------------------------------------
  const runtime = dashboard?.runtime;
  const live = dashboard?.running === true && runtime?.recording === true && runtime.currentSession ? runtime.currentSession : null;
  const micBlocked = runtime?.microphonePermission === "DENIED" || runtime?.microphonePermission === "RESTRICTED";
  const recorderStopped = dashboard?.enabled === true && dashboard.running === false;
  const recordingFailed = !live && runtime?.title === "Recording failed";
  const savedStatuses = new Set(["COMPLETED", "PARTIAL", "EXISTING", ...(dashboard?.running ? ["RECORDING", "EXPORTING"] : [])]);
  const sessions = runtime?.sessions ?? [];
  const sessionByEvent = new Map(sessions.map((session) => [session.event.id, session]));
  const saved = new Set(sessions.filter((session) => savedStatuses.has(session.status)).map((session) => session.event.id));

  const events = [...(dashboard?.events ?? [])].sort((a, b) => a.startsAt.localeCompare(b.startsAt) || a.id.localeCompare(b.id));
  const upcoming = events.find((event) => event.eligibility !== "CANCELLED" && Date.parse(event.endsAt) > nowMs && !saved.has(event.id));

  const recorderChipFor = (event: CaptureEvent | undefined): Chip => {
    if (live && event && live.event.id === event.id) return { kind: "recording", status: "RECORDING" };
    if (micBlocked) return { kind: "recording", status: "MIC_BLOCKED" };
    if (dashboard && (!dashboard.enabled || dashboard.running === false)) return { kind: "recording", status: "OFF" };
    if (event?.eligibility === "CONFLICT") return { kind: "recording", status: "CONFLICT" };
    if (event?.eligibility === "NEEDS_REVIEW") return { kind: "recording", status: "NEEDS_COURSE" };
    return { kind: "recording", status: "SCHEDULED" };
  };

  // Today's lessons ------------------------------------------------------------------------------
  const todayEvents = events.filter((event) => sameLocalDay(new Date(event.startsAt), now));
  let nextAssigned = false;
  const lessons: TodayLesson[] = todayEvents.map((event) => {
    const start = new Date(event.startsAt);
    const end = new Date(event.endsAt);
    const lesson = ref(event.courseCode, event.lessonNumber, event.title);
    let phase: LessonPhase;
    if (live?.event.id === event.id) phase = "live";
    else if (end.getTime() <= nowMs || saved.has(event.id)) phase = "past";
    else if (!nextAssigned && event.eligibility !== "CANCELLED") {
      phase = "next";
      nextAssigned = true;
    } else phase = "later";
    return { ...lesson, eventId: event.id, start, end, phase, chip: lessonChip(event, phase, lesson.lectureId) };
  });

  function lessonChip(event: CaptureEvent, phase: LessonPhase, lectureId: string | null): Chip {
    if (phase === "live") return { kind: "recording", status: "RECORDING" };
    if (event.eligibility === "CANCELLED") return { kind: "recording", status: "CANCELLED" };
    if (phase !== "past") return recorderChipFor(event);
    const job = lectureId ? notesByLecture.get(lectureId) : undefined;
    if (job) return { kind: "notes", status: notesStatusOf(job) };
    const transcription = lectureId ? transcriptionByLecture.get(lectureId) : undefined;
    if (transcription && transcription !== "NOT_STARTED") return { kind: "transcription", status: transcription };
    const session = sessionByEvent.get(event.id);
    if (session) return { kind: "recording", status: session.status };
    return { kind: "recording", status: "MISSED" };
  }

  // Needs you ----------------------------------------------------------------------------------
  const needs: NeedsItem[] = [];
  const activity: Route = { name: "activity" };
  for (const job of notesJobs) {
    if (job.state === "running") continue;
    const status = notesStatusOf(job);
    const mapped = NOTES_REASONS[status];
    if (!mapped) continue;
    needs.push({
      id: `notes:${job.jobId}`,
      lesson: refFromLecture(job.lectureIds?.[0], job.courseCode ?? ""),
      reason: mapped.reason,
      severity: mapped.severity,
      chip: { kind: "notes", status },
      target: activity,
    });
  }
  for (const job of queue?.jobs ?? []) {
    const problem = TRANSCRIPTION_PROBLEMS.has(job.status);
    if (!problem && job.status !== "WAITING") continue;
    const lecture = recordingById.get(job.recording_id)?.lecture_id;
    needs.push({
      id: `queue:${job.id}`,
      lesson: refFromLecture(lecture, job.title),
      reason: problem ? "transcriptionToCheck" : "transcriptionWaiting",
      severity: job.status === "FAILED" ? 0 : 1,
      chip: { kind: "transcription", status: job.status },
      target: activity,
    });
  }
  for (const event of todayEvents) {
    const session = sessionByEvent.get(event.id);
    const lesson = ref(event.courseCode, event.lessonNumber, event.title);
    const calendar: Route = { name: "calendar", view: "week", date: null };
    if (session?.status === "FAILED" || session?.status === "MISSED") {
      needs.push({
        id: `session:${session.id}`,
        lesson,
        reason: session.status === "FAILED" ? "recordingFailed" : "recordingMissed",
        severity: session.status === "FAILED" ? 0 : 1,
        chip: { kind: "recording", status: session.status },
        target: calendar,
      });
    } else if (event.eligibility === "CONFLICT") {
      needs.push({ id: `event:${event.id}`, lesson, reason: "timeClash", severity: 1, chip: { kind: "recording", status: "CONFLICT" }, target: calendar });
    } else if (event.eligibility === "NEEDS_REVIEW" && event.reasonCode === "UNKNOWN_COURSE") {
      needs.push({ id: `event:${event.id}`, lesson, reason: "needsCourse", severity: 1, chip: { kind: "recording", status: "NEEDS_COURSE" }, target: calendar });
    }
  }

  // Hero action --------------------------------------------------------------------------------
  let action: HeroAction = { kind: "record" };
  const approval = notesJobs.find((job) => job.state !== "running" && notesStatusOf(job) === "AWAITING_APPROVAL");
  const generateCandidate = (library?.lessons ?? [])
    .filter((lesson) => {
      const id = lesson.id;
      if (notesByLecture.has(id) || lesson.hidden) return false;
      const transcribed = lesson.transcript_ids.length > 0 || transcriptionByLecture.get(id) === "COMPLETED";
      const when = lesson.date ? Date.parse(lesson.date) : Number.NaN;
      return transcribed && Number.isFinite(when) && nowMs - when < GENERATE_WINDOW_MS && when <= nowMs;
    })
    .sort((a, b) => (b.date ?? "").localeCompare(a.date ?? ""))[0];

  if (live) action = { kind: "stop" };
  else if (micBlocked) action = { kind: "fix", problem: "microphone" };
  else if (recorderStopped) action = { kind: "fix", problem: "recorderStopped" };
  else if (recordingFailed) action = { kind: "fix", problem: "recordingFailed" };
  else if (approval) action = { kind: "review", jobId: approval.jobId, lesson: refFromLecture(approval.lectureIds?.[0], approval.courseCode ?? "") };
  else if (generateCandidate) action = { kind: "generate", lesson: refFromLecture(generateCandidate.id, generateCandidate.title) };

  // The hero already carries its item: do not repeat it below.
  const heroItem = action.kind === "review" ? `notes:${action.jobId}` : null;
  const needsYou = needs
    .filter((item) => item.id !== heroItem)
    .sort((a, b) => a.severity - b.severity);

  // Hero lesson ---------------------------------------------------------------------------------
  let hero: Hero;
  if (live) {
    const event = events.find((candidate) => candidate.id === live.event.id);
    const lesson = lessons.find((candidate) => candidate.eventId === live.event.id);
    const fallback: TodayLesson = {
      ...ref(live.event.courseCode, live.event.lessonNumber, live.event.title ?? ""),
      eventId: live.event.id,
      start: new Date(live.actualStart ?? now),
      end: event ? new Date(event.endsAt) : now,
      phase: "live",
      chip: { kind: "recording", status: "RECORDING" },
    };
    hero = {
      mode: "live",
      lesson: lesson ?? fallback,
      startedAt: live.actualStart ? new Date(live.actualStart) : null,
      recorderChip: { kind: "recording", status: "RECORDING" },
      isToday: true,
      action,
    };
  } else if (upcoming) {
    const start = new Date(upcoming.startsAt);
    const lesson = lessons.find((candidate) => candidate.eventId === upcoming.id) ?? {
      ...ref(upcoming.courseCode, upcoming.lessonNumber, upcoming.title),
      eventId: upcoming.id,
      start,
      end: new Date(upcoming.endsAt),
      phase: "next" as const,
      chip: recorderChipFor(upcoming),
    };
    hero = { mode: "next", lesson, startedAt: null, recorderChip: recorderChipFor(upcoming), isToday: sameLocalDay(start, now), action };
  } else {
    hero = { mode: "none", lesson: null, startedAt: null, recorderChip: recorderChipFor(undefined), isToday: false, action };
  }

  // Finish setup -------------------------------------------------------------------------------
  const completed = new Set(config?.onboarding.completed_groups ?? []);
  const setup: SetupItem[] = [
    { id: "profile", done: completed.has("personal"), target: { name: "settings", section: "profile" } },
    { id: "schedule", done: Boolean(dashboard?.configured && dashboard.sources.length > 0), target: { name: "settings", section: "calendars" } },
    { id: "microphone", done: runtime?.microphonePermission === "AUTHORIZED", target: { name: "settings", section: "recording" } },
    { id: "automatic", done: Boolean(dashboard?.enabled && !dashboard.paused), target: { name: "settings", section: "recording" } },
  ];

  return {
    ready: dashboard !== null,
    hero,
    needsYou: needsYou.slice(0, NEEDS_YOU_LIMIT),
    needsYouTotal: needsYou.length,
    lessons,
    setup,
  };
}
