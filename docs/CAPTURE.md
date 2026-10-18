# MyNotes Capture

Capture is the native macOS 14+ menu-bar recorder included with MyNotes 0.2.
It records selected lectures through the Mac microphone and works with the chat
closed. Configuration goes through the Capture tab in MyNotes, your agent or the CLI. Capture never
starts transcription, AI generation, or Word publication.

## Capture tab in MyNotes

Open **Capture** beside the MyNotes tab. Connect an HTTPS/webcal calendar with
its timezone, teaching period and title-to-course mappings. Calendar cards show
the last successful refresh, next attempt and failures; refresh one calendar or
all connected calendars. The subscription URL remains private.

Upcoming lessons are ordered by time and searchable. Select a lesson to inspect
its origin, times, timezone and recording eligibility. **Modifica** saves local
time/title changes and recording exclusions without writing to the external
calendar. These changes survive refresh; **Ripristina calendario** restores the
latest source values. Conflicts, cancellations and unresolved events are explicit.
An externally cancelled or disappeared event cannot be re-enabled locally.

**Aggiungi lezione** schedules a special lesson for an existing course.
**Registra ora** requests a recording with course, number and duration when
Capture is running. A pending request is not proof that recording started;
the UI confirms recording only from the recorder's reported state. Opening the
tab does not activate Capture or request microphone permission. Editing a
currently recorded/exported lesson is blocked, and Stop remains available while
a calendar refresh runs.

The tab polls local state every 15 seconds while visible and no form is open.
Calendar downloads remain explicit or follow Capture's configured refresh cycle.

## Set up through the agent

Tell the agent: “Here is my teaching calendar; configure Capture”, or give it an
ICS/iCal subscription link. Provide the teaching period, timezone, course names
and official lesson numbers when they are not present in the calendar. The agent
normalizes documents/text into JSON; the recorder does not interpret documents.
For a feed without official lesson numbers, give the first lesson number for each
course. Later imports preserve assigned numbers. New occurrences receive the next
unused number; inserted earlier lessons may need manual verification.

Configure and inspect first. Activate with `capture enable` when ready. macOS
asks for microphone access and optional notifications. Run the menu's 60-second
microphone test and listen to its output before relying on scheduled recordings.

All commands accept `--workspace PATH`; otherwise they use `MYNOTES_WORKSPACE`
or the usual private MyNotes workspace. JSON files must stay in that workspace.

```sh
mynotes capture configure --workspace "$MYNOTES_WORKSPACE"
mynotes capture import --workspace "$MYNOTES_WORKSPACE" --file timetable.json
mynotes capture schedule --workspace "$MYNOTES_WORKSPACE"
mynotes capture enable --workspace "$MYNOTES_WORKSPACE"
mynotes capture status --workspace "$MYNOTES_WORKSPACE"
```

## Complete timetable input

`import --file` accepts a list or an object with an `events` list:

```json
{
  "events": [
    {
      "id": "quantitative-session-1",
      "courseCode": "QNT9",
      "lessonNumber": 1,
      "startsAt": "2026-10-12T10:00:00+02:00",
      "endsAt": "2026-10-12T11:30:00+02:00",
      "timezone": "Europe/Rome",
      "title": "Quantitative Methods"
    }
  ]
}
```

The course must already exist in MyNotes. IDs are optional for static timetables;
the default is based on course and lesson. Reimport replaces the static timetable,
retains other feed sources and marks removed static events cancelled. An existing
ID cannot change course or lesson number. Eligible values are `SCHEDULED`,
`CANCELLED`, and `NEEDS_REVIEW`. Explicit offsets are required for ambiguous local
times around daylight-saving transitions.

## ICS/iCal input

```json
{
  "sourceID": "semester-calendar",
  "url": "https://calendar.example.invalid/teaching.ics",
  "timezone": "Europe/Rome",
  "periodStart": "2026-10-01T00:00:00+02:00",
  "periodEnd": "2026-12-31T23:59:59+01:00",
  "mappings": [
    {"match": "Quantitative Methods", "courseCode": "QNT9", "firstLesson": 1}
  ]
}
```

```sh
mynotes capture subscribe --file calendar-source.json
mynotes capture sync
mynotes capture schedule
```

`match` is a case-insensitive substring of the event title. Only matched lectures
are imported; multiple matches require review. Known `L01`, `Lesson 1`,
`Lecture 1`, and `Lezione 1` title forms supply official numbers. Otherwise the
configured first number starts a stable sequence. Recurrence identity uses the
source ID, UID and original occurrence time, including moved exceptions.

HTTPS and `webcal://` subscriptions are supported (`webcal` becomes HTTPS).
Links may contain private subscription tokens; they are stored privately and
are excluded from diagnostics. Google Calendar `/calendar/embed?src=...` pages
with a single calendar are resolved automatically to their public iCal feed.
If that feed is unavailable, use the private iCal address from Google Calendar
settings; Capture does not change sharing permissions. Other browser pages,
OAuth login, CalDAV and calendar editing are unsupported. Selected all-day events or events without
start/end times must be corrected before import.

