/**
 * Library view-model: lessons, recordings and transcripts as one searchable list, plus the
 * import plan for dropped audio. Pure; unit-tested in model.test.ts.
 *
 * The lesson registry (`library.lessons`) is the source of truth when present, as in the Swift
 * `WorkspaceLesson.build`; recordings or transcripts outside every lesson become their own rows.
 */
import type { AudioImportItem, AudioInspection, Library, LibraryLesson, LibraryRecording, LibraryTranscript } from "../../rpc";

export interface LibraryItem {
  id: string;
  /** Registry lesson id (`STAT-L07`, `lesson-…`) or null for a loose recording or transcript. */
  lessonId: string | null;
  lectureId: string | null;
  courseCode: string | null;
  courseName: string | null;
  lessonNumber: number | null;
  title: string;
  /** Day of the lesson or the recording, `YYYY-MM-DD`; null when unknown. */
  date: string | null;
  recordings: LibraryRecording[];
  transcripts: LibraryTranscript[];
  hidden: boolean;
  unassigned: boolean;
  /** Seconds of the longest recording. */
  duration: number | null;
  /** Transcription state to show: from the recording, or COMPLETED when a transcript exists. */
  transcription: string;
  imported: boolean;
}

export interface LibraryFilters {
  q: string;
  course: string | null;
  unassigned: boolean;
  hidden: boolean;
}

export interface LibraryModel {
  ready: boolean;
  items: LibraryItem[];
  total: number;
  counts: { unassigned: number; hidden: number };
}

const LECTURE_ID = /^[A-Z0-9]+-L\d{1,3}$/;

/** Lowercase without accents, so "diritto" finds "Diritto" and "perche" finds "perché". */
export function normalize(text: string): string {
  return text
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLocaleLowerCase();
}

function dayOf(value: string | null | undefined): string | null {
  return value ? value.slice(0, 10) : null;
}

function transcriptionOf(recordings: readonly LibraryRecording[], transcripts: readonly LibraryTranscript[]): string {
  const statuses = recordings.map((recording) => recording.transcription_status ?? "NOT_STARTED");
  for (const status of ["RUNNING", "CANCELLING", "QUEUED", "FAILED", "INTERRUPTED", "NEEDS_REVIEW", "OUTDATED", "WAITING"]) {
    if (statuses.includes(status)) return status;
  }
  if (statuses.includes("COMPLETED") || transcripts.some((transcript) => transcript.status === "COMPLETED")) return "COMPLETED";
  return "NOT_STARTED";
}

export function buildItems(library: Library | null, courses: ReadonlyArray<{ code: string; name: string }>): LibraryItem[] {
  if (!library) return [];
  const names = new Map(courses.map((course) => [course.code, course.name]));
  const recordings = new Map(library.recordings.map((recording) => [recording.id, recording]));
  const transcripts = new Map((library.transcripts ?? []).map((transcript) => [transcript.id, transcript]));
  const usedRecordings = new Set<string>();
  const usedTranscripts = new Set<string>();
  const items: LibraryItem[] = [];

  const make = (
    id: string,
    lesson: LibraryLesson | null,
    courseCode: string | null,
    number: number | null,
    title: string,
    date: string | null,
    rows: LibraryRecording[],
    texts: LibraryTranscript[],
  ): LibraryItem => ({
    id,
    lessonId: lesson?.id ?? null,
    lectureId: lesson && LECTURE_ID.test(lesson.id) ? lesson.id : (rows.find((row) => row.lecture_id)?.lecture_id ?? null),
    courseCode,
    courseName: courseCode ? (names.get(courseCode) ?? courseCode) : null,
    lessonNumber: number,
    title,
    date,
    recordings: rows,
    transcripts: texts,
    hidden: lesson?.hidden ?? false,
    unassigned: courseCode === null,
    duration: rows.reduce<number | null>((longest, row) => (row.duration && row.duration > (longest ?? 0) ? row.duration : longest), null),
    transcription: transcriptionOf(rows, texts),
    imported: rows.some((row) => row.origin !== null && row.origin !== undefined && row.origin !== "capture"),
  });

  for (const lesson of library.lessons ?? []) {
    const rows = lesson.recording_ids.map((id) => recordings.get(id)).filter((row): row is LibraryRecording => row !== undefined);
    const texts = lesson.transcript_ids.map((id) => transcripts.get(id)).filter((row): row is LibraryTranscript => row !== undefined);
    for (const row of rows) usedRecordings.add(row.id);
    for (const text of texts) usedTranscripts.add(text.id);
    const date = lesson.date ?? dayOf(rows[0]?.recorded_at ?? rows[0]?.created_at);
    items.push(make(`lesson:${lesson.id}`, lesson, lesson.course ?? null, lesson.number ?? null, lesson.title, date, rows, texts));
  }
  for (const recording of library.recordings) {
    if (usedRecordings.has(recording.id)) continue;
    const texts = (library.transcripts ?? []).filter((transcript) => transcript.recording_id === recording.id && !usedTranscripts.has(transcript.id));
    for (const text of texts) usedTranscripts.add(text.id);
    const number = recording.lecture_id ? Number(recording.lecture_id.split("-L")[1]) || null : null;
    items.push(make(`recording:${recording.id}`, null, recording.course_code ?? null, number, recording.title, dayOf(recording.recorded_at ?? recording.created_at), [recording], texts));
  }
  for (const transcript of library.transcripts ?? []) {
    if (usedTranscripts.has(transcript.id)) continue;
    items.push(make(`transcript:${transcript.id}`, null, transcript.course_code ?? null, null, transcript.title, dayOf(transcript.created_at), [], [transcript]));
  }
  return items.sort((a, b) => (b.date ?? "").localeCompare(a.date ?? "") || a.id.localeCompare(b.id));
}

