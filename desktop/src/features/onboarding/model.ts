/**
 * Onboarding view-model (UX-SPEC §2): pure functions only, no React, no RPC.
 *
 * - Step machine: 13 named steps in order, grouped in three header segments.
 * - Record: the app's own `onboarding.json` (answers kept as a draft, step marks, what was saved).
 * - Draft queue: answers are saved in order once the `mynotes` command exists.
 * - Resume: the first incomplete required step, else where the student left off.
 * - Validation and small mappers for setup progress, sign-in, folders, calendars and imports.
 */
import { isLang, type Lang } from "../../i18n/lang";
import { WELCOME_STEPS, type WelcomeStep } from "../../state/routes";

export type StepId = WelcomeStep;
export type Segment = "setup" | "course" | "recording";

export const PROVIDERS = ["claude", "codex", "agy"] as const;
export type Provider = (typeof PROVIDERS)[number];

export function isProvider(value: unknown): value is Provider {
  return typeof value === "string" && (PROVIDERS as readonly string[]).includes(value);
}

export interface StepSpec {
  id: StepId;
  segment: Segment;
  /** Resume opens the first required step that is not done (UX-SPEC §2: 1, 2, 4, 6, 7, 8). */
  required: boolean;
  /** Offers Skip / Do this later / Not now; skipped steps become "Finish later". */
  skippable: boolean;
  /** Needs the installed `mynotes` command and every drafted answer saved first. */
  needsBackend: boolean;
}

export const STEPS: readonly StepSpec[] = [
  { id: "language", segment: "setup", required: true, skippable: false, needsBackend: false },
  { id: "assistant", segment: "setup", required: true, skippable: false, needsBackend: false },
  { id: "sign-in", segment: "setup", required: false, skippable: true, needsBackend: false },
  { id: "privacy", segment: "setup", required: true, skippable: false, needsBackend: false },
  { id: "studies", segment: "course", required: false, skippable: true, needsBackend: false },
  { id: "course", segment: "course", required: true, skippable: false, needsBackend: false },
  { id: "style", segment: "course", required: true, skippable: false, needsBackend: false },
  { id: "files", segment: "course", required: true, skippable: false, needsBackend: true },
  { id: "schedule", segment: "recording", required: false, skippable: true, needsBackend: true },
  { id: "microphone", segment: "recording", required: false, skippable: true, needsBackend: true },
  { id: "automatic", segment: "recording", required: false, skippable: true, needsBackend: true },
  { id: "first-notes", segment: "recording", required: false, skippable: true, needsBackend: true },
  { id: "ready", segment: "recording", required: false, skippable: false, needsBackend: false },
];

export const SEGMENTS: readonly Segment[] = ["setup", "course", "recording"];

export function stepSpec(id: StepId): StepSpec {
  const spec = STEPS.find((step) => step.id === id);
  if (!spec) throw new Error(`Unknown onboarding step ${id}`);
  return spec;
}

export function stepIndex(id: StepId): number {
  return WELCOME_STEPS.indexOf(id);
}

export function nextStepId(id: StepId): StepId | null {
  return WELCOME_STEPS[stepIndex(id) + 1] ?? null;
}

export function prevStepId(id: StepId): StepId | null {
  const index = stepIndex(id);
  return index > 0 ? (WELCOME_STEPS[index - 1] ?? null) : null;
}

// ---------------------------------------------------------------------------------------------
// Draft answers
// ---------------------------------------------------------------------------------------------

export const GOALS = ["exam", "understanding", "exercises", "work", "personal"] as const;
export type Goal = (typeof GOALS)[number];

/** Spec labels → backend `kind` (configuration.COURSE_KINDS). */
export const COURSE_KINDS = ["theoretical", "quantitative", "cases", "programming", "mixed"] as const;
export type CourseKind = (typeof COURSE_KINDS)[number];

export const LECTURE_LANGUAGES = ["en", "it", "fr", "de", "es"] as const;
export type LectureLanguage = (typeof LECTURE_LANGUAGES)[number];

