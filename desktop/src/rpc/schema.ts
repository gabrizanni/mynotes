/**
 * Zod schemas for every RPC method and notification the app uses (protocol 1).
 *
 * Rules:
 * - Shapes follow the real backend: `capture dashboard --json` (camelCase, capture_dashboard.py),
 *   `transcription-queue status|library --json` (snake_case, transcription_queue.py),
 *   `config show --json`, `batch-status --json` (batch_recovery.py).
 * - Objects are `z.looseObject`: fields the app does not model pass through untouched, so a
 *   newer backend never breaks parsing. Status values stay `z.string()` for the same reason;
 *   the UI maps the statuses it knows (see components/StatusChip/statusMap.ts).
 * - CLI-backed methods answer `{ exitCode, data }`, because exit codes 1–3 can still carry JSON.
 *
 * To add a method: add its params and result here, add a fixture
 * `tests/fixtures/rpc/<method>.json`, then call it with `rpc.call("<method>", params)`.
 */
import { z } from "zod";

const str = z.string();
const optStr = z.string().nullable().optional();
const optNum = z.number().nullable().optional();
const optBool = z.boolean().nullable().optional();

/** `{ exitCode, data }` envelope of every CLI-backed method. */
export function cli<T extends z.ZodType>(data: T) {
  return z.object({ exitCode: z.number().int(), data });
}

// ---------------------------------------------------------------------------------------------
// system, session, watch
// ---------------------------------------------------------------------------------------------

export const helloSchema = z.looseObject({
  protocol: z.number().int(),
  methods: z.array(str).optional(),
  versions: z.looseObject({}).optional(),
  platform: str.optional(),
});

export const sessionSchema = z.looseObject({ sessionId: str });

// ---------------------------------------------------------------------------------------------
// capture (camelCase, capture_dashboard.dashboard)
// ---------------------------------------------------------------------------------------------

export const captureSessionSchema = z.looseObject({
  id: str,
  event: z.looseObject({
    id: str,
    courseCode: optStr,
    lessonNumber: optNum,
    title: optStr,
  }),
  /** PENDING · RECORDING · EXPORTING · COMPLETED · PARTIAL · EXISTING · FAILED · MISSED · INTERRUPTED */
  status: str,
  outputPath: optStr,
  actualStart: optStr,
});

export const captureRuntimeSchema = z.looseObject({
  recording: optBool,
  exporting: optBool,
  /** AUTHORIZED · DENIED · RESTRICTED · NOT_DETERMINED */
  microphonePermission: optStr,
  title: optStr,
  detail: optStr,
  currentSession: captureSessionSchema.nullable().optional(),
  sessions: z.array(captureSessionSchema).optional(),
  scheduleIssues: z.array(str).optional(),
  blockedEvents: z.record(str, str).optional(),
});

export const captureEventSchema = z.looseObject({
  id: str,
  sourceID: str,
  courseCode: z.string().nullable(),
  lessonNumber: z.number().int().nullable(),
  title: str,
  startsAt: str,
  endsAt: str,
  recordAt: str.optional(),
  stopAt: str.optional(),
  timezone: str,
  /** SCHEDULED · CONFLICT · NEEDS_REVIEW · CANCELLED */
  eligibility: str,
  reason: str,
  reasonCode: str.optional(),
  localChanges: z.boolean(),
  recordingEnabled: z.boolean(),
  sourceEligibility: str,
  special: z.boolean(),
  recordingID: str.optional(),
});

export const captureSourceSchema = z.looseObject({
  sourceID: str,
  label: str,
  host: str,
  timezone: str,
  lastSuccess: optStr,
  nextAttempt: optStr,
  error: optStr,
  code: optStr,
  message: optStr,
  retryable: optBool,
  suggestedAction: optStr,
  mappings: z.array(z.looseObject({ match: str, courseCode: str, firstLesson: optNum })),
  importMode: optStr,
  summary: z
    .looseObject({
      imported: z.number(),
      recognizedCourses: z.number(),
      pending: z.number(),
      excluded: z.number(),
      issues: z.array(z.looseObject({ title: str, reason: str })).optional(),
    })
    .nullable()
    .optional(),
});

export const captureCourseSchema = z.looseObject({ code: str, name: str, contextPending: optBool });

export const captureDashboardSchema = z.looseObject({
  schemaVersion: z.literal(1),
  configured: z.boolean(),
  enabled: z.boolean(),
  paused: z.boolean(),
  running: z.boolean().nullable(),
  processCheck: str,
  runtime: captureRuntimeSchema,
  sources: z.array(captureSourceSchema),
  events: z.array(captureEventSchema),
  courses: z.array(captureCourseSchema),
  responses: z.array(z.looseObject({ requestID: str, status: str, message: optStr })).optional(),
  refreshHours: z.number(),
  recordBeforeSeconds: z.number(),
  recordAfterSeconds: z.number(),
  outputDirectory: optStr,
  standaloneSupported: optBool,
  calendarRecordingsSupported: optBool,
});

