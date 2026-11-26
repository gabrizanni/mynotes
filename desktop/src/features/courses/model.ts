/**
 * Courses view-model (unit-tested in model.test.ts). Pure functions, no strings:
 * - `courseCards`: the course list (next lesson, "Notes updated 2 days ago · Lesson 6");
 * - `courseLessons`: the Lessons table rows with their three chips;
 * - `historyEntries` / `notesSummary`: `notes.history` backups in plain terms;
 * - `courseRecord` / `profileDraft` / `styleDraft`: the Profile and Style forms;
 * - `linkPreview`: `course.link --dry-run` in one sentence of counts.
 */
import type { CaptureDashboard, Config, CourseLink, Job, Library, NotesBackup, QueueStatus, StyleProfile } from "../../rpc";
import { lessonNumberOf } from "../lesson/generate/model";
import { deriveLesson, type Chip, type LessonState } from "../lesson/model";

// ---------------------------------------------------------------------------------------------
// Notes history
// ---------------------------------------------------------------------------------------------

/** `20261124T105812123456Z` (utils.now_compact_utc) → Date; ISO strings pass too. */
export function parseStamp(value: string | null | undefined): Date | null {
  if (!value) return null;
  const compact = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(\d{0,6})Z$/.exec(value);
  if (compact) {
    const [, y, mo, d, h, mi, s, fraction = ""] = compact;
    const ms = Number((fraction + "000").slice(0, 3));
    return new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi), Number(s), ms));
  }
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? new Date(parsed) : null;
}

export type BackupKind = "publication" | "undo" | "restore" | "other";

export interface HistoryEntry {
  id: string;
  createdAt: Date | null;
  kind: BackupKind;
  /** The lesson whose publication this copy was saved before. */
  lessonNumber: number | null;
  /** Lessons the saved copy holds. */
  lessonCount: number;
}

function kindOf(reason: string | null | undefined): { kind: BackupKind; lectureId: string | null } {
  const publication = /^before ([A-Z0-9]+-L\d{1,3}) publication$/i.exec(reason ?? "");
  if (publication?.[1]) return { kind: "publication", lectureId: publication[1].toUpperCase() };
  if (/^before undo\b/i.test(reason ?? "")) return { kind: "undo", lectureId: null };
  if (/^before restore\b/i.test(reason ?? "")) return { kind: "restore", lectureId: null };
  return { kind: "other", lectureId: null };
}

/** Newest first. */
export function historyEntries(backups: readonly NotesBackup[]): HistoryEntry[] {
  return backups
    .map((backup) => {
      const { kind, lectureId } = kindOf(backup.reason);
      return {
        id: backup.backup_id,
        createdAt: parseStamp(backup.created_at),
        kind,
        lessonNumber: lectureId ? lessonNumberOf(lectureId) : null,
        lessonCount: backup.publication_state?.lessons?.length ?? 0,
      };
    })
    .sort((a, b) => (b.createdAt?.getTime() ?? 0) - (a.createdAt?.getTime() ?? 0));
}

export interface NotesSummary {
  /** When the course notes last received a lesson. */
  updatedAt: Date | null;
  /** The lesson that update added. */
  lessonNumber: number | null;
  /** Lessons known to be in the course notes. */
  lessons: number[];
  canUndo: boolean;
}

const PUBLISHED = new Set(["COMPLETED", "COMPLETED_WITH_WARNINGS"]);

/**
 * Course notes status from the backups (`before CODE-LNN publication`) and the app's notes jobs.
 * Neither alone is complete: the first publication leaves no backup, jobs only cover this app.
 */
