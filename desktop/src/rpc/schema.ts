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

/** A watch topic's snapshot (`watch.subscribe`, `<topic>.changed`); `data` is null with `error`. */
export function snapshot<T extends z.ZodType>(data: T) {
  return z.looseObject({ topic: str, hash: str, data: data.nullable(), error: z.unknown().optional() });
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

/** `{exitCode, data}` of the command a job ran (`job.finished.result`, `Job.result`). */
export const jobResultSchema = z.looseObject({ exitCode: z.number().int().nullable().optional(), data: z.unknown().optional() });

/**
 * Job record as the server sends it (`job.get`, `job.list`, `job.wait`, `job.cancel`,
 * serve_jobs.Job.public). Times are epoch seconds; `lastProgress` is the last `job.progress`.
 */
export const jobRecordSchema = z.looseObject({
  id: str,
  method: str,
  params: z.record(str, z.unknown()).nullable().optional(),
  /** RUNNING · COMPLETED · FAILED · CANCELLED · INTERRUPTED */
  state: str,
  pid: optNum,
  startedAt: z.number(),
  finishedAt: optNum,
  exitCode: optNum,
  result: jobResultSchema.nullable().optional(),
  error: z.unknown().optional(),
  lastProgress: z.looseObject({ stage: str }).nullable().optional(),
  cancelRequested: optBool,
  resumeHint: z.looseObject({ method: str, params: z.record(str, z.unknown()).optional() }).nullable().optional(),
});

/** What a job method answers at once: `{jobId, job}`. */
export const jobRefSchema = z.looseObject({ jobId: str, job: jobRecordSchema.optional() });

export const jobListSchema = z.looseObject({ jobs: z.array(jobRecordSchema) });

/**
 * The app's view of a job, built from a record or the notifications by `toJob` (rpc/jobs.ts):
 * features read `kind`, lower-case `state`, ISO times, the course and lessons it covers, and
 * the command's outcome (`result.status`: COMPLETED, AWAITING_APPROVAL, PAUSED_QUOTA, …).
 */
export const jobSchema = z.looseObject({
  jobId: str,
  /** notes · transcription · lesson · setup · login · … (from the method) */
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

// ---------------------------------------------------------------------------------------------
// onboarding, courses, style, preferences
// ---------------------------------------------------------------------------------------------

export const providerIdSchema = z.enum(["claude", "codex", "agy"]);

/** `setup.status` plus the fields the onboarding header and checklist read (installer.rs). */
export const setupDetailSchema = setupStatusSchema.extend({
  overallDone: optNum,
  overallTotal: optNum,
  components: z
    .array(z.looseObject({ id: str, title: str, state: str, bytesDone: optNum, bytesTotal: optNum }))
    .optional(),
});

export const modelSelectionSchema = z.looseObject({ provider: str, model: str, effort: str });

/** `preferences --show` (preferences._model_preferences_snapshot). */
export const modelPreferencesSchema = z.looseObject({
  /** `provider` is null until one is chosen (a fresh workspace). */
  default: modelSelectionSchema.extend({ provider: str.nullable() }),
  overrides: z.looseObject({ chat: modelSelectionSchema.nullable().optional(), notes: modelSelectionSchema.nullable().optional() }),
  effective: z.looseObject({}).optional(),
  language: str,
  revision: str,
});

export const modelChoiceSchema = z.looseObject({
  id: str,
  model: str,
  provider: str,
  label: str,
  default_effort: optStr,
  supported_efforts: z.array(str),
  /** listed · cached · provider_default · assumed · saved_unavailable */
  availability: str,
  selectable: z.boolean(),
});

/** `models --provider X --json [--refresh]` (models.catalog). */
export const modelCatalogSchema = z.looseObject({
  provider: str,
  choices: z.array(modelChoiceSchema),
  /** ready · stale · no_catalog · error · … */
  status: str,
  message: optStr,
  fetched_at: optStr,
});

/** `doctor --json` checks carry an `action` (onboarding.doctor). */
export const doctorCheckSchema = z.looseObject({ status: str, action: optStr, message: optStr });
export const doctorReportSchema = z.looseObject({ ready: optBool, status: optStr, checks: z.record(str, doctorCheckSchema) });

export const courseAddSchema = z.looseObject({ status: str.optional(), code: optStr, course: z.looseObject({ code: str }).nullable().optional() });

/** `course edit` prints a sentence, not JSON: the server answers `{exitCode: 0, data: null, text}`. */
export const textResultSchema = z.object({ exitCode: z.number().int(), data: z.null(), text: str.optional() });

const notePair = z.looseObject({ personal_notes: optStr, handout: optStr });

/** `course link CODE PATH [--dry-run] --json` (course_links.link_course_folder): READY · LINKED · NEEDS_SELECTION. */
export const courseLinkSchema = z.looseObject({
  status: str,
  course: optStr,
  saved: optBool,
  connections: z.record(str, str).nullable().optional(),
  proposed_connections: z.record(str, str).nullable().optional(),
  note_files: notePair.nullable().optional(),
  proposed_files: notePair.nullable().optional(),
  ambiguities: z.record(str, z.array(str)).nullable().optional(),
  inventory: z
    .looseObject({ resolved_folder: optStr, file_counts: z.record(str, z.number()).optional(), files: z.record(str, z.array(str)).optional() })
    .nullable()
    .optional(),
  publication_mode: optStr,
});

/** `capture connect` (capture.connect): CONNECTED with a summary, or FAILED with a capture_links code. */
export const captureConnectSchema = z.looseObject({
  status: str,
  sourceID: optStr,
  summary: z.looseObject({ imported: z.number(), pending: z.number().optional() }).nullable().optional(),
  code: optStr,
  message: optStr,
  retryable: optBool,
  suggestedAction: optStr,
});

/** Editable style fields (configuration.EDITABLE_STYLE_FIELDS plus counts). */
export const styleProfileSchema = z.looseObject({
  writing_style: str,
  list_density: str,
  image_policy: str,
  image_limit: z.number().int().nullable().optional(),
  question_count: z.number().int(),
  recap: z.boolean(),
  recap_language: str,
  slide_scope: optStr,
});

/** `style show` and `style edit` (cli._style). */
export const styleShowSchema = z.looseObject({ scope: str, effective: styleProfileSchema });
/** `style preview`: `preview` is the path of the generated sample document. */
export const stylePreviewSchema = z.looseObject({ scope: str, preview: str });

// ---------------------------------------------------------------------------------------------
// calendar and recording (capture_dashboard.py)
// ---------------------------------------------------------------------------------------------

export const lessonInputSchema = z.object({
  courseCode: str.nullable().optional(),
  lessonNumber: z.number().int().nullable().optional(),
  title: str.optional(),
  startsAt: str,
  endsAt: str,
  timezone: str,
  unassigned: z.boolean().optional(),
  transcriptionLanguage: str.optional(),
});

/** `capture edit-lesson` accepts only these fields (capture_dashboard.FIELDS). */
export const lessonPatchSchema = z.object({
  title: str.optional(),
  startsAt: str.optional(),
  endsAt: str.optional(),
  timezone: str.optional(),
  recordingEnabled: z.boolean().optional(),
});

export const lessonChangeSchema = z.looseObject({ status: str, eventID: optStr, localChanges: optBool });

export const captureSettingsPatchSchema = z.object({
  refreshHours: z.number().optional(),
  recordBeforeSeconds: z.number().int().min(0).max(1800).optional(),
  recordAfterSeconds: z.number().int().min(0).max(1800).optional(),
  outputDirectory: str.optional(),
});

export const calendarSourceInputSchema = z.object({
  url: str,
  timezone: str,
  periodStart: str,
  periodEnd: str,
  label: str.optional(),
  importMode: z.enum(["mapped", "all_timed"]).optional(),
});

// ---------------------------------------------------------------------------------------------
// queue and library (transcription_queue.py)
// ---------------------------------------------------------------------------------------------

/** `transcription-queue pause|resume|configure|clear|retry|…` answers. */
export const queueChangeSchema = z.looseObject({
  status: str,
  message: optStr,
  parallel: optNum,
  paused: optBool,
  removed: optNum,
  running: optNum,
});

export const queueJobActionSchema = z.enum(["retry", "restart", "recover", "remove", "cancel"]);

/** One file of `transcription-queue inspect-audio` (lesson_registry.inspect_imports). */
export const audioInspectionSchema = z.looseObject({
  path: str,
  name: str,
  duration: optNum,
  recorded_at: optStr,
  size: z.number(),
  duplicate: optBool,
  name_collision: optBool,
});

export const audioImportItemSchema = z.object({
  path: str,
  /** Library lesson id to join (lesson_registry.import_items). */
  target: str.optional(),
  event_id: str.optional(),
  course: str.optional(),
  lesson: z.number().int().optional(),
  recorded_at: str.optional(),
});

export const audioImportSchema = z.looseObject({
  status: str.optional(),
  imported: z.array(z.looseObject({ recording_id: str, duplicate: z.boolean(), name: optStr })).optional(),
  message: optStr,
});

/** `transcription-queue lesson-edit` request: rename or re-date a lesson, or hide it. */
export const lessonEditSchema = z.object({ action: z.enum(["update", "hide"]), id: str, title: str.optional(), date: str.optional() });

/**
 * `library.transcript` (serve_transcript.py, docs/SERVE.md "Transcript text"): the text of a
 * lesson's transcript, read from the path MyNotes recorded for it. At most `maxBytes` bytes
 * (2 MiB unless asked); `truncated` when the file is longer, `size` its length in bytes.
 * `encoding`: utf-8 · utf-8-sig · latin-1. The timestamped lines are parsed in the lesson feature.
 */
export const transcriptTextSchema = z.looseObject({
  path: str,
  text: str,
  truncated: z.boolean(),
  encoding: str,
  size: optNum,
});

// ---------------------------------------------------------------------------------------------
// notes history (publication.py: `history`, `undo`, `restore`, `publish`)
// ---------------------------------------------------------------------------------------------

/** One cumulative-Word backup (`mynotes history CODE`). `reason`: "before STAT-L06 publication". */
export const notesBackupSchema = z.looseObject({
  backup_id: str,
  course: str,
  path: optStr,
  source: optStr,
  reason: optStr,
  /** Compact UTC stamp, e.g. 20261124T105812123456Z (utils.now_compact_utc). */
  created_at: str,
  publication_state: z.looseObject({ lessons: z.array(z.looseObject({ lecture_id: str })).optional() }).nullable().optional(),
});

export const notesUndoSchema = z.looseObject({
  status: str,
  course: optStr,
  lecture_id: optStr,
  output: optStr,
  message: optStr,
  pre_restore_backup: z.unknown().optional(),
});

export const notesRestoreSchema = z.looseObject({ status: str, backup_id: optStr, output: optStr, pre_restore_backup: z.unknown().optional() });

export const notesPublishSchema = z.looseObject({ status: str, lecture_id: optStr, output: optStr });

// ---------------------------------------------------------------------------------------------
// assistant: the chat agent streamed through `mynotes serve` (serve_assistant.py, docs/SERVE.md
// "Assistant"). `start` and `send` run in the job lane but answer `{sessionId, turnId}`; the
// turn then streams `assistant.delta|tool`, then exactly one `assistant.done|error`.
// ---------------------------------------------------------------------------------------------

/** The page the user is on, sent with each message as data (the server checks it again). */
export const assistantContextSchema = z.object({
  course: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/).optional(),
  lesson: z.number().int().min(1).max(999).optional(),
  lectureId: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,31}-L\d{1,3}$/).optional(),
  page: z.string().min(1).max(80).optional(),
});