export const captureActionSchema = z.looseObject({ status: str, requestID: optStr, message: optStr });

// ---------------------------------------------------------------------------------------------
// transcription queue and library (snake_case, transcription_queue.py)
// ---------------------------------------------------------------------------------------------

export const queueJobSchema = z.looseObject({
  id: str,
  created_at: optStr,
  recording_id: str,
  title: str,
  course_code: optStr,
  source_present: optBool,
  /** QUEUED · RUNNING · CANCELLING · COMPLETED · FAILED · INTERRUPTED · NEEDS_REVIEW · OUTDATED · WAITING */
  status: str,
  error: optStr,
  phase: optStr,
  elapsed_seconds: optNum,
  actions: z.array(str).optional(),
  queue_position: optNum,
  review_summary: optStr,
});

export const queueStatusSchema = z.looseObject({
  status: str.optional(),
  parallel: z.number().int(),
  paused: z.boolean(),
  ui_open: z.boolean(),
  jobs: z.array(queueJobSchema),
  wait_reason: optStr,
  activity_counts: z.record(str, z.number()).optional(),
  history_events: z.array(z.looseObject({ id: str })).optional(),
});

export const libraryRecordingSchema = z.looseObject({
  id: str,
  path: str,
  title: str,
  status: str,
  course_code: optStr,
  lecture_id: optStr,
  duration: optNum,
  created_at: str,
  recorded_at: optStr,
  transcription_status: optStr,
  can_transcribe: optBool,
  audio_available: optBool,
  association_state: optStr,
  origin: optStr,
});

export const libraryTranscriptSchema = z.looseObject({
  id: str,
  path: str,
  title: str,
  status: str,
  course_code: optStr,
  recording_id: optStr,
  lecture_id: optStr,
  created_at: str,
});

export const libraryLessonSchema = z.looseObject({
  id: str,
  course: optStr,
  number: optNum,
  title: str,
  date: optStr,
  recording_ids: z.array(str),
  transcript_ids: z.array(str),
  event_ids: z.array(str),
  not_recorded: z.boolean(),
  hidden: z.boolean(),
  revision_required: z.boolean(),
});

export const librarySchema = z.looseObject({
  lessons: z.array(libraryLessonSchema).optional(),
  recordings: z.array(libraryRecordingSchema),
  transcripts: z.array(libraryTranscriptSchema).optional(),
  folder: optStr,
  folder_configured: optBool,
});

// ---------------------------------------------------------------------------------------------
// configuration, doctor, providers, setup
// ---------------------------------------------------------------------------------------------

export const configSchema = z.looseObject({
  onboarding: z.looseObject({
    status: str,
    /** personal · paths · courses · style · providers (configuration.ONBOARDING_GROUPS) */
    completed_groups: z.array(str),
    next_group: optStr,
  }),
  personal: z.looseObject({ interface_language: optStr, background: optStr }).optional(),
  courses: z.array(z.looseObject({ code: str, name: str, notes_language: optStr })).optional(),
  providers: z.looseObject({ default: optStr }).optional(),
});

export const doctorSchema = z.looseObject({
  status: optStr,
  checks: z.record(str, z.looseObject({ status: str, message: optStr })),
});

/** missing · incompatible · login_required · unverified · ready */
export const providerStatusSchema = z.looseObject({ provider: str, status: str, version: optStr });

export const setupStatusSchema = z.looseObject({
  /** idle · running · paused · failed · complete */
  state: str,
  stage: optStr,
  title: optStr,
  bytesDone: optNum,
  bytesTotal: optNum,
  lastDataAt: optStr,
  error: optStr,
});

// ---------------------------------------------------------------------------------------------
// notes and jobs
// ---------------------------------------------------------------------------------------------

/** One entry of `batch-status --json` lessons. */
export const batchLessonSchema = z.looseObject({
  lecture_id: str,
  content_validated: z.boolean(),
  word_candidate: optStr,
  status: str,
});

export const batchStatusSchema = z.looseObject({
  batch_id: str,
  status: str,
  lessons: z.array(batchLessonSchema),
  problem: optStr,
  warnings: z.array(z.unknown()).optional(),
});

export const notesPlanSchema = z.looseObject({
  status: str,
  lessons: z.array(z.looseObject({ lecture_id: str, transcript: optBool, slides: z.array(str).optional(), notebook_section: optStr })).optional(),
});

export const jobRefSchema = z.looseObject({ jobId: str });

/**
 * App-side job record (`job.list`, `job.get`, `job.started`). Notes outcomes travel in
 * `result.status` (COMPLETED, AWAITING_APPROVAL, PAUSED_QUOTA, …).
 */
export const jobSchema = z.looseObject({
  jobId: str,
  /** notes · transcription · lesson · setup · login */
  kind: str,
  /** running · succeeded · failed · cancelled · interrupted */
  state: str,
  stage: optStr,
  detail: optStr,
  startedAt: str,
  finishedAt: optStr,
  stageStartedAt: optStr,
  courseCode: optStr,
  lectureIds: z.array(str).optional(),
  batchId: optStr,
  exitCode: optNum,
  result: z.looseObject({ status: str }).nullable().optional(),
});