export interface StudiesDraft {
  background: string;
  goals: Goal[];
}

export interface CourseDraft {
  name: string;
  lectureLanguage: LectureLanguage;
  notesLanguage: Lang;
  kind: CourseKind;
  examFormat: string;
  examUnknown: boolean;
  hardTopics: string[];
  anythingElse: string;
}

export const DETAILS = ["concise", "clear", "detailed"] as const;
export const LAYOUTS = ["prose", "balanced", "lists"] as const;
export const IMAGE_POLICIES = ["none", "essential", "explained"] as const;
export const RECAP_LANGUAGES = ["same", "en", "it"] as const;

export interface StyleDraft {
  detail: (typeof DETAILS)[number];
  layout: (typeof LAYOUTS)[number];
  images: (typeof IMAGE_POLICIES)[number];
  /** Max images per lesson; 0 means "no limit" is not offered: the stepper runs 1–8. */
  imageLimit: number;
  recap: boolean;
  recapLanguage: (typeof RECAP_LANGUAGES)[number];
  /** Review questions per lesson, 1–5. */
  questions: number;
}

export const IMAGE_LIMIT = { min: 1, max: 8 } as const;
export const QUESTIONS = { min: 1, max: 5 } as const;
export const COURSE_NAME_MAX = 80;

export function defaultCourse(lang: Lang): CourseDraft {
  return {
    name: "",
    lectureLanguage: lang,
    notesLanguage: lang,
    kind: "mixed",
    examFormat: "",
    examUnknown: false,
    hardTopics: [],
    anythingElse: "",
  };
}

/** Backend defaults (configuration.STYLE_PROFILE_DEFAULTS): clear study notes, balanced, essential images. */
export const DEFAULT_STYLE: StyleDraft = {
  detail: "clear",
  layout: "balanced",
  images: "essential",
  imageLimit: 4,
  recap: true,
  recapLanguage: "same",
  questions: 5,
};

export type StepMark = "done" | "skipped";
export type OpId = "language" | "profile" | "course";

/** The app's own onboarding.json. Answers live here until the backend saves them. */
export interface OnboardingRecord {
  version: 1;
  language: Lang | null;
  provider: Provider | null;
  studies: StudiesDraft | null;
  course: CourseDraft | null;
  style: StyleDraft | null;
  marks: Partial<Record<StepId, StepMark>>;
  /** Fingerprint of the payload each operation last saved. */
  saved: Partial<Record<OpId, string>>;
  /** Code the backend gave the first course (`course.add`). */
  courseCode: string | null;
  /** Folder chosen on "Where are your course files?", kept so the preview survives a restart. */
  folder: { path: string; reviewFirst: boolean } | null;
  /** Calendar connected on "Add your class schedule" (`capture.connect` source id). */
  calendar: { url: string; sourceID: string } | null;
  /** When the last microphone test was requested (epoch ms), to find its recording. */
  micTestAt: number | null;
  /** Set by "Open MyNotes" on the last step: launch no longer opens onboarding. */
  finished: boolean;
}

export const EMPTY_RECORD: OnboardingRecord = {
  version: 1,
  language: null,
  provider: null,
  studies: null,
  course: null,
  style: null,
  marks: {},
  saved: {},
  courseCode: null,
  folder: null,
  calendar: null,
  micTestAt: null,
  finished: false,
};

export function markStep(record: OnboardingRecord, step: StepId, mark: StepMark): OnboardingRecord {
  if (mark === "skipped" && !stepSpec(step).skippable) return record;
  if (record.marks[step] === mark) return record;
  return { ...record, marks: { ...record.marks, [step]: mark } };
}

// ---------------------------------------------------------------------------------------------
// Backend progress (config show, doctor) and resume
// ---------------------------------------------------------------------------------------------

export interface BackendProgress {
  /** `config.onboarding.status === "complete"` (an existing MyNotes install). */
  complete: boolean;
  /** `onboarding.completed_groups`: personal · paths · courses · style · providers. */
  completedGroups: readonly string[];
  /** `doctor --json` checks.agent.status === "ready". */
  agentReady: boolean;
}