/** One tool call; `status`: running · completed · failed · denied. */
export const assistantToolSchema = z.looseObject({
  toolId: str,
  name: str,
  summary: str,
  status: str,
  command: optStr,
  durationMs: optNum,
});

/** `state`: RUNNING · COMPLETED · FAILED · CANCELLED · INTERRUPTED. */
export const assistantTurnSchema = z.looseObject({
  id: str,
  message: str,
  state: str,
  text: str,
  tools: z.array(assistantToolSchema),
  error: z.looseObject({ code: str, message: str }).nullable().optional(),
  startedAt: optNum,
  finishedAt: optNum,
});

export const assistantSessionSchema = z.looseObject({
  id: str,
  provider: str,
  model: optStr,
  providerSession: optStr,
  /** native · replay (the agent reported no resumable session) */
  resume: optStr,
  title: optStr,
  updatedAt: optNum,
  turns: z.array(assistantTurnSchema),
});

export const assistantSummarySchema = z.looseObject({
  id: str,
  title: optStr,
  provider: str,
  updatedAt: optNum,
  turns: z.number().int(),
  state: optStr,
  running: z.boolean(),
});

const assistantMessage = z.string().trim().min(1).max(20_000);
const assistantTurnRef = z.looseObject({ sessionId: str, turnId: str });