export const jobListSchema = z.looseObject({ jobs: z.array(jobSchema) });

// ---------------------------------------------------------------------------------------------
// Method table
// ---------------------------------------------------------------------------------------------

const none = z.object({}).optional();
const qualitySchema = z.enum(["standard", "approfondito", "revisione"]);
const notesParams = z.object({
  course: str,
  lessons: str,
  quality: qualitySchema.optional(),
  instructions: str.optional(),
  imageLimit: z.number().int().optional(),
  speakerFocus: z.literal("professor").optional(),
});

export const methods = {
  "system.hello": { params: z.object({ protocol: z.literal(1) }), result: helloSchema },
  "system.ping": { params: none, result: z.unknown() },
  "session.open": { params: z.object({ visible: z.boolean() }), result: sessionSchema },
  "session.setVisible": { params: z.object({ visible: z.boolean() }), result: z.unknown() },
  "watch.subscribe": {
    params: z.object({ topics: z.array(z.enum(["capture", "queue", "library", "config"])) }),
    result: z.looseObject({
      capture: captureDashboardSchema.optional(),
      queue: queueStatusSchema.optional(),
      library: librarySchema.optional(),
      config: configSchema.optional(),
    }),
  },
  "doctor.get": { params: z.object({ provider: str.optional(), refresh: z.boolean().optional() }).optional(), result: cli(doctorSchema) },
  "provider.status": { params: z.object({ provider: str }), result: providerStatusSchema },
  "config.get": { params: none, result: cli(configSchema) },
  "setup.status": { params: none, result: setupStatusSchema },
  "capture.dashboard": { params: none, result: cli(captureDashboardSchema) },
  "capture.start": {
    params: z.object({
      courseCode: str.optional(),
      lessonNumber: z.number().int().optional(),
      unassigned: z.boolean().optional(),
      title: str.optional(),
      durationMinutes: z.number().int().optional(),
    }),
    result: cli(captureActionSchema),
  },
  "capture.stop": { params: z.object({ waitSeconds: z.number().optional() }).optional(), result: cli(captureActionSchema) },
  "queue.status": { params: none, result: cli(queueStatusSchema) },
  /** `transcription-queue library` (name to confirm against `mynotes serve --list-methods`). */
  "library.list": { params: none, result: cli(librarySchema) },
  "notes.plan": { params: notesParams, result: cli(notesPlanSchema) },
  "notes.run": { params: notesParams, result: jobRefSchema },
  "notes.batchStatus": { params: z.object({ batchId: str }), result: cli(batchStatusSchema) },
  "job.list": { params: none, result: jobListSchema },
  "job.get": { params: z.object({ jobId: str }), result: jobSchema },
  "job.cancel": { params: z.object({ jobId: str, force: z.boolean().optional() }), result: z.unknown() },
} as const;

export type MethodName = keyof typeof methods;
export type MethodParams<M extends MethodName> = z.input<(typeof methods)[M]["params"]>;
export type MethodResult<M extends MethodName> = z.output<(typeof methods)[M]["result"]>;

// ---------------------------------------------------------------------------------------------
// Notifications (server → app)
// ---------------------------------------------------------------------------------------------

export const notifications = {
  "capture.changed": captureDashboardSchema,
  "queue.changed": queueStatusSchema,
  "library.changed": librarySchema,
  "config.changed": configSchema,
  "capture.requestCompleted": z.looseObject({ requestID: str, status: str, message: optStr }),
  "provider.authChanged": providerStatusSchema,
  "job.started": jobSchema,
  "job.progress": z.looseObject({ jobId: str, stage: str, detail: optStr, phase: optStr, elapsedSeconds: optNum, at: optStr }),
  "job.output": z.looseObject({ jobId: str, line: str }),
  "job.finished": z.looseObject({ jobId: str, state: str, exitCode: optNum, result: z.looseObject({ status: str }).nullable().optional(), at: optStr }),
} as const;

export type NotificationName = keyof typeof notifications;
export type NotificationParams<N extends NotificationName> = z.output<(typeof notifications)[N]>;

// Convenience types for slices and features.
export type CaptureDashboard = z.output<typeof captureDashboardSchema>;
export type CaptureEvent = z.output<typeof captureEventSchema>;
export type CaptureSession = z.output<typeof captureSessionSchema>;
export type QueueStatus = z.output<typeof queueStatusSchema>;
export type QueueJob = z.output<typeof queueJobSchema>;
export type Library = z.output<typeof librarySchema>;
export type LibraryLesson = z.output<typeof libraryLessonSchema>;
export type LibraryRecording = z.output<typeof libraryRecordingSchema>;
export type Config = z.output<typeof configSchema>;
export type Job = z.output<typeof jobSchema>;
export type SetupStatus = z.output<typeof setupStatusSchema>;
export type NotesQuality = z.output<typeof qualitySchema>;