export function notesSummary(code: string, backups: readonly NotesBackup[], jobs: readonly Job[]): NotesSummary {
  const lessons = new Set<number>();
  let updatedAt: Date | null = null;
  let lessonNumber: number | null = null;
  const consider = (at: Date | null, numbers: readonly number[]) => {
    for (const n of numbers) lessons.add(n);
    if (at && (!updatedAt || at > updatedAt)) {
      updatedAt = at;
      lessonNumber = numbers.length ? Math.max(...numbers) : lessonNumber;
    }
  };
  let undoable = false;
  for (const backup of backups.filter((item) => item.course === code)) {
    const { kind, lectureId } = kindOf(backup.reason);
    for (const lesson of backup.publication_state?.lessons ?? []) {
      const n = lessonNumberOf(lesson.lecture_id);
      if (n !== null) lessons.add(n);
    }
    if (kind === "publication" && lectureId) {
      undoable = true;
      const n = lessonNumberOf(lectureId);
      consider(parseStamp(backup.created_at), n === null ? [] : [n]);
    }
  }
  for (const job of jobs) {
    if (job.courseCode !== code || job.state !== "succeeded" || !PUBLISHED.has(job.result?.status ?? "COMPLETED")) continue;
    const numbers = (job.lectureIds ?? []).map(lessonNumberOf).filter((n): n is number => n !== null);
    undoable = true;
    consider(parseStamp(job.finishedAt), numbers);
  }
  return { updatedAt, lessonNumber, lessons: [...lessons].sort((a, b) => a - b), canUndo: undoable };
}

// ---------------------------------------------------------------------------------------------
// Course list
// ---------------------------------------------------------------------------------------------

export interface CourseCard {
  code: string;
  name: string;
  lessonCount: number;
  nextLesson: { start: Date; lessonNumber: number | null } | null;
  notes: NotesSummary;
  /** Lessons whose notes or transcript need the student. */
  attention: number;
}

export interface CoursesInput {
  config: Config | null;
  dashboard: CaptureDashboard | null;
  library: Library | null;
  queue: QueueStatus | null;
  jobs: readonly Job[];
  backups: readonly NotesBackup[];
  now: Date;
}

const ATTENTION = new Set(["AWAITING_APPROVAL", "PAUSED_QUOTA", "PAUSED_AUTH", "PAUSED_NETWORK", "PAUSED_SLEEP", "PERSONAL_CONTENT_CONFLICT", "NEEDS_REVIEW", "STOPPED", "COMPLETED_WITH_WARNINGS", "NEEDS_TRANSCRIPT", "CANCELLED"]);

export function courseList(input: Pick<CoursesInput, "config" | "dashboard">): Array<{ code: string; name: string }> {
  const seen = new Map<string, string>();
  for (const course of input.config?.courses ?? []) seen.set(course.code, course.name);
  for (const course of input.dashboard?.courses ?? []) if (!seen.has(course.code)) seen.set(course.code, course.name);
  return [...seen].map(([code, name]) => ({ code, name }));
}

export function nextLessonOf(code: string, dashboard: CaptureDashboard | null, now: Date): CourseCard["nextLesson"] {
  const event = (dashboard?.events ?? [])
    .filter((candidate) => candidate.courseCode === code && candidate.eligibility !== "CANCELLED" && Date.parse(candidate.startsAt) > now.getTime())
    .sort((a, b) => a.startsAt.localeCompare(b.startsAt))[0];
  return event ? { start: new Date(event.startsAt), lessonNumber: event.lessonNumber } : null;
}

export function courseCards(input: CoursesInput): CourseCard[] {
  return courseList(input).map(({ code, name }) => {
    const rows = courseLessons(code, input);
    return {
      code,
      name,
      lessonCount: rows.length,
      nextLesson: nextLessonOf(code, input.dashboard, input.now),
      notes: notesSummary(code, input.backups, input.jobs),
      attention: rows.filter((row) => ATTENTION.has(row.chips.notes.status) || row.chips.transcript.status === "NEEDS_REVIEW" || row.chips.transcript.status === "FAILED").length,
    };
  });
}

// ---------------------------------------------------------------------------------------------
// Lessons table
// ---------------------------------------------------------------------------------------------

export interface LessonRow {
  id: string;
  number: number | null;
  date: string | null;
  state: LessonState;
  chips: { recording: Chip; transcript: Chip; notes: Chip };
  /** A transcript exists, so notes can be generated (or regenerated). */
  canGenerate: boolean;
}

/** The course's lessons, newest first, hidden ones left out. */
export function courseLessons(code: string, input: Omit<CoursesInput, "now">): LessonRow[] {
  const inNotes = new Set(notesSummary(code, input.backups, input.jobs).lessons);
  return (input.library?.lessons ?? [])
    .filter((lesson) => lesson.course === code && !lesson.hidden)
    .map((lesson) => {
      const detail = deriveLesson(lesson.id, input, lesson.number !== null && lesson.number !== undefined && inNotes.has(lesson.number));
      return {
        id: lesson.id,
        number: lesson.number ?? lessonNumberOf(lesson.id),
        date: lesson.date ?? null,
        state: detail?.state ?? "noAudio",
        chips: detail?.chips ?? {
          recording: { kind: "recording", status: "MISSED" },
          transcript: { kind: "transcription", status: "NOT_STARTED" },
          notes: { kind: "notes", status: "NONE" },
        },
        canGenerate: lesson.transcript_ids.length > 0,
      };
    })
    .sort((a, b) => (b.number ?? 0) - (a.number ?? 0) || (b.date ?? "").localeCompare(a.date ?? ""));
}