const assistantMethods = {
  "assistant.start": {
    params: z.object({
      message: assistantMessage,
      provider: providerIdSchema.optional(),
      model: str.optional(),
      effort: str.optional(),
      context: assistantContextSchema.optional(),
    }),
    result: assistantTurnRef,
  },
  "assistant.send": { params: z.object({ sessionId: str, message: assistantMessage, context: assistantContextSchema.optional() }), result: assistantTurnRef },
  "assistant.cancel": {
    params: z.object({ sessionId: str, force: z.boolean().optional() }),
    result: z.looseObject({ sessionId: str, cancelRequested: z.boolean() }),
  },
  "assistant.list": {
    params: z.object({ limit: z.number().int().min(1).max(200).optional() }).optional(),
    result: z.looseObject({ sessions: z.array(assistantSummarySchema) }),
  },
  "assistant.get": { params: z.object({ sessionId: str }), result: assistantSessionSchema },
} as const;

const assistantNotifications = {
  "assistant.delta": z.looseObject({ sessionId: str, turnId: str, text: str }),
  "assistant.tool": assistantToolSchema.extend({ sessionId: str, turnId: str }),
  "assistant.done": z.looseObject({ sessionId: str, turnId: str, text: str, providerSession: optStr, usage: z.unknown().optional() }),
  /** `code`: QUOTA · AUTH · NETWORK · TIMEOUT · UNAVAILABLE · CANCELLED · PROVIDER */
  "assistant.error": z.looseObject({ sessionId: str, turnId: str, code: str, message: str }),
} as const;

