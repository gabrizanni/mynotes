/**
 * Generate notes flow: pure view-model (unit-tested in model.test.ts). No strings here.
 *
 * - `flowView(job)`: which face the sheet shows (setup, live stepper, or an outcome).
 * - `outcomeOf(job)`: every backend outcome → one problem + one fix (UX-SPEC §3 outcome table).
 * - `stageTimeline(job, timings, now)`: StageStepper input, elapsed per stage, never a percentage.
 * - `sourceChecklist(plan, lectureIds)`: the transcript / slides / notebook rows from `notes.plan`.
 */
import { z } from "zod";

import type { Job, MethodResult } from "../../../rpc";
import type { StageTiming } from "../../../state/slices/jobs";

export const NOTES_STAGES = ["CHECKING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"] as const;
export type NotesStage = (typeof NOTES_STAGES)[number];
/** The stepper's last row; it has no timing of its own. */
export const DONE_STAGE = "DONE";

export type OutcomeKind =
  | "completed"
  | "warnings"
  | "needsTranscript"
  | "pausedQuota"
  | "pausedAuth"
  | "pausedNetwork"
  | "pausedSleep"
  | "awaitingApproval"
  | "conflict"
  | "needsReview"
  | "cancelled";

/** The one fix button of an outcome. */
export type OutcomeFix = "openWord" | "viewWarnings" | "transcribe" | "resume" | "signIn" | "openPreview" | "askAssistant";

/** At most one quieter action next to the fix. */
export type OutcomeSecondary = "approve" | "copyDetails" | "resume" | "undo" | null;

export interface Outcome {
  kind: OutcomeKind;
  /** Raw status for the StatusChip (`notes` table); never shown as text. */
  status: string;
  tone: "success" | "warning" | "danger";
  fix: OutcomeFix;
  secondary: OutcomeSecondary;
  /** Progress is kept and `--resume BATCH` finishes the same notes job. */
  resumable: boolean;
}

type OutcomeSpec = Omit<Outcome, "status" | "resumable"> & { resumable?: boolean };

const OUTCOMES: Record<string, OutcomeSpec> = {
  COMPLETED: { kind: "completed", tone: "success", fix: "openWord", secondary: "undo" },
  COMPLETED_WITH_WARNINGS: { kind: "warnings", tone: "warning", fix: "viewWarnings", secondary: null },
  NEEDS_TRANSCRIPT: { kind: "needsTranscript", tone: "warning", fix: "transcribe", secondary: null },
  PAUSED_QUOTA: { kind: "pausedQuota", tone: "warning", fix: "resume", secondary: null, resumable: true },
  PAUSED_AUTH: { kind: "pausedAuth", tone: "warning", fix: "signIn", secondary: "resume", resumable: true },
  PAUSED_NETWORK: { kind: "pausedNetwork", tone: "warning", fix: "resume", secondary: null, resumable: true },
  PAUSED_SLEEP: { kind: "pausedSleep", tone: "warning", fix: "resume", secondary: null, resumable: true },
  AWAITING_APPROVAL: { kind: "awaitingApproval", tone: "warning", fix: "openPreview", secondary: "approve" },
  PERSONAL_CONTENT_CONFLICT: { kind: "conflict", tone: "danger", fix: "askAssistant", secondary: null },
  NEEDS_REVIEW: { kind: "needsReview", tone: "danger", fix: "askAssistant", secondary: "copyDetails" },
  STOPPED: { kind: "needsReview", tone: "danger", fix: "askAssistant", secondary: "copyDetails" },
};

/** Statuses the outcome table covers, for tests and the gallery. */
export const OUTCOME_STATUSES = Object.keys(OUTCOMES);

function spec(status: string, resumable: boolean): Outcome {
  const found = OUTCOMES[status] ?? OUTCOMES.NEEDS_REVIEW;
  if (!found) throw new Error("outcome table is empty");
  const known = OUTCOMES[status] !== undefined;
  return { ...found, status: known ? status : "NEEDS_REVIEW", resumable: (found.resumable ?? false) && resumable };
}

