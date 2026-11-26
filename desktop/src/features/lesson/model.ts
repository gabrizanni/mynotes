/**
 * Lesson detail view-model (unit-tested in model.test.ts). Pure functions, no strings:
 * - `deriveLesson`: one lesson from the library, the queue, the calendar and notes jobs;
 * - `lessonChips`: the recording / transcript / notes chips (also used by the Lessons table);
 * - transcript search (accent- and case-insensitive, Italian-friendly) and match stepping;
 * - the audio player state machine (play, ±15 s, speed, keys).
 */
import type { CaptureDashboard, Config, Job, Library, LibraryLesson, LibraryRecording, QueueJob, QueueStatus, TranscriptSegment } from "../../rpc";
import type { ChipKind } from "../../components/StatusChip";
import { latestNotesJob, lessonNumberOf } from "./generate/model";

export interface Chip {
  kind: ChipKind;
  status: string;
}

/** What the main column shows (UX-SPEC §3 Lesson detail › States). */
export type LessonState = "noAudio" | "chooseSource" | "transcribing" | "notTranscribed" | "transcriptProblem" | "ready";

export interface LessonDetail {
  id: string;
  courseCode: string | null;
  courseName: string;
  number: number | null;
  date: string | null;
  recordings: LibraryRecording[];
  /** The recording the player and transcription use. */
  recording: LibraryRecording | null;
  transcriptId: string | null;
  transcriptPath: string | null;
  /** The queue job for this lesson's audio, if any. */
  transcription: QueueJob | null;
  state: LessonState;
  chips: { recording: Chip; transcript: Chip; notes: Chip };
  notesJob: Job | null;
}

export interface LessonInput {
  library: Library | null;
  queue: QueueStatus | null;
  dashboard: CaptureDashboard | null;
  config: Config | null;
  jobs: readonly Job[];
}

const TRANSCRIPTION_ACTIVE = new Set(["QUEUED", "RUNNING", "CANCELLING", "WAITING"]);
const TRANSCRIPTION_PROBLEM = new Set(["FAILED", "INTERRUPTED", "NEEDS_REVIEW", "OUTDATED"]);
const NOTES_RUNNING = new Set(["CHECKING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"]);

export function courseNameOf(code: string | null, input: Pick<LessonInput, "config" | "dashboard">): string {
  if (!code) return "";
  return input.config?.courses?.find((course) => course.code === code)?.name ?? input.dashboard?.courses.find((course) => course.code === code)?.name ?? code;
}

/** Chip of a notes job: its live stage while running, its outcome after. */
export function notesChipOf(job: Job | null, inCourseNotes = false): Chip {
  if (!job) return { kind: "notes", status: inCourseNotes ? "COMPLETED" : "NONE" };
  if (job.state === "running") return { kind: "notes", status: job.stage && NOTES_RUNNING.has(job.stage) ? job.stage : "CHECKING" };
  if (job.state === "cancelled" && (!job.result?.status || job.result.status === "STOPPED")) return { kind: "notes", status: "CANCELLED" };
  return { kind: "notes", status: job.result?.status ?? (job.state === "succeeded" ? "COMPLETED" : "NEEDS_REVIEW") };
}

interface ChipInput {
  lesson: Pick<LibraryLesson, "recording_ids" | "transcript_ids" | "not_recorded">;
  recordings: readonly LibraryRecording[];
  transcription: QueueJob | null;
  notesJob: Job | null;
  inCourseNotes: boolean;
}

export function lessonChips({ lesson, recordings, transcription, notesJob, inCourseNotes }: ChipInput): LessonDetail["chips"] {
  const audio = recordings.some((recording) => recording.audio_available !== false);
  const recording: Chip = audio
    ? { kind: "recording", status: "COMPLETED" }
    : { kind: "recording", status: lesson.not_recorded || lesson.recording_ids.length === 0 ? "MISSED" : "PARTIAL" };
  let transcriptStatus = lesson.transcript_ids.length > 0 ? "COMPLETED" : (transcription?.status ?? recordings.find((r) => r.transcription_status)?.transcription_status ?? "NOT_STARTED");
  if (lesson.transcript_ids.length === 0 && transcriptStatus === "COMPLETED") transcriptStatus = "NOT_STARTED";
  return {
    recording,
    transcript: { kind: "transcription", status: audio || lesson.transcript_ids.length > 0 ? transcriptStatus : "NOT_STARTED" },
    notes: notesChipOf(notesJob, inCourseNotes),
  };
}