export type AssistantContext = z.input<typeof assistantContextSchema>;
export type AssistantSession = z.output<typeof assistantSessionSchema>;
export type AssistantToolRow = z.output<typeof assistantToolSchema>;
export type AssistantNotification = {
  [N in keyof typeof assistantNotifications]: { readonly type: N } & z.output<(typeof assistantNotifications)[N]>;
}[keyof typeof assistantNotifications];

// ---------------------------------------------------------------------------------------------
// Method table: one entry per method. Names, params and lanes follow docs/SERVE.md in the serve
// repository (params are the command's flags in camelCase; a `json` param is written to a private
// request file by the server). `setup.*` is answered by the desktop shell's installer, not by
// `mynotes serve`. Methods marked "Not in SERVE.md yet" are requested additions: the app handles
// their "unknown method" error.
// ---------------------------------------------------------------------------------------------

const none = z.object({}).optional();
const qualitySchema = z.enum(["standard", "approfondito", "revisione"]);
const notesParams = z.object({
  course: str,
  lessons: str.optional(),
  resume: str.optional(),
  regenerate: z.boolean().optional(),
  provider: providerIdSchema.optional(),
  quality: qualitySchema.optional(),
  instructions: str.optional(),
  imageLimit: z.number().int().optional(),
  speakerFocus: z.enum(["lecture", "professor"]).optional(),
  contextLessons: str.optional(),
  contiguousTranscripts: z.boolean().optional(),
});
const libraryQuery = z.object({ query: str.optional(), date: str.optional(), course: str.optional(), sync: z.boolean().optional() }).optional();
const okSchema = z.looseObject({ status: str.optional(), message: optStr });
const anyObject = z.looseObject({});