export const NO_BACKEND: BackendProgress = { complete: false, completedGroups: [], agentReady: false };

const GROUP_FOR_STEP: Partial<Record<StepId, string>> = {
  assistant: "providers",
  studies: "personal",
  course: "courses",
  style: "style",
  files: "paths",
};

export type StepState = "done" | "skipped" | "todo";

export function stepState(record: OnboardingRecord, backend: BackendProgress, step: StepId): StepState {
  const mark = record.marks[step];
  if (mark) return mark;
  const group = GROUP_FOR_STEP[step];
  if (group && backend.completedGroups.includes(group)) return "done";
  if (step === "sign-in" && backend.agentReady) return "done";
  if (backend.complete && stepSpec(step).required) return "done";
  return "todo";
}

/** Where launch opens onboarding, or `null` when it is finished. */
export function resumeStep(record: OnboardingRecord, backend: BackendProgress): StepId | null {
  if (record.finished) return null;
  const firstRequired = STEPS.find((step) => step.required && stepState(record, backend, step.id) === "todo");
  if (firstRequired) return firstRequired.id;
  let furthest = -1;
  STEPS.forEach((step, index) => {
    if (record.marks[step.id]) furthest = index;
  });
  const after = STEPS.slice(furthest + 1).find((step) => stepState(record, backend, step.id) === "todo");
  return after?.id ?? "ready";
}

/** Whether launch should leave Today and open onboarding. */
export function shouldOpenOnboarding(record: OnboardingRecord, backend: BackendProgress): boolean {
  return !record.finished && !backend.complete;
}

/** A step that cannot be shown yet redirects here (deep links, stale URLs). `null` = show it. */
export function redirectFor(step: StepId, record: OnboardingRecord, backend: BackendProgress, hasCourse: boolean): StepId | null {
  const needsProvider: readonly StepId[] = ["sign-in", "privacy"];
  const needsCourse: readonly StepId[] = ["style", "files", "schedule", "first-notes"];
  if (needsProvider.includes(step) && !record.provider && !backend.complete) return resumeStep(record, backend) ?? "assistant";
  if (needsCourse.includes(step) && !record.course && !hasCourse) return resumeStep(record, backend) ?? "course";
  return null;
}

export type SegmentState = "done" | "current" | "upcoming";

export function segmentStates(step: StepId): Record<Segment, SegmentState> {
  const current = SEGMENTS.indexOf(stepSpec(step).segment);
  const last = step === "ready";
  return {
    setup: segmentState(0, current, last),
    course: segmentState(1, current, last),
    recording: segmentState(2, current, last),
  };
}

function segmentState(index: number, current: number, last: boolean): SegmentState {
  if (last || index < current) return "done";
  return index === current ? "current" : "upcoming";
}

// ---------------------------------------------------------------------------------------------
// Draft queue: saved in order once the command is installed
// ---------------------------------------------------------------------------------------------

export interface CourseSettings {
  kind: CourseKind;
  languages: { lesson: LectureLanguage; notes: Lang };
  assessment: string;
  difficult_topics: string[];
  style: {
    writing_style: "concise" | "clear_study" | "detailed";
    list_density: "prose_first" | "balanced" | "list_first";
    image_policy: "none" | "essential" | "explanatory";
    image_limit: number | null;
    question_count: number;
    recap: boolean;
    recap_language: "same_as_notes" | "en" | "it";
  };
}

export interface CoursePayload {
  name: string;
  notesLanguage: Lang;
  settingsJson: CourseSettings;
  contextAnswer?: string;
}

export type SaveOp =
  | { id: "language"; fingerprint: string; language: Lang }
  | { id: "profile"; fingerprint: string; background: string | null }
  | { id: "course"; fingerprint: string; mode: "add" | "edit"; code: string | null; payload: CoursePayload };

