# Versioned updates and recovery

## 0.2.25: recording names and associations

- Separate numeric groups in filenames are no longer joined into a lesson number.
  Filename-derived metadata follows Finder renames; explicit assignments and
  capture/calendar links retain their precedence.
- Lesson IDs consistently accept numbers 1–999. Missing lesson context or optional
  material extraction falls back to the course ASR profile instead of rejecting
  queue admission. Truly unknown courses still export completed TXT into `Dubbi`.
- Pending jobs refresh their association before admission; active settings remain
  frozen. Existing transcript text, personal TXT names and cancelled work survive
  reconciliation. The source-bound lesson and coordinated Word behavior from
  0.2.24 remains available.

## 0.2.24: source-bound lessons and coordinated notebooks

- Source contributions can span lessons while retaining origin and evidence
  identity. A provisional source association remains distinguishable from
  confirmed teaching coverage; valid existing text is reused before generation.
- Scoped revisions preserve prior decisions and valid content. Coordinated updates
  publish one cumulative Word with a backup and atomic replacement, preserving
  personal additions and reporting localized conflicts. Undo remains available.
- Codex, Agy and Claude integration instructions follow the same source and
  revision rules. Existing course batches are not resumed by upgrading.

## 0.2.21-0.2.23: queue execution and real-file libraries

- Shared local ASR slots, precise elapsed time and serialized queue controls
  distinguish preparation, inference and finalization. Closing the window stops
  new admissions while active jobs finish saving.
- Recordings follow the selected audio collection and its archives. Transcripts
  follow actual TXT files and remain usable without original audio. Refresh does
  not recreate deleted TXT files or resurrect removed originals.
- Transcript rename changes the actual TXT independently of the audio. Agents
  inspect all unassigned TXT candidates and can assign and move them without new
  ASR, preserving manual edits and avoiding overwrites.
- Optional verification failures retain available primary text with an explicit
  warning. Unreliable timestamps are omitted rather than invented; an unknown
  course does not make a saved transcript incomplete.

## 0.2.20: usable transcripts with disclosed uncertainty

Since the previous public 0.2.13 preview:

- Capture reads supported audio from the chosen originals folder, including
  externally added recordings. Technical files remain private. Completed
  transcripts export as course-organized TXT and unchanged originals archive
  under `processate`; the historical `processed` folder remains readable.
- The recording library and queue share backend actions for removal, targeted
  interruption, retry, fresh transcription, selective recovery and incomplete
  archiving. Transcript work can run while another recording is being captured.
- WhisperKit uses indicative segment timestamps without a decoder prompt.
  FluidAudio checks doubtful local windows. After a completed independent check,
  residual text disagreements become warnings rather than blocking the lesson.
  The TXT and note writer retain uncertainty markers and private alternatives.
  Structural timing errors, changed sources, unavailable tools and incomplete
  checks still block publication. The selective 30% audio limit remains.
- Stable personal notebooks and course handouts remain distinct. `prepare`,
  `generate`, then `update` reuse saved preferences, publish with backups in
  automatic mode and support `undo`. External Word changes still stop updates.
- Operational agent requests take precedence over optional onboarding.
  Single-call writing is the default; deliberate multi-part writing remains an
  explicit choice. No automatic paid retry is introduced.

Completion means usable automatic transcription, not human verification or a
measured accuracy guarantee. This preview remains ad-hoc signed and is not
Apple-notarized. Existing recordings are not automatically retranscribed on
update. Keep the previous runtime and a private backup; update only when the
recorder and transcription workers are idle.

Download the intended new DMG, replace the MyNotes setup app in Applications, and reopen it. Component setup prepares a versioned runtime and switches the `current` launcher target only after its checks pass. Completed setup/download work is reused on retry. Earlier runtime versions remain available for rollback. The update is explicit; no unattended GitHub publication or automatic legacy migration is performed.

Capture holds an exclusive lease during recording and export. Both update and rollback defer while it is held. For an active, idle Capture workspace, the installer stops and restarts the LaunchAgent around the update and refreshes its Python runtime path. Rolling back to a version without Capture requires disabling the recorder first. Failed dependency installation preserves the previous selection and removes the incomplete installation.

After an app update, check microphone access again with a short recording and playback. A successfully verified executable alone does not verify microphone authorization or the audio you need.

## Advanced recovery

The retained manual ZIP installer supports `--list-versions` and `--rollback VERSION`:

```sh
zsh scripts/install-mynotes.sh --list-versions
zsh scripts/install-mynotes.sh --rollback VERSION
```

Run these from a verified standalone release with its required Python available. They do not overwrite course preferences or generated handouts. `mynotes setup` revisits component checks; `mynotes doctor --json` reports next actions separately for installation, provider, transcription, Word and Capture.

No current release performs a private-workspace schema/database migration. Before introducing one, create a timestamped backup and document the transition. Updates must never reprocess lectures, replace generated handouts or discard personal settings.

For a development clone, preserve local modifications before updating. Do not use an automated update to discard them.