// ---------------------------------------------------------------------------------------------
// Profile and style forms
// ---------------------------------------------------------------------------------------------

/** Backend kinds (configuration.COURSE_KINDS), in the order the form shows them. */
export const COURSE_KINDS = ["theoretical", "quantitative", "cases", "programming", "mixed"] as const;
export type CourseKind = (typeof COURSE_KINDS)[number];

export const NOTE_LANGUAGES = ["en", "it"] as const;

export interface ProfileDraft {
  name: string;
  lectureLanguage: string;
  notesLanguage: string;
  kind: CourseKind;
  assessment: string;
  examUnknown: boolean;
  difficultTopics: string[];
  context: string;
}

type CourseRecord = Record<string, unknown>;

const text = (value: unknown, fallback = ""): string => (typeof value === "string" ? value : fallback);
const record = (value: unknown): Record<string, unknown> => (typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {});

/** The full course record from `config.get` (loose objects keep every backend field). */
export function courseRecord(config: Config | null, code: string): CourseRecord | null {
  const course = config?.courses?.find((candidate) => candidate.code === code);
  return course ? { ...course } : null;
}

export function profileDraft(course: CourseRecord): ProfileDraft {
  const languages = record(course.languages);
  const kind = COURSE_KINDS.find((candidate) => candidate === course.kind) ?? "mixed";
  const assessment = text(course.assessment);
  return {
    name: text(course.name),
    lectureLanguage: text(languages.lesson, text(course.notes_language, "en")),
    notesLanguage: text(languages.notes, text(course.notes_language, "en")),
    kind,
    assessment,
    examUnknown: assessment === "",
    difficultTopics: Array.isArray(course.difficult_topics) ? course.difficult_topics.filter((item): item is string => typeof item === "string") : [],
    context: text(course.context),
  };
}

export type ProfileError = "nameRequired";

export function validateProfile(draft: ProfileDraft): ProfileError | null {
  return draft.name.trim() ? null : "nameRequired";
}

/**
 * `course edit --file` replaces the record, so the app sends every field it received plus the
 * edited ones (configuration.normalize_course fills defaults for anything missing).
 */
export function applyProfile(course: CourseRecord, draft: ProfileDraft): CourseRecord {
  return {
    ...course,
    name: draft.name.trim(),
    kind: draft.kind,
    languages: { ...record(course.languages), lesson: draft.lectureLanguage, notes: draft.notesLanguage },
    notes_language: draft.notesLanguage,
    assessment: draft.examUnknown ? "" : draft.assessment.trim(),
    difficult_topics: draft.difficultTopics,
    context: draft.context.trim(),
  };
}

export function reviewFirst(course: CourseRecord | null): boolean {
  return record(course?.publication).mode === "review";
}

export function withPublicationMode(course: CourseRecord, review: boolean): CourseRecord {
  return { ...course, publication: { ...record(course.publication), mode: review ? "review" : "automatic" } };
}

export const STYLE_DEFAULTS: StyleDraft = {
  writing_style: "clear_study",
  list_density: "balanced",
  image_policy: "essential",
  image_limit: null,
  question_count: 5,
  recap: true,
  recap_language: "same_as_notes",
};

export interface StyleDraft {
  writing_style: "concise" | "clear_study" | "detailed";
  list_density: "prose_first" | "balanced" | "list_first";
  image_policy: "none" | "essential" | "explanatory";
  image_limit: number | null;
  question_count: number;
  recap: boolean;
  recap_language: "same_as_notes" | "it" | "en";
}

function pick<T extends string>(values: readonly T[], value: unknown, fallback: T): T {
  return values.find((candidate) => candidate === value) ?? fallback;
}