const WRITING: Record<StyleDraft["detail"], CourseSettings["style"]["writing_style"]> = { concise: "concise", clear: "clear_study", detailed: "detailed" };
const DENSITY: Record<StyleDraft["layout"], CourseSettings["style"]["list_density"]> = { prose: "prose_first", balanced: "balanced", lists: "list_first" };
const IMAGES: Record<StyleDraft["images"], CourseSettings["style"]["image_policy"]> = { none: "none", essential: "essential", explained: "explanatory" };
const RECAP: Record<StyleDraft["recapLanguage"], CourseSettings["style"]["recap_language"]> = { same: "same_as_notes", en: "en", it: "it" };

export function coursePayload(course: CourseDraft, style: StyleDraft): CoursePayload {
  const context = course.anythingElse.trim();
  return {
    name: course.name.trim(),
    notesLanguage: course.notesLanguage,
    settingsJson: {
      kind: course.kind,
      languages: { lesson: course.lectureLanguage, notes: course.notesLanguage },
      assessment: course.examUnknown ? "" : course.examFormat.trim(),
      difficult_topics: course.hardTopics,
      style: {
        writing_style: WRITING[style.detail],
        list_density: DENSITY[style.layout],
        image_policy: IMAGES[style.images],
        image_limit: style.images === "none" ? null : style.imageLimit,
        question_count: style.questions,
        recap: style.recap,
        recap_language: RECAP[style.recapLanguage],
      },
    },
    ...(context ? { contextAnswer: context } : {}),
  };
}

/** One free-form background answer (`profile --background`), goals first in the student's words. */
export function backgroundText(studies: StudiesDraft, goalLabel: (goal: Goal) => string, goalsPrefix: string): string {
  const text = studies.background.trim();
  if (studies.goals.length === 0) return text;
  const goals = `${goalsPrefix} ${studies.goals.map(goalLabel).join(", ")}.`;
  return text ? `${text}\n\n${goals}` : goals;
}

export interface QueueLabels {
  goalLabel: (goal: Goal) => string;
  goalsPrefix: string;
}

/** Operations whose answers are final and not yet saved, in the order they must run. */
export function pendingOps(record: OnboardingRecord, labels: QueueLabels): SaveOp[] {
  const ops: SaveOp[] = [];
  if (record.language && record.marks.language === "done") {
    const fingerprint = `language:${record.language}`;
    if (record.saved.language !== fingerprint) ops.push({ id: "language", fingerprint, language: record.language });
  }
  const studiesMark = record.marks.studies;
  if (studiesMark) {
    const background = studiesMark === "done" && record.studies ? backgroundText(record.studies, labels.goalLabel, labels.goalsPrefix) : null;
    const fingerprint = `profile:${background === null ? "skip" : JSON.stringify(background)}`;
    if (record.saved.profile !== fingerprint) ops.push({ id: "profile", fingerprint, background: background || null });
  }
  if (record.course && record.style && record.marks.course === "done" && record.marks.style === "done") {
    const payload = coursePayload(record.course, record.style);
    const fingerprint = `course:${JSON.stringify(payload)}`;
    if (record.saved.course !== fingerprint) {
      ops.push({ id: "course", fingerprint, mode: record.courseCode ? "edit" : "add", code: record.courseCode, payload });
    }
  }
  return ops;
}

export function applySaved(record: OnboardingRecord, op: SaveOp, courseCode?: string | null): OnboardingRecord {
  return {
    ...record,
    saved: { ...record.saved, [op.id]: op.fingerprint },
    courseCode: op.id === "course" && courseCode ? courseCode : record.courseCode,
  };
}

/** Labels of the "Almost there" checklist, in queue order (done, running or waiting). */
export function draftChecklist(record: OnboardingRecord, labels: QueueLabels, running: OpId | null): Array<{ id: OpId; state: "done" | "running" | "waiting" }> {
  const pending = new Set(pendingOps(record, labels).map((op) => op.id));
  const relevant: OpId[] = [];
  if (record.marks.language === "done") relevant.push("language");
  if (record.marks.studies) relevant.push("profile");
  if (record.marks.course === "done" && record.marks.style === "done") relevant.push("course");
  return relevant.map((id) => ({ id, state: id === running ? "running" : pending.has(id) ? "waiting" : "done" }));
}