## 0.2.11 candidate: calendar connection repair

This candidate fixes Google Calendar embed links being accepted as feeds and then
failing with a generic parser error. Single-calendar Google embed URLs now resolve
to their public iCal feed; unavailable public calendars prompt for a private iCal
subscription address. There is no Google OAuth or sharing-permission change.

A verified connection is saved only after the feed has downloaded and parsed.
Existing failed sources can be corrected in place, preserving IDs, exclusions,
associations, recordings and transcripts. Error messages distinguish HTML pages,
network/TLS failures, unavailable feeds, invalid calendars and incompatible helpers.
Permanent errors wait for correction or manual retry; transient failures retain
the fifteen-minute retry and the last successful timetable.

Install through the normal versioned updater. App, Python backend and Capture
helper must come from the same candidate package. No course database migration
or retranscription is required. Earlier runtime versions remain available for
rollback. Validation and publication status must be reported with the candidate;
this entry alone does not imply a published or installed release.


## 0.2.12: reliable local writing recovery and Word reuse

Leading-zero evidence aliases are normalized only when uniquely supported by the
request inventory. Every change is reported; raw results, source packs and original
failed-process records remain immutable. `recover-batch` diagnoses without writes;
`--apply` can certify a complete verified SUCCESS stream rejected by an old local
reference check. Incomplete, changed or genuinely failed model results remain blocked.
Content defects are reported together where independent; they never trigger a
silent paid rewrite. `batch-status` identifies local review and preserves active time.

UNKNOWN speakers no longer block ordinary lecture-supported insight. Explicit
professor focus warns if the voice cannot be isolated; no speaker is invented.
Shared writing keeps its cumulative budget and reserves two minutes for local work.
Cumulative usage snapshots are counted once per conversation, with thinking and
cache separate. Word failures preserve validated content for local-only rebuilding.

Standalone builds contain just the selected lesson. `build-batch --mode both`
creates separate and fresh cumulative candidates from structured content, with
hashes and reuse checks. Building does not publish academic notes. Existing Google
Calendar conversion/repair, recordings and transcription associations are retained.

Upgrade through the normal versioned installer. No workspace migration, new academic
ASR, lecture rewriting or source conversion is required. Busy Capture defers the
update; previous runtime versions remain available for rollback. This release is
prepared and verified locally; that does not imply hosted CI, Apple notarization,
GitHub publication or new real-model generation acceptance.


## 0.2.13: quiet Capture updates and usable saved recordings

Capture refreshes local status silently, preserves the current view and discards
obsolete reads after commands such as Stop. Active work is checked every five
seconds and idle views every fifteen, while visible and foreground; calendar
subscription synchronization remains separate. Idle calendar updates no longer
scan the audio library each time. Window queue heartbeat remains independent.

The recording archive uses All / To transcribe / Transcribed, with the Trascrivi
button and bulk action retained. Saved partial audio remains transcribable with
its coverage warning; missing association is separate from transcription state.
Actual audio/ASR errors include a cause and next action. Existing valid results
remain visible even for partial recordings.

`transcribe --recording ID` and matching `transcription-status` work without a
course/lesson or open Capture window. Lesson/audio selection resolves Capture
identity first and reuses the same locks, saved profile and validated transcript.
No automatic ASR starts when recording ends. Existing external audio and material
paths stay supported; plain text never acquires invented transcript timestamps.

Update using the normal versioned installer. No workspace migration, academic
rewriting or automatic retranscription is required. Previous versions remain for
rollback; an active recording/export defers installation. Package, installed-app,
UI and real-microphone checks are separate from synthetic regression results.

## 0.2.14 — local Capture reliability candidate

Original-audio catalogue, arbitrary external filenames, private working files,
course TXT exports and recoverable archival. Queue removal/reinsertion, active
cancellation and fresh retry share backend action availability with the native UI.
Independent stable recordings may transcribe while microphone capture continues.
No automatic transcription or note generation. Public publication is separate.


## 0.2.15 — integrated local candidate

Integrates the Capture recording collection and recoverable queue with stable
course notebooks, automatic Word updates with safety backups, and undo. Concrete
requests take precedence over optional course-context setup in every managed
integration. Agy uses a single-turn writing request by default in both CLI and
direct adapter calls; explicit multipart recovery remains available. Evidence
stays complete in bounded input files. Performance and real-provider validation
are reported separately from synthetic regression tests. No public release is
implied by this local candidate.


## 0.2.16 — segment timing for local Argmax transcription

New Argmax attempts keep model-derived segment timestamps and the saved glossary,
without requesting word alignment. This avoids the WhisperKit 1.1.0 prompted
word-timing defect tracked upstream as issue 542. Timing mode is recorded in
results and pass caches; existing valid complete transcripts remain reusable.
Missing confidence is no longer treated as certainty. Average log probability,
when available, supplies a labelled proxy rather than a calibrated accuracy.
Timestamp failures include the segment, interval and specific violation.
Original audio and failed attempt diagnostics remain preserved.
