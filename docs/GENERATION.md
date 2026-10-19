# Model selection and coordinated notes

MyNotes keeps these shortcuts: **Gemini Flash 3.8 High**, **Gemini Flash
3.8 Medium**, and **GPT Terra High**. They are separate from the dynamic CLI
catalog and are enabled only when their model and effort are reported.
New unset choices use Agy High or Terra
High; explicitly saved choices, including an agent-default choice, are retained.
The setup shows default effort, selected effort, and catalog/access status.
Changing preferences affects the next chat and future generation, not a chat
already running. A model being listed is not proof of subscription access.

```sh
mynotes models --provider codex --refresh --json
mynotes models --provider agy --refresh --json
mynotes preferences --provider agy --model gemini-3.8-flash-medium --effort medium
```

An explicit Refresh action queries the installed CLI without generation: Codex app-server metadata,
Agy's model-list command, or Claude's SDK initialization protocol. New model and
effort identifiers appear without an application release. Saved models absent
from the new catalog are removed from the menu while the previous selection is
retained for identification. Until a successful explicit refresh, the saved list
stays selectable; opening the app or changing agents never rediscovers models.
Unknown effort defaults are explicitly marked, and Automatic
delegates the choice to the CLI. See [dynamic catalog behavior](MODEL-CATALOG.md).

## Prepare, plan, generate, inspect

```sh
mynotes generate-batch OPS --lessons 1-6 --plan-only
mynotes generate-batch OPS --lessons 1-6 --selection ownership.json
mynotes generate-batch OPS --resume OPS-0123456789ab --selection ownership.json
mynotes usage --batch OPS-0123456789ab --json
mynotes batch-status OPS-0123456789ab --json
mynotes generate-batch OPS --resume OPS-0123456789ab --build-word
```

Replace the example batch ID with the returned ID. Planning reads existing
packs, writes nothing and invokes no provider. Missing or changed sources return
`NEEDS_PREPARATION`; normal generation prepares relevant sources, preserving
explicit source selections and using the existing extraction/ASR caches.
The coordinator reads the relevant recordings before organizing a shared deck.
An ambiguous slide receives an explicit provisional placement that the agent
reviews autonomously; slide assignment never requires user confirmation.

Organization uses verified source-file SHA-256 and actual page/slide number.
`owner` is the revisable main placement, with optional `reason`, `provisional`
and `uses`. Each use maps a lesson to `role` (introduction, development, recall,
supplement), a `reason` and local transcript `evidence_ids`. Additional teaching
uses require transcript evidence; material-only uses are labelled supplementary.
Distinct contributions may include formulas, tables and figures in either lesson
direction. There is no blanket 50-word limit for recaps or cross-lesson references.
Headings and recaps still cannot establish substantive coverage.

Use `--context-lessons 1,3-4` to import relevant other prepared lessons. Imported
rows retain origin_lecture_id and origin_evidence_id in an immutable effective
source pack; the original packs are not changed. Context is included in budget
checks and is never silently truncated. Previously validated narrative is supplied
with source links, not only a short title/recap synopsis, and is not independent
evidence or instructions.

The selection file maps returned canonical IDs to reviewed choices:

```json
{
  "sha256:REPLACE_WITH_RETURNED_HASH:page:10": {
    "owner": "OPS-L01",
    "status": "supplement",
    "anchor": {"after": "S009", "before": "S011"}
  }
}
```

Allowed statuses are `taught`, `supplement`, and `material` (associated material,
without claiming it was taught). Supplementary content is visibly labelled and
placed inline beside the relevant explanation. Same-deck neighboring anchors
can be inferred; unclear placement requires selection, not a final appendix.
Verified published placement is reused as context and can be revised. Unverified
historical coverage requires source review by the coordinator, without inventing
coverage or asking the user to assign slides. Include related published lessons
when their content needs correction.