// ---------------------------------------------------------------------------------------------
// Setup progress (setup.status) → header and checklist
// ---------------------------------------------------------------------------------------------

export interface SetupComponent {
  id: string;
  title: string;
  /** waiting · running · done */
  state: string;
  bytesDone: number | null;
  bytesTotal: number | null;
}

export interface SetupDetail {
  /** idle · running · paused · failed · complete */
  state: string;
  stage: string | null;
  title: string | null;
  bytesDone: number | null;
  bytesTotal: number | null;
  lastDataAt: string | null;
  error: string | null;
  overallDone: number | null;
  overallTotal: number | null;
  components: SetupComponent[];
}

export type SetupPhase = "idle" | "running" | "waiting" | "failed" | "complete";

export interface SetupView {
  phase: SetupPhase;
  /** Measured share of all downloads, 0–99 while running; `null` when nothing is measured. */
  percent: number | null;
  /** installer stage: components · installation · workspace · verification · ready */
  stage: string | null;
  stageTitle: string | null;
  bytesDone: number | null;
  bytesTotal: number | null;
  overallDone: number | null;
  overallTotal: number | null;
  secondsSinceData: number | null;
  error: string | null;
  components: SetupComponent[];
}

/** UX-SPEC §2: after 60 s with no data, "Waiting for network". */
export const NETWORK_WAIT_SECONDS = 60;

export function setupView(status: SetupDetail | null, now: Date): SetupView {
  const base: SetupView = {
    phase: "idle",
    percent: null,
    stage: null,
    stageTitle: null,
    bytesDone: null,
    bytesTotal: null,
    overallDone: null,
    overallTotal: null,
    secondsSinceData: null,
    error: null,
    components: [],
  };
  if (!status) return base;
  const lastData = status.lastDataAt ? Date.parse(status.lastDataAt) : Number.NaN;
  const secondsSinceData = Number.isFinite(lastData) ? Math.max(0, Math.floor((now.getTime() - lastData) / 1000)) : null;
  const measured = status.overallTotal !== null && status.overallTotal > 0 && status.overallDone !== null;
  const view: SetupView = {
    ...base,
    stage: status.stage,
    stageTitle: status.title,
    bytesDone: status.bytesDone,
    bytesTotal: status.bytesTotal,
    overallDone: status.overallDone,
    overallTotal: status.overallTotal,
    secondsSinceData,
    error: status.error,
    components: status.components,
  };
  switch (status.state) {
    case "complete":
      return { ...view, phase: "complete", percent: 100 };
    case "failed":
    case "paused":
      return { ...view, phase: "failed", percent: measured ? percentOf(status.overallDone ?? 0, status.overallTotal ?? 1) : null };
    case "running": {
      const percent = measured ? percentOf(status.overallDone ?? 0, status.overallTotal ?? 1) : null;
      const waiting = secondsSinceData !== null && secondsSinceData >= NETWORK_WAIT_SECONDS;
      return { ...view, phase: waiting ? "waiting" : "running", percent };
    }
    default:
      return base;
  }
}

function percentOf(done: number, total: number): number {
  return Math.min(99, Math.max(0, Math.floor((done / total) * 100)));
}

/** "212 / 300 MB" in decimal megabytes, like the download sizes in setup-components.json. */
export function megabytes(bytes: number): number {
  return Math.round(bytes / 1_000_000);
}

export function commandReady(view: SetupView): boolean {
  return view.phase === "complete";
}

/** Steps that need the backend show "Almost there" until setup and the draft queue are done. */
export function mustWait(step: StepId, view: SetupView, pending: number): boolean {
  return stepSpec(step).needsBackend && (!commandReady(view) || pending > 0);
}

// ---------------------------------------------------------------------------------------------
// Sign-in (provider.status, provider.login)
// ---------------------------------------------------------------------------------------------