/**
 * Outcome of a finished notes job; `null` while it runs. A job the student stopped keeps its
 * progress ("cancelled" → Resume); unknown statuses fall back to "needs a closer look".
 */
export function outcomeOf(job: Job): Outcome | null {
  if (job.state === "running") return null;
  const hasBatch = Boolean(job.batchId);
  const status = job.result?.status;
  if (job.state === "cancelled" && (!status || status === "STOPPED")) {
    return { kind: "cancelled", status: "CANCELLED", tone: "warning", fix: "resume", secondary: null, resumable: hasBatch };
  }
  if (status) return spec(status, hasBatch);
  if (job.state === "succeeded") return spec("COMPLETED", hasBatch);
  return spec("NEEDS_REVIEW", hasBatch);
}

export type FlowView = { view: "setup" } | { view: "running"; job: Job } | { view: "outcome"; job: Job; outcome: Outcome };

/** Which face the sheet shows for the lesson's latest notes job. */
export function flowView(job: Job | null | undefined): FlowView {
  if (!job) return { view: "setup" };
  const outcome = outcomeOf(job);
  return outcome ? { view: "outcome", job, outcome } : { view: "running", job };
}

export interface StageTimeline {
  /** Running stage id, or `"done"` once every stage finished. */
  current: string;
  timings: Record<string, { startedAt: string; finishedAt: string | null }>;
  /** Seconds since the job started (until it finished). */
  totalSeconds: number;
}

function isNotesStage(stage: string | null | undefined): stage is NotesStage {
  return NOTES_STAGES.some((candidate) => candidate === stage);
}

/**
 * StageStepper input. Timings come from the job's progress notifications; a stage the app did
 * not see start (the app opened mid-job) shows as done without a time rather than an invented one.
 */
export function stageTimeline(job: Job, history: readonly StageTiming[], now: Date): StageTimeline {
  const timings: StageTimeline["timings"] = {};
  for (const timing of history) {
    if (isNotesStage(timing.stage)) timings[timing.stage] = { startedAt: timing.startedAt, finishedAt: timing.finishedAt };
  }
  const end = job.finishedAt ? Date.parse(job.finishedAt) : now.getTime();
  const totalSeconds = Math.max(0, (end - Date.parse(job.startedAt)) / 1000);
  if (job.state === "succeeded" && (job.result?.status === "COMPLETED" || job.result?.status === "COMPLETED_WITH_WARNINGS" || !job.result)) {
    return { current: "done", timings, totalSeconds };
  }
  const current = isNotesStage(job.stage) ? job.stage : (history.filter((timing) => isNotesStage(timing.stage)).at(-1)?.stage ?? NOTES_STAGES[0]);
  if (!timings[current] && isNotesStage(current) && job.stageStartedAt) {
    timings[current] = { startedAt: job.stageStartedAt, finishedAt: null };
  }
  return { current, timings, totalSeconds };
}

// ---------------------------------------------------------------------------------------------
// Sources (notes.plan)
// ---------------------------------------------------------------------------------------------

export interface SourceRow {
  lectureId: string;
  lessonNumber: number | null;
  transcript: boolean;
  slides: string[];
  notebookSection: string | null;
}

type Plan = MethodResult<"notes.plan">["data"];

export function lessonNumberOf(lectureId: string): number | null {
  const match = /-L(\d{1,3})$/.exec(lectureId);
  return match?.[1] ? Number(match[1]) : null;
}

/** One row per requested lesson, in the requested order; lessons the plan misses have no sources. */
export function sourceChecklist(plan: Plan | null, lectureIds: readonly string[]): SourceRow[] {
  const byId = new Map((plan?.lessons ?? []).map((lesson) => [lesson.lecture_id, lesson]));
  return lectureIds.map((lectureId) => {
    const entry = byId.get(lectureId);
    return {
      lectureId,
      lessonNumber: lessonNumberOf(lectureId),
      transcript: entry?.transcript === true,
      slides: entry?.slides ?? [],
      notebookSection: entry?.notebook_section ?? null,
    };
  });
}

/** Lessons that still need a transcript before notes can start. */
export function missingTranscripts(rows: readonly SourceRow[]): SourceRow[] {
  return rows.filter((row) => !row.transcript);
}