export const methods = {
  // system, session, watch
  "system.hello": { params: z.object({ protocol: z.literal(1) }), result: helloSchema },
  "system.ping": { params: none, result: z.unknown() },
  "session.open": { params: z.object({ visible: z.boolean() }), result: sessionSchema },
  "session.setVisible": { params: z.object({ visible: z.boolean() }), result: z.unknown() },
  "watch.subscribe": {
    params: z.object({ topics: z.array(z.enum(["capture", "queue", "library", "config"])) }),
    result: z.looseObject({
      topics: z.array(str),
      snapshots: z.looseObject({
        capture: snapshot(captureDashboardSchema).optional(),
        queue: snapshot(queueStatusSchema).optional(),
        library: snapshot(librarySchema).optional(),
        config: snapshot(configSchema).optional(),
      }),
    }),
  },

  // setup (desktop installer), doctor, providers, configuration
  "setup.status": { params: none, result: setupStatusSchema },
  /** The shell's `setup_run` (src/rpc/setup.ts): answers the first status, then `setup://progress`. */
  "setup.run": { params: z.object({ provider: providerIdSchema, workspace: str.min(1).optional() }), result: setupStatusSchema },
  "doctor.get": { params: z.object({ provider: providerIdSchema.optional(), refresh: z.boolean().optional() }).optional(), result: cli(doctorSchema) },
  "provider.status": { params: z.object({ provider: str }), result: cli(providerStatusSchema) },
  "provider.login": { params: z.object({ provider: providerIdSchema }), result: jobRefSchema },
  "config.get": { params: none, result: cli(configSchema) },
  "profile.save": { params: z.union([z.object({ background: str }), z.object({ skip: z.literal(true) })]), result: cli(anyObject) },
  "preferences.get": { params: none, result: cli(modelPreferencesSchema) },
  "preferences.saveModels": {
    params: z.object({
      models: z.object({
        default: modelSelectionSchema.nullable(),
        overrides: z.object({ chat: modelSelectionSchema.nullable(), notes: modelSelectionSchema.nullable() }),
        language: z.enum(["en", "it"]),
      }),
      expectedRevision: str,
    }),
    result: cli(modelPreferencesSchema),
  },
  "models.catalog": { params: z.object({ provider: str, saved: str.optional(), savedEffort: str.optional() }), result: cli(modelCatalogSchema) },
  /** `models --refresh`: a job; read the refreshed list with `models.catalog` once it ends. */
  "models.refresh": { params: z.object({ provider: str, saved: str.optional(), savedEffort: str.optional() }), result: jobRefSchema },

  // courses and style
  "course.add": {
    params: z.object({
      name: str,
      notesLanguage: z.enum(["en", "it"]).optional(),
      materials: str.optional(),
      recordings: str.optional(),
      contextAnswer: str.optional(),
      settings: anyObject.optional(),
      deferContext: z.boolean().optional(),
    }),
    result: cli(courseAddSchema),
  },
  /** `course edit CODE --file`: the course record to save (merged into the saved one). */
  "course.edit": { params: z.object({ code: str, course: z.record(str, z.unknown()) }), result: textResultSchema },
  "course.link": {
    params: z.object({
      code: str,
      path: str,
      notes: str.optional(),
      notesFile: str.optional(),
      handoutFile: str.optional(),
      publicationMode: z.enum(["automatic", "review"]).optional(),
      recordings: str.optional(),
      dryRun: z.boolean().optional(),
    }),
    result: cli(courseLinkSchema),
  },
  "style.show": { params: z.object({ course: str.optional() }), result: cli(styleShowSchema) },
  "style.edit": {
    params: z.union([z.object({ course: str.optional(), style: z.record(str, z.unknown()) }), z.object({ course: str.optional(), reset: z.literal(true) })]),
    result: cli(styleShowSchema),
  },
  "style.preview": { params: z.object({ course: str.optional(), output: str.optional() }), result: cli(stylePreviewSchema) },

  // calendar and recording
  "capture.dashboard": { params: none, result: cli(captureDashboardSchema) },
  "capture.start": {
    params: z.object({
      course: str.optional(),
      lesson: z.number().int().optional(),
      unassigned: z.boolean().optional(),
      title: str.optional(),
      language: str.optional(),
      durationMinutes: z.number().int(),
    }),
    result: cli(captureActionSchema),
  },
  "capture.stop": { params: none, result: cli(captureActionSchema) },
  "capture.test": { params: none, result: cli(captureActionSchema) },
  "capture.confirmTest": { params: z.object({ audio: str, audibleConfirmed: z.boolean() }), result: cli(anyObject) },
  "capture.recover": { params: z.object({ session: str.optional() }).optional(), result: cli(captureActionSchema) },
  "capture.configure": { params: z.object({ config: captureSettingsPatchSchema.optional() }).optional(), result: cli(okSchema) },
  /** Job lane (it downloads the calendar): read the answer with `runJob` (rpc/jobs.ts). */
  "capture.connect": { params: z.object({ source: anyObject }), result: jobRefSchema },
  "capture.unsubscribe": { params: z.object({ source: str }), result: cli(okSchema) },
  "capture.setImportMode": { params: z.object({ source: str, mode: z.enum(["mapped", "all_timed"]) }), result: cli(okSchema) },
  /** Job lane, like `capture.connect`, `.enable` and `.disable` (network, OS services). */
  "capture.sync": { params: z.object({ ifDue: z.boolean().optional(), source: str.optional() }).optional(), result: jobRefSchema },
  "capture.enable": { params: none, result: jobRefSchema },
  "capture.disable": { params: none, result: jobRefSchema },
  "capture.pause": { params: none, result: cli(okSchema) },
  "capture.resume": { params: none, result: cli(okSchema) },
  "capture.addLesson": { params: z.object({ lesson: lessonInputSchema }), result: cli(lessonChangeSchema) },
  "capture.editLesson": { params: z.object({ event: str, patch: lessonPatchSchema }), result: cli(lessonChangeSchema) },
  "capture.resetLesson": { params: z.object({ event: str }), result: cli(lessonChangeSchema) },

  // transcription queue
  "queue.status": { params: none, result: cli(queueStatusSchema) },
  "queue.pause": { params: none, result: cli(queueChangeSchema) },
  "queue.resume": { params: none, result: cli(queueChangeSchema) },
  "queue.clear": { params: none, result: cli(queueChangeSchema) },
  "queue.configure": { params: z.object({ parallel: z.number().int().min(1).max(4) }), result: cli(queueChangeSchema) },
  "queue.enqueue": {
    params: z.union([
      z.object({ allUntranscribed: z.literal(true), parallel: z.number().int().optional() }),
      z.object({ recordingId: str, parallel: z.number().int().optional() }),
      z.object({ readyRecordings: z.array(str).min(1), parallel: z.number().int().optional() }),
    ]),
    result: cli(okSchema),
  },
  "queue.job": { params: z.object({ action: queueJobActionSchema, jobId: str }), result: cli(queueChangeSchema) },

  // library
  "library.list": { params: libraryQuery, result: cli(librarySchema) },
  "library.inspectAudio": { params: z.object({ paths: z.array(str).min(1).max(100) }), result: cli(z.looseObject({ status: str, files: z.array(audioInspectionSchema) })) },
  "library.importAudio": { params: z.object({ items: z.array(audioImportItemSchema).min(1).max(100) }), result: cli(audioImportSchema) },
  "library.verify": { params: none, result: cli(okSchema) },
  "library.associate": { params: z.object({ recordingId: str, course: str, lesson: z.number().int().optional() }), result: cli(okSchema) },
  "library.finish": { params: z.object({ recordingId: str }), result: cli(okSchema) },
  /** `prefer-audio`: use this recording for the lesson's transcription. */
  "library.preferAudio": { params: z.object({ recordingId: str }), result: cli(okSchema) },
  "library.editLesson": { params: z.object({ edit: lessonEditSchema }), result: cli(okSchema) },
  /** The transcript text of one lesson or recording; the server picks the file, never the app. */
  "library.transcript": {
    params: z.union([
      z.object({ lectureId: str, maxBytes: z.number().int().min(1).max(8 * 1024 * 1024).optional() }),
      z.object({ recordingId: str, maxBytes: z.number().int().min(1).max(8 * 1024 * 1024).optional() }),
    ]),
    result: cli(transcriptTextSchema),
  },

  // transcription, notes, lessons, jobs
  "transcription.run": {
    params: z.union([z.object({ lectureId: str, audio: str.optional() }), z.object({ recordingId: str, audio: str.optional() })]),
    result: jobRefSchema,
  },
  /** One lesson from an audio file, or from a transcript (`transcript` skips speech recognition). */
  "lesson.process": {
    params: z
      .object({ lectureId: str, audio: str.optional(), transcript: str.optional(), provider: providerIdSchema.optional(), quality: str.optional(), instructions: str.optional() })
      .refine((value) => !(value.audio && value.transcript), { message: "Give an audio file or a transcript, not both" }),
    result: jobRefSchema,
  },
  "notes.plan": { params: notesParams, result: cli(notesPlanSchema) },
  "notes.run": { params: notesParams, result: jobRefSchema },
  "notes.batchStatus": { params: z.object({ batchId: str }), result: cli(batchStatusSchema) },
  "notes.history": { params: z.object({ course: str.optional() }), result: cli(z.array(notesBackupSchema)) },
  /** `undo CODE`: restores the course notes before the last update; a safety copy is kept. */
  "notes.undo": { params: z.object({ course: str }), result: cli(notesUndoSchema) },
  "notes.restore": { params: z.object({ backupId: str }), result: cli(notesRestoreSchema) },
  /** `publish CODE-LNN [--candidate PATH]` (Approve & update Word): a job, it renders with LibreOffice. */
  "notes.publish": { params: z.object({ lecture: str, candidate: str.optional() }), result: jobRefSchema },
  "job.list": { params: z.object({ limit: z.number().int().optional() }).optional(), result: jobListSchema },
  "job.get": { params: z.object({ jobId: str }), result: jobRecordSchema },
  "job.wait": { params: z.object({ jobId: str, timeoutSeconds: z.number().optional() }), result: jobRecordSchema },
  "job.cancel": { params: z.object({ jobId: str, force: z.boolean().optional() }), result: jobRecordSchema },

  // checks
  "word.verifyRenderer": { params: none, result: jobRefSchema },
  "speech.installModel": { params: none, result: jobRefSchema },
  "speech.verify": { params: z.object({ audio: str.optional(), language: z.enum(["en", "it"]).optional() }).optional(), result: jobRefSchema },

  // assistant (block above)
  ...assistantMethods,
} as const;