export type SignInPhase = "checking" | "missing" | "incompatible" | "login_required" | "unverified" | "waiting" | "timeout" | "ready";

export const LOGIN_POLL_MS = 3000;
export const LOGIN_TIMEOUT_MS = 5 * 60_000;
export const AUTO_ADVANCE_MS = 800;

export function signInPhase(status: string | null, loginStartedAt: number | null, now: number): SignInPhase {
  if (status === null) return "checking";
  if (status === "ready") return "ready";
  if (loginStartedAt !== null) {
    if (now - loginStartedAt >= LOGIN_TIMEOUT_MS) return "timeout";
    if (status === "login_required" || status === "unverified") return "waiting";
  }
  if (status === "missing" || status === "incompatible" || status === "unverified" || status === "login_required") return status;
  return "login_required";
}

// ---------------------------------------------------------------------------------------------
// Course form and calendar link validation
// ---------------------------------------------------------------------------------------------

export interface CourseErrors {
  name?: "required" | "tooLong";
}

export function validateCourse(course: CourseDraft): CourseErrors {
  const name = course.name.trim();
  if (!name) return { name: "required" };
  if (name.length > COURSE_NAME_MAX) return { name: "tooLong" };
  return {};
}

export type CalendarUrlError = "empty" | "scheme" | "credentials" | "invalid";

/** HTTPS or webcal only, no credentials in the URL (capture_links INVALID_URL rules). */
export function validateCalendarUrl(raw: string): CalendarUrlError | null {
  const value = raw.trim();
  if (!value) return "empty";
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return "invalid";
  }
  if (url.protocol !== "https:" && url.protocol !== "webcal:") return "scheme";
  if (url.username || url.password) return "credentials";
  if (!url.hostname) return "invalid";
  return null;
}

/** capture_links codes → one problem and one fix (UX-SPEC §2 Errors). */
export const CALENDAR_CODES = [
  "INVALID_URL",
  "GOOGLE_MULTIPLE_CALENDARS",
  "GOOGLE_LINK_UNSUPPORTED",
  "WEBPAGE_NOT_CALENDAR",
  "GOOGLE_FEED_UNAVAILABLE",
  "ACCESS_DENIED",
  "FEED_UNAVAILABLE",
  "NETWORK_ERROR",
  "TIMEOUT",
  "TLS_ERROR",
  "INVALID_CALENDAR",
  "FEED_TOO_LARGE",
  "INSECURE_REDIRECT",
  "RECORDER_INCOMPATIBLE",
  "SOURCE_CHANGED",
  "INVALID_SOURCE",
  "INTERNAL_ERROR",
] as const;
export type CalendarCode = (typeof CALENDAR_CODES)[number];
export type CalendarFix = "editLink" | "showGuide" | "retry";

const CALENDAR_FIX: Record<CalendarCode, CalendarFix> = {
  INVALID_URL: "editLink",
  GOOGLE_MULTIPLE_CALENDARS: "showGuide",
  GOOGLE_LINK_UNSUPPORTED: "showGuide",
  WEBPAGE_NOT_CALENDAR: "showGuide",
  GOOGLE_FEED_UNAVAILABLE: "showGuide",
  ACCESS_DENIED: "showGuide",
  FEED_UNAVAILABLE: "editLink",
  NETWORK_ERROR: "retry",
  TIMEOUT: "retry",
  TLS_ERROR: "retry",
  INVALID_CALENDAR: "showGuide",
  FEED_TOO_LARGE: "editLink",
  INSECURE_REDIRECT: "editLink",
  RECORDER_INCOMPATIBLE: "retry",
  SOURCE_CHANGED: "editLink",
  INVALID_SOURCE: "editLink",
  INTERNAL_ERROR: "retry",
};

export function calendarProblem(code: string | null | undefined): { code: CalendarCode; fix: CalendarFix } {
  const known = CALENDAR_CODES.find((candidate) => candidate === code) ?? "INTERNAL_ERROR";
  return { code: known, fix: CALENDAR_FIX[known] };
}