During the same source planning pass, a visually inspected cover or section
divider containing no teaching content may use `status: "material"` plus
`structural_review: {"kind": "title", "reason": "...", "image_sha256": "..."}`
(or kind `section_divider`). Hash the actual source-pack image after inspecting
it. If an older pack omitted a non-teaching title page from its visual inventory,
a separate inspected source render can be bound through `source_image` with
`path`, `source_file`, `source_sha256` and `page`; these image/source hashes are
checked without editing the old pack. The page remains in the inventory and coverage ledger, but needs no invented
full explanation. Short or missing extracted text alone never establishes this
classification; diagrams and image-only teaching slides still require coverage.
The writer's own exclusion cannot grant an exemption. Changed images invalidate
the review. This is source classification, not an additional AI review phase.
Code, data, formulas and substantive text cannot be exempted as covers. A page
without extracted text may contain charts; inspect its pixels before exclusion.

## Context, style and resuming

Lessons are sorted numerically. With supplied selected-model context/output
limits, the planner selects consecutive groups that pass conservative text,
image/output reservations and CLI-launch checks. `--budgets FILE` accepts
`verified_for_model`, `context_tokens`, `output_tokens`,
`output_reserve_per_lesson`, and `visual_tokens_per_image`. Missing model limits
use single-lesson groups with shared continuity. This is not proof that unknown
model limits or hidden CLI overhead fit; evidence is never silently truncated.

Standard quality remains one writing phase. Course context, learner profile,
detail, lists, formulas and image preferences are shared with generation.
`--instructions TEXT` applies temporary instructions. `--image-limit N` caps
inserted figures per lesson; zero inserts none, while source-image inspection
needed for table accuracy remains required. Persistent caps use `image_limit`
in global/course style JSON; `null` preserves the existing image policy.

Integrity-checked checkpoints reuse completed groups and preserve interrupted
work. Source, ownership, model, effort or instruction changes invalidate their
affected dependencies. Edited/corrupt candidates stop for inspection rather than
automatically spending quota. Shared provider attempts are stored and counted
once; per-lesson candidates reference them without multiplying consumption.
Counter semantics and unknown values remain separate; there is no inferred bill.

### Single-turn default and optional bounded Agy portions

Agy writing uses one single-turn request by default, including large source packs.
Inputs over 32,000 UTF-8 bytes are staged losslessly in evidence files bounded
by 20,000 bytes instead of one oversized inline message. No source rows are omitted.
There is no guarantee of a sub-minute response or immunity to network failures.

Explicit `MYNOTES_AGY_PARTS=1` selects one conversation/process with consecutive
portions of up to 12 blocks. `MYNOTES_AGY_PARTS=auto` retains the previous
size-based selection for diagnostics and existing workflows. This is still
one writing phase: no general AI review is added. Sources are staged losslessly
once; continuations keep the conversation, model, effort and style. Explicitly requested review phases keep their existing single-turn path.

Each portion requires its own SUCCESS result and an integrity-checked checkpoint.
The assembled notes still pass the full coverage, duplicate, style and Word
validators; partial or ERROR replies cannot become a finished candidate. A local
validation defect reuses the saved transport instead of automatically regenerating
the lesson. Explicit count/percentage contradictions are blocked for targeted
correction without an additional AI review call.

The active lesson budget is 20 minutes after source preparation, including
recovery and targeted correction, with the final 30 seconds reserved for Word.
The writer stops after 300 seconds without useful progress; repeated identical
events do not reset that timer. A deadline is a failure bound, not
a promise of successful completion or a word limit. Completed portions are
retained on cancellation. Real progress reports accepted portion counts and
elapsed time, never invented completion percentages. `MYNOTES_AGY_PARTS=0`
selects the default single-turn path explicitly, without changing the saved
model/effort. CLI and direct adapter calls use the same default without changing
the parent process environment. Usage is the CLI's cumulative session snapshot, not a sum of
the same counters across portions or resumes; cache/thinking are not added again.

Temporary transport failure permits one automatic resume after 10 seconds with
the same model and conversation. The conversation ID is saved at initialization,
even before the first portion. Authentication, quota, corrupt input and a second
transport failure stop; the coordinator must not add further retry loops.
Only the current job prevents idle system sleep; manual/lid sleep is respected.
On wake the stale transport is closed. Active time and elapsed/suspended time are
reported separately. Attempt logs record actual start and end timestamps.

Resume first revalidates retained SUCCESS writing under revised organization.
A separate revision record retains before/after choices and affected lessons.
Source-bound original receipts and manifests remain unchanged; reuse under a new
validation policy is explicitly recorded as organization_revalidation, never as
an equivalent historical prompt. Unchanged complete work is not regenerated.