function eventLesson(id: string, dashboard: CaptureDashboard | null): LibraryLesson | null {
  const parsed = /^([A-Z0-9]+)-L(\d{1,3})$/.exec(id);
  if (!parsed?.[1] || !parsed[2]) return null;
  const code = parsed[1];
  const number = Number(parsed[2]);
  const event = dashboard?.events.find((candidate) => candidate.courseCode === code && candidate.lessonNumber === number);
  if (!event) return null;
  return {
    id,
    course: code,
    number,
    title: event.title,
    date: event.startsAt.slice(0, 10),
    recording_ids: [],
    transcript_ids: [],
    event_ids: [event.id],
    not_recorded: true,
    hidden: false,
    revision_required: false,
  };
}

/**
 * One lesson, or `null` when neither the library nor the calendar knows it. `inCourseNotes`
 * says whether a publication already holds the lesson (from `notes.history`).
 */
export function deriveLesson(id: string, input: LessonInput, inCourseNotes = false): LessonDetail | null {
  const lesson = input.library?.lessons?.find((candidate) => candidate.id === id) ?? eventLesson(id, input.dashboard);
  if (!lesson) return null;
  const all = input.library?.recordings ?? [];
  const recordings = lesson.recording_ids
    .map((recordingId) => all.find((recording) => recording.id === recordingId))
    .filter((recording): recording is LibraryRecording => recording !== undefined);
  const transcriptions = (input.queue?.jobs ?? []).filter((job) => recordings.some((recording) => recording.id === job.recording_id));
  const transcription = transcriptions.find((job) => TRANSCRIPTION_ACTIVE.has(job.status)) ?? transcriptions[0] ?? null;
  const preferred =
    recordings.find((recording) => recording.id === transcription?.recording_id) ??
    recordings.find((recording) => recording.audio_available !== false) ??
    recordings[0] ??
    null;
  const transcriptId = lesson.transcript_ids[0] ?? null;
  const transcriptPath = transcriptId ? (input.library?.transcripts?.find((transcript) => transcript.id === transcriptId)?.path ?? null) : null;
  const notesJob = latestNotesJob(input.jobs, id);
  const hasAudio = recordings.some((recording) => recording.audio_available !== false);

  let state: LessonState;
  if (transcriptId) state = "ready";
  else if (!hasAudio) state = "noAudio";
  else if (transcription && TRANSCRIPTION_ACTIVE.has(transcription.status)) state = "transcribing";
  else if (transcription && TRANSCRIPTION_PROBLEM.has(transcription.status)) state = "transcriptProblem";
  else if (recordings.filter((recording) => recording.audio_available !== false).length > 1) state = "chooseSource";
  else state = "notTranscribed";

  const courseCode = lesson.course ?? null;
  return {
    id,
    courseCode,
    courseName: courseNameOf(courseCode, input) || lesson.title,
    number: lesson.number ?? lessonNumberOf(id),
    date: lesson.date ?? null,
    recordings,
    recording: preferred,
    transcriptId,
    transcriptPath,
    transcription,
    state,
    chips: lessonChips({ lesson, recordings, transcription, notesJob, inCourseNotes }),
    notesJob,
  };
}

// ---------------------------------------------------------------------------------------------
// Transcript search
// ---------------------------------------------------------------------------------------------

export interface Match {
  segment: number;
  /** Character offsets in the original text, end exclusive. */
  start: number;
  end: number;
}

/** Lower-case, accent-free text plus a map from each folded character to its original index. */
export function fold(text: string): { folded: string; map: number[] } {
  let folded = "";
  const map: number[] = [];
  let index = 0;
  for (const char of text) {
    const plain = char.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase();
    for (const piece of plain) {
      folded += piece;
      map.push(index);
    }
    index += char.length;
  }
  return { folded, map };
}

/** Every match of `query` across the segments, in reading order (case- and accent-insensitive). */
export function findMatches(segments: readonly Pick<TranscriptSegment, "text">[], query: string): Match[] {
  const needle = fold(query.trim()).folded;
  if (needle.length < 2) return [];
  const matches: Match[] = [];
  segments.forEach((segment, segmentIndex) => {
    const { folded, map } = fold(segment.text);
    let from = folded.indexOf(needle);
    while (from !== -1) {
      const last = map[from + needle.length - 1];
      const first = map[from];
      if (first !== undefined && last !== undefined) {
        const lastChar = segment.text.codePointAt(last) ?? 0;
        matches.push({ segment: segmentIndex, start: first, end: last + (lastChar > 0xffff ? 2 : 1) });
      }
      from = folded.indexOf(needle, from + needle.length);
    }
  });
  return matches;
}