export interface PreviewEvent {
  sourceID: string;
  courseCode: string | null;
  eligibility: string;
}

export interface SchedulePreview {
  lessons: number;
  clashes: number;
  needCourse: number;
}

export function schedulePreview(events: readonly PreviewEvent[], sourceID: string): SchedulePreview {
  const mine = events.filter((event) => event.sourceID === sourceID && event.eligibility !== "CANCELLED");
  return {
    lessons: mine.length,
    clashes: mine.filter((event) => event.eligibility === "CONFLICT").length,
    needCourse: mine.filter((event) => event.courseCode === null).length,
  };
}

/** Default period: today to the end of the current semester (31 Jan or 30 Jun / 31 Jul). */
export function defaultPeriod(now: Date): { start: string; end: string } {
  const year = now.getFullYear();
  const month = now.getMonth();
  const end = month >= 8 ? new Date(Date.UTC(year + 1, 0, 31)) : month <= 0 ? new Date(Date.UTC(year, 0, 31)) : new Date(Date.UTC(year, 6, 31));
  const pad = (value: number) => String(value).padStart(2, "0");
  const start = `${year}-${pad(month + 1)}-${pad(now.getDate())}`;
  return { start, end: end.toISOString().slice(0, 10) };
}

// ---------------------------------------------------------------------------------------------
// Course folder preview (course.link --dry-run)
// ---------------------------------------------------------------------------------------------

export type SelectionRole = "notes_file" | "personal_notes" | "recordings";
export const SELECTION_PARAM: Record<SelectionRole, "notesFile" | "notes" | "recordings"> = {
  notes_file: "notesFile",
  personal_notes: "notes",
  recordings: "recordings",
};

export interface FolderPreview {
  status: "ready" | "selection" | "other";
  materials: number;
  recordings: number;
  notebook: { name: string; isNew: boolean } | null;
  courseNotes: string | null;
  question: { role: SelectionRole; options: string[] } | null;
}

export interface LinkResultLike {
  status: string;
  proposed_files?: { personal_notes?: string | null; handout?: string | null } | null;
  note_files?: { personal_notes?: string | null; handout?: string | null } | null;
  ambiguities?: Partial<Record<string, string[]>> | null;
  inventory?: { file_counts?: Partial<Record<string, number>> | null; files?: Partial<Record<string, string[]>> | null } | null;
}

export function baseName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

export function folderPreview(result: LinkResultLike): FolderPreview {
  const counts = result.inventory?.file_counts ?? {};
  const files = result.inventory?.files ?? {};
  const planned = result.note_files ?? result.proposed_files ?? {};
  const notebookPath = planned.personal_notes ?? null;
  const existingNotes = files.personal_notes ?? [];
  const roles: SelectionRole[] = ["notes_file", "personal_notes", "recordings"];
  const role = roles.find((candidate) => (result.ambiguities?.[candidate]?.length ?? 0) > 0) ?? null;
  return {
    status: result.status === "READY" || result.status === "LINKED" ? "ready" : result.status === "NEEDS_SELECTION" ? "selection" : "other",
    materials: counts.materials ?? 0,
    recordings: counts.recordings ?? 0,
    notebook: notebookPath ? { name: baseName(notebookPath), isNew: !existingNotes.includes(notebookPath) } : null,
    courseNotes: planned.handout ? baseName(planned.handout) : null,
    question: role ? { role, options: result.ambiguities?.[role] ?? [] } : null,
  };
}

// ---------------------------------------------------------------------------------------------
// Microphone test and first notes
// ---------------------------------------------------------------------------------------------

/** `capture test` records a fixed 60 s sample (UX-SPEC §7 gap 5). */
export const MIC_TEST_SECONDS = 60;

export interface CaptureSessionLike {
  id: string;
  status: string;
  outputPath?: string | null;
  actualStart?: string | null;
  isTest?: unknown;
}