One managed hash-anchored targeted correction per affected lesson and revision
may add/attach missing coverage or replace, move or delete explicitly defective
blocks. All unrelated passages remain unchanged; source coverage is validated
again. A durable attempt guard and inherited active-time budgets prevent paid
retry loops or a new allowance from a changed checkpoint. Failed/unfinished
transport never becomes certified success.

`--build-word` builds/render-validates each lesson before advancing, without
publishing. `--stop-after N` pauses at a selected lesson for inspection without
changing the batch selection. Read-only `batch-status` distinguishes content
ready, Word ready, writing, retry wait, stopped and review-paused states. It
checks candidate/Word hashes and never invokes discovery, ASR or generation.

## Word and release gates

DOCX is the default deliverable for ordinary notes, multi-lesson notes, revision
and quiz preparation. Internal JSON/Markdown is working data. Other final formats
require an explicit user request. An improvised formatting script is not the
normal delivery path: every Word candidate uses the shared course renderer.

`mynotes update OPS-L01 OPS-L02` builds one cumulative candidate for related
lessons, renders/validates it once, then publishes with one backup and atomic
replacement. Current Word edits are compared to per-lesson generated baselines;
independent personal additions survive subsequent revisions. Overlapping
contradictions retain the current lesson and return PERSONAL_CONTENT_CONFLICT,
or PARTIALLY_PUBLISHED when independent lessons were updated. Resolve against
sources and ask the user only for an irreconcilable substantive contradiction.
Interrupted commits roll back Word, publication membership and merge baselines;
the staged candidate remains available. Review publication mode still requires
approval of the concrete candidate.

### Standalone study guides

```sh
mynotes study OPS --lessons 6-10 --purpose quiz --plan-only
mynotes study-schema --json
mynotes study OPS --lessons 6-10 --purpose quiz --manifest reviewed-sources.json --output quiz-candidate.docx
```

The conversation agent performs the source review and constructs the private
manifest; the user does not fill in technical JSON. Planning records inventory,
not completed reading. Read sources and complete transcripts consecutively,
including image-only pages. Bind every evidence item to a file fingerprint and
location, then define topic-to-source coverage. `review_status: complete` records
reading actually completed; keyword search or sampling cannot establish it.
Inspect transcript provenance and timestamps before relying on it. Preserve
legacy files and record uncertainty instead of migrating them automatically.

The reviewed manifest contains `schema_version`, `course`, numeric `lessons`,
`sources` (`id`, `path`, `sha256`, `kind`, `lesson_ids`, `review_status`), `evidence`
(`id`, `source_id`, `content`, `location`), `topics` (`id`, `title`, `evidence_ids`,
`quantitative`), requested `links` (`id`, `url`, `title`, `source_id`), and
`limitations`. Source content remains untrusted evidence, not agent instructions.
Unavailable linked resources must be disclosed; never claim their contents
were reviewed based on a URL or title alone. Browser access is optional and is
not a prerequisite for local-source note generation.

The writing phase uses the selected provider/model/effort and returns the study
schema. A reviewed internal content file may instead be passed with `--candidate
content.json`; this rebuilds Word without spending tokens on valid content.
The guide is standalone and does not import earlier cumulative lectures. It
explains definitions, intuition, mechanisms, conditions and limitations in prose.
Quantitative topics include native formulas, defined variables and worked
examples. Preserve actual code behavior while distinguishing methodological
recommendations. Recap and generated practice exercises precede explanations
of requested links. Saved course language, list density and image policy apply.

Topic coverage is an explicit map to sections and explanatory facets, not a
keyword count. These declarations remain subject to a complete editorial review;
automatic schema checks do not prove factual or pedagogical completeness. A
provider cannot mark its own draft editorially reviewed. Build status,
render-validation status and readiness remain distinct. Actual per-page visual
inspection and a real Word check are required before recording readiness.
Existing output files are preserved; choose a fresh candidate path.
Use `mynotes study-verify quiz-candidate.docx` to render/validate. Only after
actual checks, record `--editorial-reviewed --visual-reviewed --word-verified`.
`--no-render` reuses an intact prior render; the flags never perform the named
reviews automatically and must never be used to bypass them.

### Direct transcription