export type MethodName = keyof typeof methods;

/**
 * What `job.finished.result` carries for the job methods the app waits on (`runJob`,
 * rpc/jobs.ts): the command's `{exitCode, data}`, checked like a direct result.
 */
export const jobResults = {
  "capture.connect": cli(captureConnectSchema),
  "capture.sync": cli(okSchema),
  "capture.enable": cli(okSchema),
  "capture.disable": cli(okSchema),
  "notes.publish": cli(notesPublishSchema),
} as const;

export type JobMethod = keyof typeof jobResults;
export type JobResult<M extends JobMethod> = z.output<(typeof jobResults)[M]>;
export type MethodParams<M extends MethodName> = z.input<(typeof methods)[M]["params"]>;
export type MethodResult<M extends MethodName> = z.output<(typeof methods)[M]["result"]>;

// ---------------------------------------------------------------------------------------------
// Notifications (server → app)
// ---------------------------------------------------------------------------------------------

export const notifications = {
  "capture.changed": snapshot(captureDashboardSchema),
  "queue.changed": snapshot(queueStatusSchema),
  "library.changed": snapshot(librarySchema),
  "config.changed": snapshot(configSchema),
  "capture.requestCompleted": z.looseObject({ requestID: str, status: str, message: optStr }),
  /** serve._watch_login: `status` is the whole provider check (`provider.status` data). */
  "provider.authChanged": z.looseObject({ provider: str, jobId: optStr, status: providerStatusSchema }),
  "job.started": z.looseObject({ jobId: str, method: str, params: z.record(str, z.unknown()).nullable().optional(), pid: optNum }),
  /** One stderr JSON line with a `stage` (notes, lesson process), or the ASR status for transcription. */
  "job.progress": z.looseObject({ jobId: str, stage: str, detail: optStr, phase: optStr, elapsedSeconds: optNum, at: optStr }),
  "job.output": z.looseObject({ jobId: str, stream: str, text: str }),
  "job.finished": z.looseObject({
    jobId: str,
    /** COMPLETED · FAILED · CANCELLED · INTERRUPTED */
    state: str,
    exitCode: optNum,
    result: jobResultSchema.nullable().optional(),
    error: z.unknown().optional(),
    resumeHint: z.looseObject({ method: str, params: z.record(str, z.unknown()).optional() }).nullable().optional(),
  }),
  ...assistantNotifications,
} as const;