The app checks refresh deadlines every five minutes, refreshes due sources at
startup/wake, and defaults to six hours between successful updates. HTTP validators
avoid unchanged downloads. Temporary network failures retry after fifteen minutes
and preserve the last valid schedule. Invalid links, unavailable feeds and other
errors requiring correction suspend automatic retries. Explicit sync always
allows a manual retry. Explicit cancellations disable the event; unexplained
disappearance becomes `NEEDS_REVIEW` and suppresses automatic recording. The
teaching period is bounded to 550 days, feeds to ten sources and downloads to ten
MB each. Refreshes require no AI runtime.

## Settings and manual controls

`configure --file` accepts any of these fields:

```json
{
  "refreshHours": 6,
  "recordBeforeSeconds": 0,
  "recordAfterSeconds": 120,
  "outputDirectory": "/tmp/MyNotes-Recordings"
}
```

Refresh can range from fifteen minutes to one week; each margin is 0–1800 seconds.
For consecutive lectures, only margins are shortened; real overlaps and duplicate
course/lesson targets are marked `CONFLICT`. Other lectures continue normally.
Reloads retain the active recording's event and destination snapshots. Changing its
output directory through the CLI requires stopping/exporting first.

```sh
mynotes capture start --course QNT9 --lesson 8 --duration-minutes 90
mynotes capture stop
mynotes capture pause
mynotes capture resume
mynotes capture test
mynotes capture recover
mynotes capture recover --session ATTEMPT-ID
mynotes capture disable
```

Manual windows must last at most twelve hours. The menu can start the selected
upcoming lecture early; unscheduled course/lesson/duration setup goes through the
agent. Pause is persistent and stops future automatic starts, not an active
recording. Stop saves the current audio. Disable requires stopping and finishing
the export first. Quitting closes the segments and exits without immediate
LaunchAgent restart; login starts the enabled app again. Crashes trigger restart.

Control commands default to waiting ten seconds (maximum thirty). `PENDING` means
the app has not acknowledged the request; inspect `status` or the private response
file before claiming execution. Requests expire after sixty seconds and are
idempotent by request ID. A stop request is acknowledged only after export succeeds
or fails. `RECORDING` acknowledges actual microphone acquisition, not file delivery.

## Storage, partial audio and recovery

The menu shows "Saving audio" while an export is active. Keep Capture open until
it finishes. A failed recording remains visible while Capture is enabled and
unpaused, instead of being replaced by the next scheduled lecture. Inspect the
runtime/session details and preserved segments before attempting recovery.

Configuration: `config/capture.json`. Timetable and runtime: `state/capture/`.
Versions are retained under `state/capture/history/`; commands use a local file
mailbox with responses and attempt manifests. Audio staging is in local application
support, outside synced course folders. Keep the operational workspace local.
Agree on the original-audio folder with the user before the first recording,
then save it with `transcription-queue folder --path PATH`. The legacy default is
`recordings/capture/`; the confirmed user folder may be on a synced drive.
Capture shows the folder and offers Open folder / Change folder.

Audio is mono 44.1 kHz AAC at 128 kbps, segmented every five minutes. Export
validates every segment and total duration, then exclusively renames the finished
file in its destination filesystem. Existing files are retained. Coverage metadata
stays in private native sessions and the recording catalogue; historical sidecars
remain readable for compatibility. Hidden staging and microphone tests are excluded.
All supported audio names appear in the library. Successful validated transcription
exports TXT under `Trascrizioni/COURSE` (or `Senza corso`), then moves the unchanged
original into `processate`. Normalized audio, JSON, caches and logs remain private.
Export/archive failures can be retried without running speech recognition again.

Sleep/termination closes the current segment and preserves the attempt. A wake
within the window can resume the same unambiguous interrupted attempt; the result
is marked partial because of its gap. Startup reconciles stale in-progress state.
Closed, unambiguous interrupted attempts are recovered after their window ends.
Multiple attempts require explicit selection rather than picking the largest.
Missing/corrupt segments fail export, with staged audio retained for inspection.
Staged data is not automatically deleted in this version.

The Mac must be awake at the recording time; lid closure and power-off prevent
capture. Capture prevents idle system sleep only while recording. It does not
schedule hardware wake. A live LaunchAgent alone does not prove audio readiness:
check permission, runtime state, test audio and file duration.

## Build and release

```sh
uv sync --locked
uv run pytest
uv run python scripts/test_capture_native.py
uv run python scripts/build_capture.py --native-only
uv run python scripts/build_release.py
```

The release builder produces a universal arm64/x86_64 app and includes it in the
hashed archive. The installer copies the app with Capture disabled for a new
workspace. Existing active Capture is restarted only while idle; an exclusive
recording/export lease protects updates and rollback. Rollback to a version that
does not contain Capture requires disabling Capture first. Private workspaces
and staged recordings are never removed by the installer.