```sh
mynotes transcribe OPS-L10 --workspace PRIVATE_WORKSPACE --json
mynotes transcription-status OPS-L10 --workspace PRIVATE_WORKSPACE --json
```

Only the selected recording is resolved and hydrated. The direct command handles
ASR preflight and shares its glossary/cache with preparation. Progress uses
stderr; stdout is a structured result. Read-only status never starts work.
`BUSY` returns exit 3 without queueing or terminating the owner; `NEEDS_REVIEW`
returns exit 4 with preserved raw paths and issues; ordinary failures return 2.
One workspace ASR slot and the shared per-lesson lock prevent conflicting work.
Fresh attempt folders and validated hashes prevent stale results from being
accepted. Timestamp anomalies beyond 0.5 seconds require review. Changed
transcripts invalidate previously prepared packs. No generation or Office
conversion is needed to transcribe.

Build each lesson candidate against the current cumulative document, inspect it,
then use `mynotes update COURSE-LNN` for the automatic safety-backup and atomic-update flow. Course publication mode `review` returns an approval preview instead. Building alone does not publish. Do not build several candidates against an old cumulative baseline
and publish them blindly in succession.

New output uses editable, rectangular native Word tables and 1.5 cm margins on
every side of every section, including inherited sections. Unambiguous legacy
Markdown tables can be recovered locally; malformed tables block validation.
Backslash notation in formulas and literal text is preserved. Word corrections
can reuse saved note content, without another provider call. Existing academic
documents are not repaired or changed in place by these development changes.

Validate source changes and real output before assigning a new version or
building the DMG. Unit tests, native compilation, real provider runs, rendered
page inspection, Microsoft Word checks and isolated installer checks are
distinct evidence. A skipped visual check never counts as passed.

### Single-turn default and optional bounded Agy portions workers

Agy generation selects `mynotes-note-writer`, a private, per-attempt agent with
`view_file` for the exact staged text inputs/source images and `finish` to return the structured
result. Omitting `finish` makes Agy force additional generations even after the
model has written a complete response. It does not inherit the interactive
MyNotes coordinator role. The full response schema is embedded in the writing
prompt as well as supplied through the CLI structured-output flag. New responses
keep legacy parallel arrays empty; ordered blocks contain the complete narrative
without a second copy in sections, bullets, formulas or table. Shell/code
exploration, plugins, onboarding and nested MyNotes calls are outside the writing
worker's task. The user's global Agy profile is unchanged.

Repeated evidence metadata is declared once by identifier prefix; every included
utterance, exact identifier and timestamp remains available. This is payload
compaction, not source summarization or removal. `standard` remains one writing
phase. Oversized Agy user messages use application-staged input files instead of
a monolithic prompt: trusted configuration, untrusted source metadata, and ordered
JSONL evidence chunks. Every source row is retained unchanged in the compact
representation. Planning computes the same file list without creating files.
The writer must read every listed file fully, including additional line ranges
when the viewer truncates its first response. A real input-integrity probe
recovered five exact beginning/middle/end references through files, while the
monolithic user message failed that same check. This is not itself a lesson
quality or latency guarantee. A successful transport is saved before local validation, so a local
rejection is retained for review without another automatic paid generation.
Unvalidated returned responses do not count as completed phases.

The writer uses a 19-minute CLI deadline and a 20-minute parent-process bound,
with cleanup of its own process group on timeout/interruption. These bounds stop
a stalled attempt; they are not a guarantee of successful lesson completion.
The application never automatically retries a paid attempt, falls back to another
model or changes effort. Agy may retry streaming internally within that one
attempt; those retries remain included in elapsed time and reported usage. Full errors and
usage remain in private attempt files; the coordinator receives a short error.
An ERROR envelope can never become a ready candidate, even with recoverable JSON.
Historical batch candidates referencing failed transports cannot be built or
published to Word. Never edit the runtime or weaken coverage checks to bypass a
failure. Keep content, transport success and actual Word completion distinct.

### Local recovery, speaker focus and independent Word candidates

