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
// >>> onboarding (feat/desktop-onboarding) >>>
// Setup, sign-in, profile, course, folders, calendar, microphone and first notes (UX-SPEC §2).
// Spread into `methods` below with `...onboardingMethods`.
// ---------------------------------------------------------------------------------------------

const providerId = z.enum(["claude", "codex", "agy"]);

/** `setup.status` plus the fields the onboarding header and checklist read (installer.rs). */
export const setupDetailSchema = setupStatusSchema.extend({
  overallDone: optNum,
  overallTotal: optNum,
  components: z
    .array(z.looseObject({ id: str, title: str, state: str, bytesDone: optNum, bytesTotal: optNum }))
    .optional(),
});

const modelChoice = z.looseObject({ provider: str, model: str, effort: str });
/** `preferences --show` (preferences.model_preferences). */
export const preferencesSchema = z.looseObject({
  default: modelChoice.nullable(),
  overrides: z.looseObject({ chat: modelChoice.nullable().optional(), notes: modelChoice.nullable().optional() }),
  language: str,
  revision: str,
});

export const courseAddSchema = z.looseObject({ status: str.optional(), code: optStr, course: z.looseObject({ code: str }).nullable().optional() });

/** `course link --json` (course_links.link_course_folder): READY · LINKED · NEEDS_SELECTION. */
export const courseLinkSchema = z.looseObject({
  status: str,
  course: optStr,
  saved: optBool,
  proposed_files: z.looseObject({ personal_notes: optStr, handout: optStr }).nullable().optional(),
  note_files: z.looseObject({ personal_notes: optStr, handout: optStr }).nullable().optional(),
  ambiguities: z.record(str, z.array(str)).nullable().optional(),
  inventory: z
    .looseObject({ file_counts: z.record(str, z.number()).optional(), files: z.record(str, z.array(str)).optional() })
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

const anyObject = z.looseObject({});
const noParams = z.object({}).optional();
const courseSettingsParams = z.looseObject({});

const onboardingMethods = {
  "setup.run": { params: z.object({ provider: providerId }), result: jobRefSchema },
  "provider.login": { params: z.object({ provider: providerId }), result: jobRefSchema },
  "preferences.get": { params: noParams, result: cli(preferencesSchema) },
  "preferences.saveModels": {
    params: z.object({
      models: z.object({ default: modelChoice.nullable(), overrides: z.object({ chat: modelChoice.nullable(), notes: modelChoice.nullable() }), language: z.enum(["en", "it"]) }),
      expectedRevision: str,
    }),
    result: cli(preferencesSchema),
  },
  "profile.save": { params: z.union([z.object({ background: str }), z.object({ skip: z.literal(true) })]), result: cli(anyObject) },
  "course.add": {
    params: z.object({ name: str, notesLanguage: z.enum(["en", "it"]), settingsJson: courseSettingsParams, contextAnswer: str.optional() }),
    result: cli(courseAddSchema),
  },
  "course.edit": { params: z.object({ code: str, file: anyObject }), result: cli(anyObject) },
  "course.link": {
    params: z.object({
      code: str,
      path: str,
      dryRun: z.boolean().optional(),
      notes: str.optional(),
      recordings: str.optional(),
      notesFile: str.optional(),
      publicationMode: z.enum(["automatic", "review"]).optional(),
    }),
    result: cli(courseLinkSchema),
  },
  "style.preview": { params: z.object({ course: str }), result: cli(z.looseObject({ path: optStr, output: optStr })) },
  "capture.configure": { params: z.object({ file: anyObject }).optional(), result: cli(anyObject) },
  "capture.connect": { params: z.object({ file: anyObject }), result: cli(captureConnectSchema) },
  "capture.unsubscribe": { params: z.object({ source: str }), result: cli(anyObject) },
  "capture.enable": { params: noParams, result: cli(anyObject) },
  "capture.pause": { params: noParams, result: cli(anyObject) },
  "capture.resume": { params: noParams, result: cli(anyObject) },
  "capture.test": { params: z.object({ waitSeconds: z.number().optional() }).optional(), result: cli(captureActionSchema) },
  "capture.confirmTest": { params: z.object({ audio: str, audibleConfirmed: z.literal(true) }), result: cli(anyObject) },
  /** A4 `lesson process`; `transcript` is a requested addition (the CLI has `prepare --transcript`). */
  "lesson.process": { params: z.object({ lectureId: str, audio: str.optional(), transcript: str.optional() }), result: jobRefSchema },
} as const;

export type SetupDetailResult = z.output<typeof setupDetailSchema>;
export type CourseLinkResult = z.output<typeof courseLinkSchema>;
export type PreferencesResult = z.output<typeof preferencesSchema>;

// <<< onboarding (feat/desktop-onboarding) <<<

// ---------------------------------------------------------------------------------------------
// BEGIN assistant (feat/desktop-assistant-ui)
// The chat agent streamed through `mynotes serve` (serve_assistant.py, docs/SERVE.md
// "Assistant"). Spread into `methods` and `notifications` below.
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
      provider: z.enum(["claude", "codex", "agy"]).optional(),
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

// END assistant (feat/desktop-assistant-ui)
// ---------------------------------------------------------------------------------------------

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

// ---------------------------------------------------------------------------------------------
// BEGIN workspace screens (Calendar, Library, Activity, Settings, palette)
//
// Naming rule for CLI-backed methods: the subcommand in camelCase, its flags in camelCase, and
// `file` for the JSON object the server writes to `state/desktop-requests/` and passes as
// `--file` (capture_dashboard.py, transcription_queue.py, preferences.py, models.py).
// Names still to confirm against `mynotes serve --list-methods`.
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

export const modelSelectionSchema = z.looseObject({ provider: str, model: str, effort: str });

/** `preferences --show` (preferences._model_preferences_snapshot). */
export const modelPreferencesSchema = z.looseObject({
  default: modelSelectionSchema,
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

const okSchema = z.looseObject({ status: str.optional(), message: optStr });

export const workspaceMethods = {
  "capture.addLesson": { params: z.object({ file: lessonInputSchema }), result: cli(lessonChangeSchema) },
  "capture.editLesson": { params: z.object({ event: str, file: lessonPatchSchema }), result: cli(lessonChangeSchema) },
  "capture.resetLesson": { params: z.object({ event: str }), result: cli(lessonChangeSchema) },
  "capture.sync": { params: z.object({ ifDue: z.boolean().optional(), source: str.optional() }).optional(), result: cli(okSchema) },
  "capture.unsubscribe": { params: z.object({ source: str }), result: cli(okSchema) },
  "capture.setImportMode": { params: z.object({ source: str, mode: z.enum(["mapped", "all_timed"]) }), result: cli(okSchema) },
  "capture.configure": { params: z.object({ file: captureSettingsPatchSchema }), result: cli(okSchema) },
  "capture.enable": { params: none, result: cli(okSchema) },
  "capture.disable": { params: none, result: cli(okSchema) },
  "capture.pause": { params: none, result: cli(okSchema) },
  "capture.resume": { params: none, result: cli(okSchema) },
  "capture.test": { params: z.object({ waitSeconds: z.number().optional() }).optional(), result: cli(captureActionSchema) },
  "capture.recover": { params: z.object({ session: str.optional(), waitSeconds: z.number().optional() }).optional(), result: cli(captureActionSchema) },
  "queue.pause": { params: none, result: cli(queueChangeSchema) },
  "queue.resume": { params: none, result: cli(queueChangeSchema) },
  "queue.clear": { params: none, result: cli(queueChangeSchema) },
  "queue.configure": { params: z.object({ parallel: z.number().int().min(1).max(4) }), result: cli(queueChangeSchema) },
  "queue.enqueue": {
    params: z.object({ allUntranscribed: z.boolean().optional(), recording: str.optional(), readyRecording: z.array(str).optional(), parallel: z.number().int().optional() }),
    result: cli(okSchema),
  },
  "queue.job": { params: z.object({ jobId: str, action: queueJobActionSchema }), result: cli(queueChangeSchema) },
  "queue.finish": { params: z.object({ recording: str }), result: cli(okSchema) },
  "library.inspectAudio": { params: z.object({ file: z.object({ paths: z.array(str).min(1).max(100) }) }), result: cli(z.looseObject({ status: str, files: z.array(audioInspectionSchema) })) },
  "library.importAudio": { params: z.object({ file: z.object({ items: z.array(audioImportItemSchema).min(1).max(100) }) }), result: cli(audioImportSchema) },
  "library.verifyLibrary": { params: none, result: cli(okSchema) },
  "library.associate": { params: z.object({ recording: str, course: str, lesson: z.number().int().optional() }), result: cli(okSchema) },
  "preferences.get": { params: none, result: cli(modelPreferencesSchema) },
  "models.catalog": {
    params: z.object({ provider: str, saved: str.optional(), savedEffort: str.optional(), refresh: z.boolean().optional() }),
    result: cli(modelCatalogSchema),
  },
  "provider.login": { params: z.object({ provider: str }), result: jobRefSchema },
  /** `undo CODE`: restores the course notes before the last update; a safety copy is kept. */
  "notes.undo": { params: z.object({ course: str }), result: cli(okSchema) },
  "word.verifyRenderer": { params: none, result: jobRefSchema },
  "speech.installModel": { params: none, result: jobRefSchema },
  "speech.verify": { params: z.object({ language: z.enum(["en", "it"]).optional() }).optional(), result: jobRefSchema },
} as const;

// END workspace screens
// ---------------------------------------------------------------------------------------------

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
  // onboarding (feat/desktop-onboarding)
  ...onboardingMethods,
  // workspace screens (see the block above)
  ...workspaceMethods,
  // assistant (feat/desktop-assistant-ui, see the block above)
  ...assistantMethods,
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
  // assistant (feat/desktop-assistant-ui)
  ...assistantNotifications,
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

// BEGIN workspace screens: convenience types
export type CaptureSource = z.output<typeof captureSourceSchema>;
export type LessonPatch = z.input<typeof lessonPatchSchema>;
export type LessonInput = z.input<typeof lessonInputSchema>;
export type AudioInspection = z.output<typeof audioInspectionSchema>;
export type AudioImportItem = z.input<typeof audioImportItemSchema>;
export type QueueJobAction = z.output<typeof queueJobActionSchema>;
export type ModelPreferences = z.output<typeof modelPreferencesSchema>;
export type ModelSelection = z.output<typeof modelSelectionSchema>;
export type ModelCatalog = z.output<typeof modelCatalogSchema>;
export type ModelChoice = z.output<typeof modelChoiceSchema>;
export type DoctorReport = z.output<typeof doctorReportSchema>;
export type LibraryTranscript = z.output<typeof libraryTranscriptSchema>;
// END workspace screens: convenience types