Set `MYNOTES_CAPTURE_SIGNING_IDENTITY` to a Developer ID certificate for a
distribution signature. Without it, builds are explicitly local ad-hoc signed
candidates and may require renewed microphone permission after an upgrade.
Notarization and public release are separate steps. `MYNOTES_CAPTURE_APP` selects
a development app bundle; `MYNOTES_CAPTURE_HOME` isolates local test runtime.


## Verified connection and repair (0.2.11)

Use `mynotes capture connect --file calendar-source.json --json` for a verified
connection. It accepts the subscription payload above, or `importMode: all_timed`
with optional mappings. The command resolves the URL, downloads once and validates
the calendar before writing a new source or schedule. A valid feed with no events
in the selected period is a successful connection with a warning. The selected
period/timezone is not replaced by Google display parameters.

`subscribe` remains available for existing scripts. Synchronization also resolves
Google embed links saved by earlier versions, preserving the source ID, event
identities, recording IDs and local overrides. Successful resolution stores the
feed URL. Correct a source in the UI with **Correggi collegamento**; replacing a
populated source with a different calendar requires a new connection. A failed
source with no successful import or events can be corrected in place.

JSON failures provide `code`, `message`, `retryable` and `suggestedAction` without
provider response bodies or secret URLs. The dashboard includes these optional
fields while remaining readable with legacy metadata. `capture source --source ID
--json` returns the private subscription only for explicitly editing that source;
it must not be included in public diagnostics.


### Indicative transcript timestamps

Argmax transcription uses timestamps for passages rather than individual words.
They are engine-produced navigation cues, not precise word synchronization.
The technical glossary and selected language/model remain in use. A transcript
with invalid intervals is retained for review; sorting or inventing times does
not certify it. Confidence may be an explicitly labelled log-probability proxy
or unavailable, and should not be read as a percentage of correct words.

## Recording filenames

Course recordings use the familiar short calendar label and lesson number, such
as `QNT_L14.m4a`, `STAT_L12.m4a` and `DEMO_L11.m4a`. The persistent recording ID
remains private metadata and does not replace these filenames. A short calendar
label can name the file before course association; this does not assign a course.
Free recordings without a lesson retain their title-based unique filenames.
The recording collection is chosen by the user; an existing `registration agent`
folder can be selected. Existing originals are never overwritten.


## Selective local verification

New Argmax passes use segment timestamps without a decoder prompt. Timestamps
are indicative navigation aids; word timing precision is not promised. The
saved language override and course language remain authoritative. Unknown or
model-derived confidence is not treated as measured agreement.

Timing defects, abnormal repetition and decoder quality warnings identify local
windows. The configured FluidAudio v2 tool verifies only those windows, with
five seconds of context and at most ninety seconds per clip. Clips use exact
PCM sample indices. Overlapping windows are merged, and verification stops
before calling FluidAudio if it would cover more than 30% of the recording.
Each window receives at most one automatic pass. After the independent pass,
text disagreements, different numbers and uncertain boundaries are nonblocking
warnings. The result becomes **Trascritta**, ready for notes; doubtful passages
are marked in the TXT and alternatives remain in private reports. Where alignment
is uncertain, usable primary text is retained; invalid primary intervals still
require genuine, validated secondary timing. Residual quality warnings never
mean human verification. Changed sources, invalid final timing, missing engines,
incomplete independent passes and the 30% scope limit still block completion.

**Ripara passaggi dubbi** (`transcription-queue recover --job ID`) reuses a saved
primary report only after verifying the original hash, attempt identity, report
integrity and normalized audio. Valid clip results can be reused across recovery
attempts. **Trascrivi da zero** always bypasses old pass and clip results. Both
paths retain raw reports, previous text, alternatives and provenance privately.
No final TXT or original-file move occurs until validation succeeds. Already
validated legacy final results remain reusable with the same source/settings;
legacy filenames or matching durations alone never establish source identity.

Configure an existing local FluidAudio CLI through the course transcription
`tools.fluidaudio` path. No engine/model upgrade is needed. If that tool is
unavailable and a repair is required, the attempt stops with a recoverable
explanation. Capture, direct chat transcription and lesson preparation all use
the same pipeline, shared ASR slot and finalization.


### Queue controls and unassigned transcripts

`transcription-queue clear --json` removes pending jobs only and preserves pause,
originals and previous attempts. `pause` preserves pending jobs for `resume`.
A recovery integrity mismatch never authorizes reuse of a mismatching report:
attested normalized audio is reused only with its intact source/report binding;
otherwise one fresh local ASR attempt is allowed and recorded for that command.
There is no recursive or paid automatic retry.

`capture pending --json` includes external and manual recordings and ranked local
course candidates. `capture assign --file PATH --json` accepts associationState
(`resolved` by default, or `estimated`), a reason and alternative course codes.
Estimates require a reason and cannot override resolved user choices. Conflicting
lesson numbers are retained as proposals on the independent recording, without
overwriting an occupied destination. Valid unassigned TXT exports are in
`Trascrizioni/Dubbi`; originals still archive in `processate`.