`recover-batch COURSE-0123456789ab --json` diagnoses saved writing portions
without writes, ASR or provider calls. Add `--apply` only to certify a verified,
complete SUCCESS stream that an older local reference check rejected. Recovery
compares staged inputs, portion order, conversation, immutable attempt records
and hashes before applying normal validation. A separate receipt records local
recovery; the failed process remains failed. Missing portions, ERROR results,
modified inputs and unproven provenance remain blocked. Content defects are
reported for review; recovery never silently switches to paid rewriting.
Ordinary batch resume checks compatible saved writing before invoking a model.

A shared reference normalizer only resolves leading-zero differences in evidence
fields when one canonical identifier exists in the same source context. Exact
identifiers win; ambiguous or unknown references are rejected. Prose, numbers,
raw provider responses and source packs are unchanged. Conversion reports retain
the original reference, canonical reference and reason. Duplicate or contradictory
coverage decisions still fail validation after normalization.

The default speaker focus is `lecture`. UNKNOWN voices do not invalidate insight
supported by the current lesson transcript, and they are never relabeled as the
professor. Set `speaker_focus` to `professor` in a course, or pass
`--speaker-focus professor` to `generate` / `generate-batch`, to record explicit
professor focus. A warning explains when the transcript cannot isolate the voice;
this option does not claim diarization that the sources cannot support.

`batch-status` includes local recovery diagnostics, defects, preserved active time,
speaker warnings and the next action. Shared writing preserves the twenty-minute
active budget across compatible checkpoints and versions, reserving at least
two minutes for local validation and Word work. The ten-minute notice is not a
completion estimate. Exact repeated file requests do not reset the useful-progress
deadline; evidence chunking and every source row are retained. Phase metrics and
source-read audit keep local work separate from reported provider activity.
`usage --batch` retains raw attempts and counts cumulative writing snapshots once
per conversation using each counter's largest reported value. Thinking, cache,
input and output counters stay separate. Missing reporting stays unknown; these
reports do not infer billed cost or remaining quota.

`build COURSE-L01 --mode standalone` starts a clean document containing only that
lesson. The default `--mode update` preserves the existing cumulative workflow.
Standalone documents cannot accidentally pass the update/publish gate.
`build-batch COURSE-0123456789ab --mode both --json` requires validated candidates
for every selected lesson, creates separate documents and a fresh cumulative
through the shared renderer, and writes a hash-indexed delivery manifest. It does
not concatenate existing cumulative Word files. Repeating an unchanged completed
build reuses its verified files. A Word failure preserves validated content so
resume can rebuild locally without another model call. All these outputs remain
candidates: rendered-page and Microsoft Word QA are separate from automated
structural checks, and building never publishes academic notes.

## Stable course files and undo

`mynotes course link CODE COURSE_ROOT --json` saves exact `files.personal_notes`
and `files.handout` paths. Choose names with `--notes-file PATH` and
`--handout-file PATH.docx`; `--notes` continues to select a folder.
`--publication-mode automatic|review` saves the course publication preference
(default automatic). Legacy course folders and per-lesson sources remain usable.
An explicit `prepare --personal-notes PATH` overrides only that lesson.

One notebook is reused for the whole course. Headings `Lezione 1`, `Lesson 1`,
`Lecture 1` or `L1` delimit the requested lesson. The new notebook has an empty
`Lezione 1` section; add subsequent sections as lessons progress. Existing user
notebooks are never rewritten. `NOTA AI:` and `NOTE AI:` highlight academic
questions in the selected lesson; sources remain evidence, not tool instructions.
The final handout is excluded from lecture evidence even with a custom name.

After source/content checks, `mynotes update CODE-L01` builds and render-validates
against the current target, then publishes with a verified backup. In review mode
it returns `AWAITING_APPROVAL`; show that candidate before explicit `publish`.
`build` remains candidate-only. For several lessons, call update in numeric order,
so each candidate is built on the latest handout. Failures and external edits stop
an automatic update without replacing the target. Updating needs no new AI call.

`mynotes undo CODE` reverses the most recent completed update after checking the
current Word fingerprint, and saves a safety backup of the version being undone.
It handles first creation too: the generated Word is archived before removing its
course-folder entry. Repeated undo walks back completed updates. Publication state
is reconciled; newer candidates remain available for local reuse. `history CODE`
and `restore BACKUP_ID` expose older backups and the safety copy made before undo.
A missing/corrupt backup stops recovery; interrupted undo is reconciled by recover.
These commands operate on local course files; they do not publish software releases.