// ---------------------------------------------------------------------------------------------
// Warnings and details
// ---------------------------------------------------------------------------------------------

const warningSchema = z.union([z.string(), z.looseObject({ message: z.string() }), z.looseObject({ detail: z.string() })]);
const resultWarningsSchema = z.looseObject({ warnings: z.array(z.unknown()).optional() });

/** Human warning lines from `job.result.warnings` or `batch-status` (strings or `{message}`). */
export function warningsOf(...sources: unknown[]): string[] {
  const lines: string[] = [];
  for (const source of sources) {
    const parsed = resultWarningsSchema.safeParse(source ?? {});
    for (const raw of parsed.success ? (parsed.data.warnings ?? []) : []) {
      const warning = warningSchema.safeParse(raw);
      if (!warning.success) continue;
      const value = warning.data;
      const candidate: unknown = typeof value === "string" ? value : (value.message ?? value.detail);
      const text = typeof candidate === "string" ? candidate.trim() : "";
      if (text && !lines.includes(text)) lines.push(text);
    }
  }
  return lines;
}

/** "Copy details": codes and ids, which never appear in the UI itself (UX-SPEC §5). */
export function jobDetails(job: Job): string {
  const lines = [
    `job: ${job.jobId}`,
    `state: ${job.state}`,
    job.result?.status ? `status: ${job.result.status}` : null,
    job.batchId ? `batch: ${job.batchId}` : null,
    job.exitCode !== null && job.exitCode !== undefined ? `exit code: ${job.exitCode}` : null,
    job.stage ? `stage: ${job.stage}` : null,
    job.courseCode ? `course: ${job.courseCode}` : null,
    job.lectureIds?.length ? `lessons: ${job.lectureIds.join(", ")}` : null,
    `started: ${job.startedAt}`,
    job.finishedAt ? `finished: ${job.finishedAt}` : null,
  ];
  return lines.filter((line): line is string => line !== null).join("\n");
}

/** Lesson numbers of a notes job, sorted, for "Lesson 7" / "Lessons 5–7". */
export function jobLessonNumbers(job: Pick<Job, "lectureIds">): number[] {
  return (job.lectureIds ?? [])
    .map(lessonNumberOf)
    .filter((n): n is number => n !== null)
    .sort((a, b) => a - b);
}

/** Latest notes job that covers one lesson (jobs are newest first). */
export function latestNotesJob(jobs: readonly Job[], lectureId: string): Job | null {
  return jobs.find((job) => (job.kind === "notes" || job.kind === "lesson") && (job.lectureIds ?? []).includes(lectureId)) ?? null;
}

/** The parameters `notes.run` gets from the sheet's options. */
export interface GenerateOptions {
  quality: "standard" | "approfondito" | "revisione";
  instructions: string;
  /** `null`: keep the course's saved image limit. */
  imageLimit: number | null;
  focusLecturer: boolean;
  contextLessons: string;
}

export const DEFAULT_OPTIONS: GenerateOptions = { quality: "standard", instructions: "", imageLimit: null, focusLecturer: false, contextLessons: "" };

/** Lesson numbers typed by a student ("4, 5" or "2-4") → the CLI's `--context-lessons` value. */
export function normalizeLessonList(text: string): string | null {
  const parts = text
    .split(/[\s,;]+/)
    .map((part) => part.trim().replace(/[–—]/g, "-"))
    .filter(Boolean);
  if (parts.length === 0) return null;
  if (!parts.every((part) => /^\d{1,3}(-\d{1,3})?$/.test(part))) return null;
  return parts.join(",");
}

export function runParams(course: string, lessonNumbers: readonly number[], options: GenerateOptions) {
  const context = normalizeLessonList(options.contextLessons);
  return {
    course,
    lessons: lessonNumbers.join(","),
    quality: options.quality,
    ...(options.instructions.trim() ? { instructions: options.instructions.trim() } : {}),
    ...(options.imageLimit !== null ? { imageLimit: options.imageLimit } : {}),
    ...(options.focusLecturer ? { speakerFocus: "professor" as const } : {}),
    ...(context ? { contextLessons: context } : {}),
  };
}