export function styleDraft(profile: StyleProfile | null): StyleDraft {
  if (!profile) return STYLE_DEFAULTS;
  return {
    writing_style: pick(["concise", "clear_study", "detailed"], profile.writing_style, STYLE_DEFAULTS.writing_style),
    list_density: pick(["prose_first", "balanced", "list_first"], profile.list_density, STYLE_DEFAULTS.list_density),
    image_policy: pick(["none", "essential", "explanatory"], profile.image_policy, STYLE_DEFAULTS.image_policy),
    image_limit: profile.image_limit ?? null,
    question_count: Math.min(5, Math.max(1, profile.question_count)),
    recap: profile.recap,
    recap_language: pick(["same_as_notes", "it", "en"], profile.recap_language, STYLE_DEFAULTS.recap_language),
  };
}

/** Only the fields that differ from what is saved (`style edit --file` takes a partial). */
export function styleChanges(saved: StyleDraft, draft: StyleDraft): Partial<StyleDraft> {
  const changes: Partial<StyleDraft> = {};
  for (const key of Object.keys(draft) as Array<keyof StyleDraft>) {
    if (saved[key] !== draft[key]) Object.assign(changes, { [key]: draft[key] });
  }
  return changes;
}

// ---------------------------------------------------------------------------------------------
// Files
// ---------------------------------------------------------------------------------------------

export interface LinkPreview {
  status: "ready" | "needsSelection" | "linked";
  folder: string | null;
  pdfs: number;
  recordings: number;
  transcripts: number;
  notebook: { name: string; isNew: boolean } | null;
  handout: string | null;
  /** Roles with more than one candidate (one radio question each). */
  choices: Array<{ role: string; options: string[] }>;
}

export function fileName(path: string | null | undefined): string | null {
  if (!path) return null;
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? null;
}

export function linkPreview(result: CourseLink): LinkPreview {
  const counts = result.inventory?.file_counts ?? {};
  const materials = result.inventory?.files?.materials ?? [];
  const pdfs = materials.length > 0 ? materials.filter((path) => /\.pdf$/i.test(path)).length : (counts.materials ?? 0);
  const files = result.note_files ?? result.proposed_files;
  const notebookPath = files?.personal_notes ?? null;
  const existingNotes = result.inventory?.files?.personal_notes ?? [];
  const status = result.status === "NEEDS_SELECTION" ? "needsSelection" : result.status === "LINKED" ? "linked" : "ready";
  return {
    status,
    folder: result.inventory?.resolved_folder ?? (result.connections ?? result.proposed_connections)?.materials ?? null,
    pdfs,
    recordings: counts.recordings ?? 0,
    transcripts: counts.transcripts ?? 0,
    notebook: notebookPath ? { name: fileName(notebookPath) ?? notebookPath, isNew: !existingNotes.includes(notebookPath) } : null,
    handout: fileName(files?.handout),
    choices: Object.entries(result.ambiguities ?? {}).map(([role, options]) => ({ role, options })),
  };
}

export interface LinkedFolder {
  role: "materials" | "personal_notes" | "recordings";
  path: string;
}

export function linkedFolders(course: CourseRecord | null): LinkedFolder[] {
  const folders = record(course?.folders);
  const roles: LinkedFolder["role"][] = ["materials", "personal_notes", "recordings"];
  return roles.flatMap((role) => {
    const path = text(folders[role]);
    return path ? [{ role, path }] : [];
  });
}

export interface Attachment {
  lectureId: string;
  lessonNumber: number | null;
  name: string;
  path: string;
}

/** `lesson_sources`: exact per-lesson files that win over filename discovery. */
export function lessonAttachments(course: CourseRecord | null): Attachment[] {
  const sources = record(course?.lesson_sources);
  return Object.entries(sources)
    .flatMap(([lectureId, value]) => {
      const list = Array.isArray(value) ? value : [];
      return list.flatMap((item) => {
        const path = typeof item === "string" ? item : text(record(item).path);
        return path ? [{ lectureId, lessonNumber: lessonNumberOf(lectureId), name: fileName(path) ?? path, path }] : [];
      });
    })
    .sort((a, b) => (a.lessonNumber ?? 0) - (b.lessonNumber ?? 0) || a.name.localeCompare(b.name));
}

export function courseFiles(course: CourseRecord | null): { handout: string | null; notebook: string | null } {
  const files = record(course?.files);
  return { handout: text(files.handout) || null, notebook: text(files.personal_notes) || null };
}