export type MicPhase = "intro" | "denied" | "starting" | "recording" | "saving" | "recorded";

export function micPhase(input: {
  permission: string | null | undefined;
  requested: boolean;
  current: CaptureSessionLike | null | undefined;
  exporting: boolean;
  sessions: readonly CaptureSessionLike[];
  requestedAt: number | null;
}): { phase: MicPhase; audio: string | null; startedAt: string | null } {
  if (input.permission === "DENIED" || input.permission === "RESTRICTED") return { phase: "denied", audio: null, startedAt: null };
  if (!input.requested) return { phase: "intro", audio: null, startedAt: null };
  const current = input.current;
  if (current && current.isTest === true && current.status === "RECORDING") return { phase: "recording", audio: null, startedAt: current.actualStart ?? null };
  if (input.exporting) return { phase: "saving", audio: null, startedAt: null };
  const done = [...input.sessions]
    .reverse()
    .find((session) => session.isTest === true && session.status === "COMPLETED" && session.outputPath && (input.requestedAt === null || Date.parse(session.actualStart ?? "") >= input.requestedAt - 120_000));
  if (done?.outputPath) return { phase: "recorded", audio: done.outputPath, startedAt: done.actualStart ?? null };
  return { phase: "starting", audio: null, startedAt: null };
}

/** ⌥⇧R / Alt+Shift+R, matched on the physical key so layouts with dead keys still work. */
export function isRecordShortcut(event: { altKey: boolean; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean; code: string }): boolean {
  return event.altKey && event.shiftKey && !event.ctrlKey && !event.metaKey && event.code === "KeyR";
}

const AUDIO = /\.(m4a|mp3|wav|aac|flac|ogg|opus|webm|mp4|mov)$/i;
const TRANSCRIPT = /\.(txt)$/i;

export function classifyImport(name: string): "audio" | "transcript" | null {
  if (AUDIO.test(name)) return "audio";
  if (TRANSCRIPT.test(name)) return "transcript";
  return null;
}

export function lectureIdFor(code: string, lesson = 1): string {
  return `${code}-L${String(lesson).padStart(2, "0")}`;
}

// ---------------------------------------------------------------------------------------------
// "You're ready" summary
// ---------------------------------------------------------------------------------------------

export const SUMMARY_STEPS: readonly StepId[] = ["sign-in", "studies", "course", "style", "files", "schedule", "microphone", "automatic", "first-notes"];

export function readySummary(record: OnboardingRecord, backend: BackendProgress): Array<{ step: StepId; state: "done" | "later" }> {
  return SUMMARY_STEPS.map((step) => ({ step, state: stepState(record, backend, step) === "done" ? "done" : "later" }));
}

// ---------------------------------------------------------------------------------------------
// Keyboard: Enter = primary action, Esc = back where safe
// ---------------------------------------------------------------------------------------------

export interface KeyTarget {
  tagName: string;
  type?: string;
  isContentEditable?: boolean;
  value?: string;
  insideDialog: boolean;
}

export type KeyIntent = "primary" | "back" | null;

/** What a window-level key press means on an onboarding step. */
export function keyIntent(key: string, target: KeyTarget, options: { defaultPrevented: boolean; escapeBack: boolean; composing?: boolean }): KeyIntent {
  if (options.defaultPrevented || options.composing) return null;
  const tag = target.tagName.toUpperCase();
  const editable = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target.isContentEditable === true;
  if (key === "Escape") {
    if (!options.escapeBack || target.insideDialog) return null;
    if (editable && (target.value ?? "") !== "") return null;
    return "back";
  }
  if (key === "Enter") {
    // Buttons, links, text areas and selects keep their own Enter; text inputs submit the form natively.
    if (tag === "BUTTON" || tag === "A" || tag === "TEXTAREA" || tag === "SELECT" || tag === "INPUT" || target.isContentEditable) return null;
    if (target.insideDialog) return null;
    return "primary";
  }
  return null;
}

export function langOrNull(value: unknown): Lang | null {
  return isLang(value) ? value : null;
}