function matches(item: LibraryItem, query: string): boolean {
  if (!query) return true;
  const haystack = normalize(
    [item.title, item.courseName ?? "", item.courseCode ?? "", item.lectureId ?? "", item.date ?? "", ...item.recordings.map((row) => row.title), ...item.transcripts.map((row) => row.title)].join(" "),
  );
  return normalize(query)
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => haystack.includes(word));
}

export function deriveLibrary(library: Library | null, courses: ReadonlyArray<{ code: string; name: string }>, filters: LibraryFilters): LibraryModel {
  const all = buildItems(library, courses);
  const visible = all.filter((item) => !item.hidden);
  const items = all.filter((item) => {
    if (filters.hidden ? !item.hidden : item.hidden) return false;
    if (filters.unassigned && !item.unassigned) return false;
    if (filters.course && item.courseCode !== filters.course) return false;
    return matches(item, filters.q.trim());
  });
  return {
    ready: library !== null,
    items,
    total: visible.length,
    counts: { unassigned: visible.filter((item) => item.unassigned).length, hidden: all.length - visible.length },
  };
}

// ---------------------------------------------------------------------------------------------
// Import plan
// ---------------------------------------------------------------------------------------------

/** Where one dropped file goes: an existing lesson without audio, a course, or nowhere yet. */
export type ImportTarget = { kind: "none" } | { kind: "lesson"; lessonId: string } | { kind: "course"; courseCode: string };

export interface ImportRow {
  file: AudioInspection;
  target: ImportTarget;
  /** Already in the library (same bytes): skipped. */
  skip: boolean;
}

/** Lessons a file can join: visible, with a course, and no recording yet. */
export function openLessons(library: Library | null): LibraryLesson[] {
  return (library?.lessons ?? []).filter((lesson) => !lesson.hidden && lesson.course && lesson.recording_ids.length === 0);
}

/** Default target per file: the open lesson recorded that day, otherwise "Needs a course". */
export function planImport(files: readonly AudioInspection[], library: Library | null): ImportRow[] {
  const open = openLessons(library);
  const taken = new Set<string>();
  return files.map((file) => {
    const day = dayOf(file.recorded_at);
    const lesson = day ? open.find((candidate) => candidate.date === day && !taken.has(candidate.id)) : undefined;
    if (lesson) taken.add(lesson.id);
    return { file, target: lesson ? { kind: "lesson", lessonId: lesson.id } : { kind: "none" }, skip: file.duplicate === true };
  });
}

export function importItems(rows: readonly ImportRow[]): AudioImportItem[] {
  return rows
    .filter((row) => !row.skip)
    .map((row) => {
      const item: AudioImportItem = { path: row.file.path };
      if (row.target.kind === "lesson") item.target = row.target.lessonId;
      if (row.target.kind === "course") item.course = row.target.courseCode;
      if (row.file.recorded_at) item.recorded_at = row.file.recorded_at;
      return item;
    });
}

/** `<select>` value for a target, and back. */
export function targetValue(target: ImportTarget): string {
  return target.kind === "none" ? "none" : target.kind === "lesson" ? `lesson:${target.lessonId}` : `course:${target.courseCode}`;
}

export function parseTarget(value: string): ImportTarget {
  if (value.startsWith("lesson:")) return { kind: "lesson", lessonId: value.slice(7) };
  if (value.startsWith("course:")) return { kind: "course", courseCode: value.slice(7) };
  return { kind: "none" };
}

const AUDIO = /\.(m4a|mp3|wav|aac|flac|ogg|oga|opus|webm|mp4|caf|aiff?|wma)$/i;

/** Keep only audio files from a drop (folders and documents are ignored). */
export function audioPaths(paths: readonly string[]): string[] {
  return paths.filter((path) => AUDIO.test(path));
}