export type NotificationName = keyof typeof notifications;
export type NotificationParams<N extends NotificationName> = z.output<(typeof notifications)[N]>;

// Convenience types for slices and features.
export type CaptureDashboard = z.output<typeof captureDashboardSchema>;
export type CaptureEvent = z.output<typeof captureEventSchema>;
export type CaptureSession = z.output<typeof captureSessionSchema>;
export type CaptureSource = z.output<typeof captureSourceSchema>;
export type QueueStatus = z.output<typeof queueStatusSchema>;
export type QueueJob = z.output<typeof queueJobSchema>;
export type QueueJobAction = z.output<typeof queueJobActionSchema>;
export type Library = z.output<typeof librarySchema>;
export type LibraryLesson = z.output<typeof libraryLessonSchema>;
export type LibraryRecording = z.output<typeof libraryRecordingSchema>;
export type LibraryTranscript = z.output<typeof libraryTranscriptSchema>;
export type Config = z.output<typeof configSchema>;
export type Job = z.output<typeof jobSchema>;
export type JobRecord = z.output<typeof jobRecordSchema>;
export type SetupStatus = z.output<typeof setupStatusSchema>;
export type NotesQuality = z.output<typeof qualitySchema>;
export type LessonPatch = z.input<typeof lessonPatchSchema>;
export type LessonInput = z.input<typeof lessonInputSchema>;
export type CaptureSettingsPatch = z.input<typeof captureSettingsPatchSchema>;
export type AudioInspection = z.output<typeof audioInspectionSchema>;
export type AudioImportItem = z.input<typeof audioImportItemSchema>;
export type ModelPreferences = z.output<typeof modelPreferencesSchema>;
export type ModelSelection = z.output<typeof modelSelectionSchema>;
export type ModelCatalog = z.output<typeof modelCatalogSchema>;
export type ModelChoice = z.output<typeof modelChoiceSchema>;
export type DoctorReport = z.output<typeof doctorReportSchema>;
export type NotesBackup = z.output<typeof notesBackupSchema>;
export type StyleProfile = z.output<typeof styleProfileSchema>;
export type CourseLink = z.output<typeof courseLinkSchema>;
export type TranscriptText = z.output<typeof transcriptTextSchema>;