/** Next (`+1`) or previous (`-1`) match, wrapping around; `-1` when there is none. */
export function stepMatch(current: number, count: number, direction: 1 | -1): number {
  if (count === 0) return -1;
  if (current < 0) return direction === 1 ? 0 : count - 1;
  return (current + direction + count) % count;
}

export interface TextPart {
  text: string;
  /** Index of the match in the global list, or null for plain text. */
  match: number | null;
}

/** Split one segment's text into plain and highlighted parts. */
export function highlightParts(text: string, matches: readonly Match[], segment: number): TextPart[] {
  const parts: TextPart[] = [];
  let cursor = 0;
  matches.forEach((match, index) => {
    if (match.segment !== segment || match.start < cursor) return;
    if (match.start > cursor) parts.push({ text: text.slice(cursor, match.start), match: null });
    parts.push({ text: text.slice(match.start, match.end), match: index });
    cursor = match.end;
  });
  if (cursor < text.length || parts.length === 0) parts.push({ text: text.slice(cursor), match: null });
  return parts;
}

/** Index of the segment playing at `position` seconds (the last one that started), or -1. */
export function activeSegment(segments: readonly Pick<TranscriptSegment, "start_seconds">[], position: number): number {
  let active = -1;
  for (let index = 0; index < segments.length; index += 1) {
    const start = segments[index]?.start_seconds ?? Number.POSITIVE_INFINITY;
    if (start <= position + 0.25) active = index;
    else break;
  }
  return active;
}

// ---------------------------------------------------------------------------------------------
// Audio player
// ---------------------------------------------------------------------------------------------

export const SKIP_SECONDS = 15;
export const RATES = [1, 1.5, 2] as const;
export type Rate = (typeof RATES)[number];

export interface PlayerState {
  position: number;
  duration: number;
  playing: boolean;
  rate: Rate;
}

export type PlayerAction =
  | { type: "toggle" }
  | { type: "pause" }
  | { type: "seek"; to: number }
  | { type: "skip"; by: number }
  | { type: "rate"; rate: Rate }
  /** Time reported by the audio element (or the simulated clock). */
  | { type: "tick"; position: number }
  | { type: "duration"; duration: number }
  | { type: "ended" };

export function initialPlayer(duration: number): PlayerState {
  return { position: 0, duration: Math.max(0, duration), playing: false, rate: 1 };
}

const clampTo = (value: number, duration: number) => Math.min(Math.max(0, value), duration > 0 ? duration : Number.POSITIVE_INFINITY);

export function playerReducer(state: PlayerState, action: PlayerAction): PlayerState {
  switch (action.type) {
    case "toggle":
      if (!state.playing && state.duration > 0 && state.position >= state.duration) return { ...state, position: 0, playing: true };
      return { ...state, playing: !state.playing };
    case "pause":
      return state.playing ? { ...state, playing: false } : state;
    case "seek":
      return { ...state, position: clampTo(action.to, state.duration) };
    case "skip":
      return { ...state, position: clampTo(state.position + action.by, state.duration) };
    case "rate":
      return { ...state, rate: action.rate };
    case "tick": {
      const position = clampTo(action.position, state.duration);
      if (state.duration > 0 && position >= state.duration) return { ...state, position: state.duration, playing: false };
      return { ...state, position };
    }
    case "duration":
      return { ...state, duration: Math.max(0, action.duration), position: clampTo(state.position, action.duration) };
    case "ended":
      return { ...state, position: state.duration, playing: false };
  }
}

/** Space plays or pauses, ←/→ jump 15 s (UX-SPEC §3). Other keys are not the player's. */
export function playerKey(key: string): PlayerAction | null {
  if (key === " " || key === "Spacebar") return { type: "toggle" };
  if (key === "ArrowLeft") return { type: "skip", by: -SKIP_SECONDS };
  if (key === "ArrowRight") return { type: "skip", by: SKIP_SECONDS };
  return null;
}

/** Keys typed into a field, or pressed on a control that handles them, stay with that control. */
export function keyBelongsToControl(target: EventTarget | null, key: string): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  const role = target.getAttribute("role");
  if (role === "radio" || role === "slider" || role === "spinbutton" || role === "menuitem" || role === "switch") return true;
  return key === " " && (tag === "BUTTON" || tag === "A");
}
