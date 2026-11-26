# MyNotes serve: JSON-RPC for desktop apps

**In breve.** `mynotes serve --stdio` è un unico processo di lunga durata che
risponde a richieste JSON-RPC 2.0, una per riga, su stdin e stdout. Un'app
desktop non avvia più un processo Python per ogni azione e non interroga più
quattro comandi ogni cinque secondi: riceve notifiche quando i file cambiano.
I comandi esistenti restano invariati; ogni risultato è il JSON che il comando
stampa. Ciò che può chiamare un agente, la trascrizione, LibreOffice o la rete
gira come job in un processo separato, che sopravvive al riavvio del server.
L'assistente della chat (`assistant.*`) usa lo stesso agente di `mynotes chat`
e manda la risposta a pezzi, con notifiche, mentre l'agente lavora.

This page is the contract a desktop app can be built against, protocol 1.
`mynotes serve --list-methods --json` prints the same method table from the
code; a client should check its method names against it.

## Starting and stopping

```sh
mynotes serve --stdio --workspace "$WORKSPACE"
```

- The workspace is resolved like every command (`--workspace`, then
  `MYNOTES_WORKSPACE`, then the default) and passed to every command the
  server runs. The server does not initialize it.
- Before anything else runs, the server moves the JSON-RPC streams to private
  file descriptors. File descriptor 0 then reads `/dev/null` and descriptor 1
  writes to stderr, so a stray `print` or a child process (an agent's
  `--version` probe, a queue runner) can neither corrupt nor read the channel.
  Child processes never inherit the channel descriptors.
- Stderr carries human-readable logs only. Do not parse it.
- End of stdin, or `system.shutdown`, stops the server. Assistant replies
  are stopped first, before anything is waited for (see Assistant). Then
  queued requests get `-32800`, requests already running finish and are
  answered (a `job.wait` answers at once with the job as it is), the queue
  lease lapses within 15 s as after a crash, and running jobs keep running.

## Framing

- One UTF-8 JSON object per line (`\n`), at most 16 MiB. A longer line gets
  `-32600` with `id: null` and is skipped; the server continues.
- JSON-RPC 2.0. Batches (arrays) are rejected with `-32600`.
- A request's `id` is a string or an integer, unique among requests in flight
  (`-32600` otherwise). `params` is an object or absent.
- Responses can arrive in any order: lanes run concurrently.
- The only client notification is `$/cancelRequest {id}`. It cancels a request
  that has not started yet (answer: `-32800`); a running request finishes.
  Use `job.cancel` for jobs. Other client notifications are ignored.
- Server notifications have a `method` and `params` and no `id`.

## Handshake

The first call should be `system.hello {"protocol": 1}`. A different protocol
gets `-32005` with `data.supported`. The result lists every method and topic.

```sh
printf '{"jsonrpc":"2.0","id":1,"method":"system.hello","params":{"protocol":1}}\n' \
  | mynotes serve --stdio --workspace "$WORKSPACE"
```

## Results

A method backed by a command returns `{exitCode, data}`, where `data` is
`json.loads` of the command's stdout. Exit codes other than 0 can still carry
data: `doctor` exits 1 when not ready, `course link` exits 1 with
`NEEDS_SELECTION`, `notes` exits 2 with `NEEDS_TRANSCRIPT`. A command whose
output is not JSON but that exits 0 returns `{exitCode: 0, data: null, text}`.
`tests/test_serve_parity.py` checks, for every fast method, that `data` equals
the CLI's stdout on the same workspace.

Parameters are the command's flags in camelCase. Parameters of type `json`
whose flag takes a file are written to a private file in
`state/desktop-requests/` (mode 0600, the same place the Windows app uses),
passed by path and deleted when the command ends.

## Errors

| Code | Meaning | `data` |
| --- | --- | --- |
| -32700 | Invalid JSON or UTF-8 | — |
| -32600 | Invalid request, batch, duplicate id, line over 16 MiB | — |
| -32601 | Unknown method | — |
| -32602 | Invalid parameters: unknown, missing, wrong type or choice | sometimes `topics` |
| -32603 | Internal server error | — |
| -32001 | The command failed with text such as `mynotes: Course not found: X`; `message` is that text without the prefix | `{exitCode, stdout, stderr}` |
| -32002 | BUSY: exit 3 with `status: BUSY` | `{exitCode, data}` |
| -32003 | NEEDS_REVIEW: exit 4 with `status: NEEDS_REVIEW` | `{exitCode, data}` |
| -32004 | Unknown job id | — |
| -32005 | Unsupported protocol version | `{supported}` |
| -32006 | The server is shutting down | — |
| -32007 | Unknown assistant session | — |
| -32008 | A reply is still running in this conversation, or 3 replies already run | — |
| -32009 | `library.transcript`: unknown recording, or no transcript yet | `{lectureId}` or `{recordingId}` |
| -32800 | Request cancelled before it started | — |

## Lanes

| Lane | Runs | Used for |
| --- | --- | --- |
| fast | in the server, 4 threads | read-only commands |
| slow | in the server, 2 threads | commands that write local files, probe agent CLIs (`doctor`), copy or hash audio |
| job | `python -I -X utf8 -m mynotes.rpc_job`, own session (Unix) or Windows job object | anything that can call an agent, transcription, LibreOffice or the network, or that installs signal handlers |
| server | in the server, 4 threads (`job.wait`: its own 8) | `system.*`, `session.*`, `watch.*`, `job.*` |

`system.ping` and `system.shutdown` use no lane: the thread that reads
requests answers them, so four slow reads in the fast lane never delay the
supervisor's liveness check.

`assistant.start` and `assistant.send` are listed in the job lane: they answer
at once and the reply runs in a separate provider process (see Assistant).
Unlike a job, that process reads from a pipe and ends with the server.

In-process commands run the unchanged `cli.main()`. Thread-local stand-ins for
stdout and stderr capture each call's own output, so it is the text the
command prints in its own process; stdin is always at end of file, so an
interactive prompt ends with the existing "Onboarding paused" path. The first
call on a workspace that does not exist yet runs alone, so that two threads
never create it together. Shared state is protected as in separate processes:
settings, capture and queue writes take the same file locks (`flock` locks
belong to each open file, so they exclude threads too) and write atomically.

Reviewed per method: no fast or slow method installs a signal handler, calls
an agent to generate, starts transcription, renders with LibreOffice, or
downloads. `signal.signal` calls in providers, ASR and model discovery
already skip non-main threads, and those paths are job methods anyway.
`capture.start`, `.stop`, `.test` and `.recover` always pass
`--wait-seconds 0`: they return `PENDING` with a `requestID` at once, and the
server sends `capture.requestCompleted` when the recorder answers.
`doctor.get` is cached for 60 s per provider; `refresh: true` probes again.

## Jobs

A job method answers at once with `{jobId, job}`. The job runs
`mynotes <command>` in its own process. Its stdout and stderr go to files in
`state/desktop-jobs/`, never to pipes, so it keeps running if the server or
the app exits.

- `job.started {jobId, method, params, pid}`.
- `job.progress {jobId, stage, ...}` for every stderr line that is a JSON
  object with a `stage` (the format `notes` and `lesson process` already
  print). For `transcription.run` the server also reads the persisted ASR
  status every 2 s: `stage: TRANSCRIBING`, `phase`, `status`, `elapsedSeconds`.
- `job.output {jobId, stream: "stderr", text}` for other stderr lines.
- `job.finished {jobId, state, exitCode, result?, error?, resumeHint?}`.
  `result` and `error` are what the command's output maps to, as above.

Job (from `job.get`, `job.list`, `job.wait`, `job.cancel`): `id`, `method`,
`params`, `state` (`RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`,
`INTERRUPTED`), `pid`, `startedAt`, `finishedAt`, `exitCode`, `result`,
`error`, `lastProgress`, `cancelRequested`, `resumeHint`.

**Cancellation.** `job.cancel {jobId}` interrupts the job, waits 10 s,
terminates it, waits 10 s, then kills its process group. On Unix the
interrupt is SIGINT and the termination SIGTERM; both become
`KeyboardInterrupt` in the job, so commands stop through their existing
interrupt paths (a notes run saves its work and reports `INTERRUPTED`), and
code that installs its own SIGTERM handler while an agent or a transcription
engine runs keeps it. On Windows the job has no console for CTRL_BREAK: the
server creates a cancel file that the job watches, and the last step closes
the job object. `force: true` kills at once. A cancelled job ends `CANCELLED`.

**Restart.** Each job holds `<id>.json.lock` for its whole life and writes
`<id>.json.exit` last. A restarted server watches again every job whose lock
is held, finalizes one that ended meanwhile from its exit file, and marks one
that died without an exit file `INTERRUPTED`. `INTERRUPTED` and `CANCELLED`
jobs carry `resumeHint {method, params}`: `notes.run` with `resume` set to
the saved batch for notes and lesson jobs, otherwise the same call again.
Records are kept; nothing is deleted.

## Transcript text

`library.transcript {lectureId | recordingId, maxBytes?}` answers
`{exitCode: 0, data: {path, text, truncated, encoding, size}}`, computed in the
server (fast lane, read-only). The caller names a lesson or a recording, never
a file: the path comes from MyNotes' records. For a lesson, in order: the
transcripts the lesson registry links to it, the text of its recordings, the
library transcripts assigned to it or named after it (the rule `notes` uses),
the transcript imported into its source pack (`prepare --transcript`,
`lesson process --transcript`), then `runs/<lesson>/asr/transcript.txt`. For a
recording: its exported text, its transcript text, then the library
transcripts linked to it. A normalized `.json` transcript is read through its
`.txt` sibling.

- At most `maxBytes` bytes are read: 2 MiB by default, from 1 byte to 8 MiB.
  `truncated` says whether the file is longer, `size` is its length in bytes,
  and a truncated read never ends inside a UTF-8 character.
- `encoding` is `utf-8`, `utf-8-sig` (the byte-order mark is removed) or
  `latin-1` for a file that is not valid UTF-8.
- A lesson id that is not `COURSE-LNN` gets `-32602`; an unknown recording,
  or a lesson or recording without a transcript, gets `-32009`.

## Watching instead of polling

`watch.subscribe {topics}` returns the current snapshot of each topic, then
sends `<topic>.changed` only when a snapshot's hash changes.

| Topic | Data | Same as |
| --- | --- | --- |
| `capture` | Capture dashboard | `capture.dashboard` |
| `queue` | Queue status | `queue.status` |
| `library` | Recordings, lessons, transcripts | `library.list` |
| `config` | Private configuration | `config.get` |

Snapshot: `{topic, hash, data}`, or `{topic, hash, data: null, error}` when
the computation fails (for example before the workspace is initialized).

Every second while the window is visible (every 2 s once nothing has changed
for 30 s and no queue job runs, no capture request is awaited and no request
arrived; every 5 s after `session.setVisible {visible: false}`), the server
compares the modification
time, size and inode of each topic's files and folders. MyNotes writes JSON
atomically (a temporary file, then a rename), so a folder's own signature
changes on every write inside it. The queue database is compared by SQLite's
`data_version`, which changes only when another connection commits data, and
the window lease by its session; the lease renewal rewrites both every 5 s
without changing any answer. A topic is recomputed only when its
signature changes, after its refresh interval for time-dependent fields
(capture and queue 10 s, library 30 s, config never), or every 2 s while a
queue job is running. `watch.snapshot {topic}` returns the cached snapshot
under the same rule, or recomputes with `fresh: true`; one thread computes a
topic at a time and the others reuse its result.

## Window session and the transcription queue

Queue jobs start only while a window lease is fresh. `session.open` renews
that lease every 5 s with a new session UUID (the 15 s lease the native apps
renew with `transcription-queue heartbeat`). A queue runner is started only
when the queue has queued, running or cancelling jobs (or a recorded memory
hold to re-check) and no runner holds `runner.lock`, so an idle open window
starts no process. `session.close`
marks the window closed, which stops new queue jobs; send it when the window
closes to the tray, unless the user chose background transcription
(`release: false` only stops renewing). If the server dies, the lease lapses
within 15 s.

This rule lives in `transcription_queue.launch_runner()` itself
(`runner_active()` and `runner_needed()`), so the heartbeat of the macOS and
Windows apps no longer starts a runner that would exit at once.

## Assistant

The assistant panel talks to the same chat agent as `mynotes chat`, with the
same startup prompt (`chat.startup_prompt`), the same workspace rules
(`AGENTS.md`, or the managed skill when a user-owned rules file wins) and the
same MyNotes skills. Each message is one non-interactive turn of the agent
CLI, started in the workspace with `MYNOTES_WORKSPACE` and
`MYNOTES_CHAT_ACTIVE=1`; the prompt goes to stdin. The first prompt adds the
page the user is on (`context`, as data) and one paragraph about the panel:
nobody can approve a permission request there, and HTML is not rendered.

| Agent | First turn | Next turns |
| --- | --- | --- |
| Claude Code | `claude --print --output-format stream-json --verbose --include-partial-messages --permission-mode dontAsk --tools Bash,Read,Glob,Grep --allowedTools Bash(mynotes:*),Read,Glob,Grep --append-system-prompt-file <rules> [--model M] [--effort E]` | the same, plus `--resume <session_id>` |
| Codex | `codex exec --json --skip-git-repo-check -C <workspace> --sandbox workspace-write [--model M] [-c model_reasoning_effort="E"] -` | the same, then `resume <thread_id> -` |
| Antigravity | `agy --agent mynotes --mode accept-edits --add-dir <workspace> [--model M] [--effort E] --input-format stream-json --output-format stream-json --print-timeout 0`, one stream-json user message on stdin, closed after the `result` event | the same, plus `--conversation <conversation_id>` |

- **Permissions.** Claude Code may run `mynotes` commands and read files,
  nothing else: other tools are not loaded, and anything that would ask is
  denied (`dontAsk`), never `--dangerously-skip-permissions`. Codex keeps the
  workspace-write sandbox of `mynotes chat`. Antigravity uses the managed
  `mynotes` agent, as `mynotes chat` does.
- **Resume.** The provider's own session id (`session_id`, `thread_id`,
  `conversation_id`) is kept as `providerSession`. If a provider reported
  none, the next turn sends the startup prompt again with the last 6
  exchanges (12 000 characters at most) as a transcript, and the
  conversation's `resume` becomes `replay`.
- **Stop.** `assistant.cancel` interrupts the turn's process group (SIGINT),
  waits 5 s, terminates it (SIGTERM), waits 5 s, then kills it; `force: true`
  kills at once. On Windows the turn runs in a job object, which is closed. A
  turn also ends after 30 minutes. When the agent exits, what is left of its
  process group (a shell, a tool) is terminated too.
- **Shutdown and restart.** Closing the server kills running turns: they end
  `CANCELLED`. A transcript that a crash left `RUNNING` reads back as
  `INTERRUPTED`.
- **Transcripts.** `state/assistant/<sessionId>.json`, mode 0600, folder 0700:
  the messages, the streamed text, tool rows, errors and usage.
- **Parsing.** One JSON object per line. Unknown event types are ignored;
  invalid JSON lines and lines over 8 MiB are skipped and counted in the
  turn's `invalidLines`; a reply keeps at most 200 000 characters
  (`truncated: true`), a turn at most 200 tool rows.

Notifications, in order for one turn: `assistant.delta` and `assistant.tool`
while the agent works, then exactly one of `assistant.done` or
`assistant.error`. They are sent only after the `assistant.start` or
`assistant.send` answer.

| Notification | Params |
| --- | --- |
| `assistant.delta` | `{sessionId, turnId, text}`: new text, to append |
| `assistant.tool` | `{sessionId, turnId, toolId, name, summary, status, command, durationMs}`; `status` is `running`, `completed`, `failed` or `denied`; the same `toolId` is sent again when it changes |
| `assistant.done` | `{sessionId, turnId, text, providerSession, usage?}`; `text` is the whole reply |
| `assistant.error` | `{sessionId, turnId, code, message}`; `code` is `QUOTA`, `AUTH`, `NETWORK`, `TIMEOUT`, `UNAVAILABLE` (CLI not installed), `CANCELLED` or `PROVIDER`, from the existing provider error classes |

Conversation (from `assistant.get`): `id`, `provider`, `model`, `effort`,
`providerSession`, `resume`, `title`, `createdAt`, `updatedAt`, `turns`. Turn:
`id` (`turn-1`, `turn-2`, ...), `message`, `context`, `state` (`RUNNING`,
`COMPLETED`, `FAILED`, `CANCELLED`, `INTERRUPTED`), `text`, `tools`, `error`
(`{code, message}`), `usage`, `startedAt`, `finishedAt`, `invalidLines`.

`context` is `{course?, lesson?, lectureId?, page?}`: a course code, a lesson
number from 1 to 999, a lesson id such as `STAT-L03`, and a page label of at
most 80 characters. `message` is 1 to 20 000 characters.

## Methods
| Method | Lane | Params | Result | Notifications | Errors |
| --- | --- | --- | --- | --- | --- |
| `assistant.cancel` | fast | `sessionId`: string<br>`force`?: boolean | `{sessionId, turnId, cancelRequested}` | `assistant.error` (`CANCELLED`) | -32602, -32007 |
| `assistant.get` | fast | `sessionId`: string | Conversation | — | -32602, -32007 |
| `assistant.list` | fast | `limit`?: integer (1-200) | `{sessions: [{id, title, provider, model, effort, resume, createdAt, updatedAt, turns, state, running}]}` | — | -32602 |
| `assistant.send` | job | `sessionId`: string<br>`message`: string<br>`context`?: json | `{sessionId, turnId}` | `assistant.delta`, `assistant.tool`, `assistant.done`, `assistant.error` | -32602, -32007, -32008 |
| `assistant.start` | job | `message`: string<br>`provider`?: string (codex \| agy \| claude)<br>`model`?: string<br>`effort`?: string<br>`context`?: json | `{sessionId, turnId, session}`; the chat role's agent and model unless given | `assistant.delta`, `assistant.tool`, `assistant.done`, `assistant.error` | -32602, -32001, -32008 |
| `capture.addLesson` | slow | `lesson`: json | `{exitCode, data}` of `mynotes capture add-lesson` | — | -32602, -32001 |
| `capture.assign` | slow | `assignments`: json | `{exitCode, data}` of `mynotes capture assign` | — | -32602, -32001 |
| `capture.configure` | slow | `config`?: json | `{exitCode, data}` of `mynotes capture configure` | — | -32602, -32001 |
| `capture.confirmTest` | slow | `audio`: string<br>`audibleConfirmed`: boolean | `{exitCode, data}` of `mynotes capture confirm-test` | — | -32602, -32001 |
| `capture.connect` | job | `source`: json | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes capture connect` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `capture.dashboard` | fast | — | `{exitCode, data}` of `mynotes capture dashboard`, computed in-process | — | -32602, -32001 |
| `capture.disable` | job | — | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes capture disable` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `capture.editLesson` | slow | `event`: string<br>`patch`: json | `{exitCode, data}` of `mynotes capture edit-lesson` | — | -32602, -32001 |
| `capture.enable` | job | — | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes capture enable` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `capture.import` | slow | `events`: json | `{exitCode, data}` of `mynotes capture import` | — | -32602, -32001 |
| `capture.pause` | slow | — | `{exitCode, data}` of `mynotes capture pause` | — | -32602, -32001 |
| `capture.pending` | fast | — | `{exitCode, data}` of `mynotes capture pending` | — | -32602, -32001 |
| `capture.recover` | slow | `session`?: string | `{exitCode, data}` of `mynotes capture recover --wait-seconds 0` | `capture.requestCompleted` | -32602, -32001 |
| `capture.resetLesson` | slow | `event`: string | `{exitCode, data}` of `mynotes capture reset-lesson` | — | -32602, -32001 |
| `capture.resume` | slow | — | `{exitCode, data}` of `mynotes capture resume` | — | -32602, -32001 |
| `capture.schedule` | fast | — | `{exitCode, data}` of `mynotes capture schedule` | — | -32602, -32001 |
| `capture.setImportMode` | slow | `source`: string<br>`mode`: string (mapped \| all_timed) | `{exitCode, data}` of `mynotes capture set-import-mode` | — | -32602, -32001 |
| `capture.source` | fast | `source`: string | `{exitCode, data}` of `mynotes capture source` | — | -32602, -32001 |
| `capture.start` | slow | `course`?: string<br>`lesson`?: integer<br>`unassigned`?: boolean<br>`title`?: string<br>`language`?: string<br>`durationMinutes`: integer | `{exitCode, data}` of `mynotes capture start --wait-seconds 0` | `capture.requestCompleted` | -32602, -32001 |
| `capture.status` | fast | — | `{exitCode, data}` of `mynotes capture status` | — | -32602, -32001 |
| `capture.stop` | slow | — | `{exitCode, data}` of `mynotes capture stop --wait-seconds 0` | `capture.requestCompleted` | -32602, -32001 |
| `capture.subscribe` | slow | `source`: json | `{exitCode, data}` of `mynotes capture subscribe` | — | -32602, -32001 |
| `capture.sync` | job | `ifDue`?: boolean<br>`source`?: string | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes capture sync` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `capture.test` | slow | — | `{exitCode, data}` of `mynotes capture test --wait-seconds 0` | `capture.requestCompleted` | -32602, -32001 |
| `capture.unsubscribe` | slow | `source`: string | `{exitCode, data}` of `mynotes capture unsubscribe` | — | -32602, -32001 |
| `config.get` | fast | — | `{exitCode, data}` of `mynotes config show` | — | -32602, -32001 |
| `course.add` | slow | `name`: string<br>`notesLanguage`?: string<br>`materials`?: string<br>`recordings`?: string<br>`contextAnswer`?: string<br>`settings`?: json<br>`deferContext`?: boolean | `{exitCode, data}` of `mynotes course add` | — | -32602, -32001 |
| `course.deferContext` | slow | `code`: string | `{exitCode, data}` of `mynotes course defer-context` | — | -32602, -32001 |
| `course.edit` | slow | `code`: string<br>`course`: json | `{exitCode, data}` of `mynotes course edit` | — | -32602, -32001 |
| `course.link` | slow | `code`: string<br>`path`: string<br>`notes`?: string<br>`notesFile`?: string<br>`handoutFile`?: string<br>`publicationMode`?: string (automatic \| review)<br>`recordings`?: string<br>`dryRun`?: boolean | `{exitCode, data}` of `mynotes course link` | — | -32602, -32001 |
| `course.pending` | fast | — | `{exitCode, data}` of `mynotes course pending` | — | -32602, -32001 |
| `doctor.get` | slow | `provider`?: string (codex \| agy \| claude)<br>`refresh`?: boolean | `{exitCode, data}` of `mynotes doctor`, plus `cached` | — | -32602, -32001 |
| `job.cancel` | server | `jobId`?: string<br>`force`?: boolean | Job (`cancelRequested: true`) | `job.finished` (`CANCELLED`) | -32602, -32004 |
| `job.get` | server | `jobId`?: string | Job | — | -32602, -32004 |
| `job.list` | server | `limit`?: integer | `{jobs: [Job]}` | — | -32602 |
| `job.wait` | server | `jobId`?: string<br>`timeoutSeconds`?: number | Job, when it ends or at the timeout | — | -32602, -32004 |
| `lesson.process` | job | `lectureId`: string<br>`audio`?: string<br>`transcript`?: string<br>`resume`?: string<br>`regenerate`?: boolean<br>`provider`?: string (codex \| agy \| claude)<br>`quality`?: string<br>`instructions`?: string<br>at most one of `audio`, `transcript`, `resume`; at most one of `resume`, `regenerate` | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes lesson process`; `transcript` skips transcription and imports the file as `prepare --transcript` does; `resume` (a batch id) and `regenerate` are those of `notes`: after a failed paid writing attempt, which a rerun refuses to repeat, `regenerate` starts a new generation and keeps the old attempts | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001, -32002, -32003) |
| `library.associate` | slow | `recordingId`: string<br>`course`: string<br>`lesson`?: integer | `{exitCode, data}` of `mynotes transcription-queue associate` | — | -32602, -32001 |
| `library.associateAudio` | slow | `recordingId`: string<br>`eventId`?: json<br>`course`?: json<br>`lesson`?: json | `{exitCode, data}` of `mynotes transcription-queue associate-audio` | — | -32602, -32001 |
| `library.courseProfile` | fast | `course`: string | `{exitCode, data}` of `mynotes transcription-queue course-profile` | — | -32602, -32001 |
| `library.download` | job | `recordingId`: string | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes transcription-queue download` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `library.editLesson` | slow | `edit`: json | `{exitCode, data}` of `mynotes transcription-queue lesson-edit` | — | -32602, -32001 |
| `library.excludeAudio` | slow | `recordingId`: string | `{exitCode, data}` of `mynotes transcription-queue exclude-audio` | — | -32602, -32001 |
| `library.finish` | slow | `recordingId`: string | `{exitCode, data}` of `mynotes transcription-queue finish` | — | -32602, -32001 |
| `library.folder` | slow | `path`: string | `{exitCode, data}` of `mynotes transcription-queue folder` | — | -32602, -32001 |
| `library.importAudio` | slow | `paths`?: json<br>`items`?: json<br>`eventId`?: json<br>`course`?: json<br>`lesson`?: json<br>exactly one of `paths`, `items` | `{exitCode, data}` of `mynotes transcription-queue import-audio` | — | -32602, -32001 |
| `library.inspectAudio` | slow | `paths`: json | `{exitCode, data}` of `mynotes transcription-queue inspect-audio` | — | -32602, -32001 |
| `library.lessons` | fast | — | `{exitCode, data}` of `mynotes transcription-queue lessons` | — | -32602, -32001 |
| `library.list` | fast; slow with `sync` | `query`?: string<br>`date`?: string<br>`course`?: string<br>`sync`?: boolean | `{exitCode, data}` of `mynotes transcription-queue library`, computed in-process | — | -32602, -32001 |
| `library.preferAudio` | slow | `recordingId`: string | `{exitCode, data}` of `mynotes transcription-queue prefer-audio` | — | -32602, -32001 |
| `library.prepareTrash` | slow | `recordingId`: string | `{exitCode, data}` of `mynotes transcription-queue prepare-trash` | — | -32602, -32001 |
| `library.relink` | slow | `recordingId`: string<br>`path`: string | `{exitCode, data}` of `mynotes transcription-queue relink` | — | -32602, -32001 |
| `library.rename` | slow | `recordingId`: string<br>`title`: string | `{exitCode, data}` of `mynotes transcription-queue rename` | — | -32602, -32001 |
| `library.renameTranscript` | slow | `transcript`: string<br>`name`: string | `{exitCode, data}` of `mynotes transcription-queue rename-transcript` | — | -32602, -32001 |
| `library.restoreAudio` | slow | `recordingId`: string | `{exitCode, data}` of `mynotes transcription-queue restore-audio` | — | -32602, -32001 |
| `library.transcript` | fast | `recordingId`?: string<br>`lectureId`?: string<br>`maxBytes`?: integer<br>exactly one of `recordingId`, `lectureId` | `{exitCode: 0, data: {path, text, truncated, encoding, size}}`, computed in-process (see Transcript text) | — | -32602, -32009 |
| `library.transcripts` | fast; slow with `sync` | `query`?: string<br>`date`?: string<br>`course`?: string<br>`sync`?: boolean | `{exitCode, data}` of `mynotes transcription-queue transcripts` | — | -32602, -32001 |
| `library.verify` | slow | — | `{exitCode, data}` of `mynotes transcription-queue verify-library` | — | -32602, -32001 |
| `memory.reset` | slow | `scope`: string (global \| course)<br>`area`: string (notes \| dialogue)<br>`course`?: string<br>`expectedRevision`: string | `{exitCode, data}` of `mynotes memory reset` | — | -32602, -32001 |
| `memory.show` | fast | `scope`: string (global \| course)<br>`area`: string (notes \| dialogue)<br>`course`?: string | `{exitCode, data}` of `mynotes memory show` | — | -32602, -32001 |
| `memory.update` | slow | `scope`: string (global \| course)<br>`area`: string (notes \| dialogue)<br>`course`?: string<br>`expectedRevision`: string<br>`set`?: object<string><br>`remove`?: string[]<br>`style`?: json<br>`inherit`?: string[] | `{exitCode, data}` of `mynotes memory update` | — | -32602, -32001 |
| `models.catalog` | fast | `provider`: string (codex \| agy \| claude)<br>`saved`?: string<br>`savedEffort`?: string | `{exitCode, data}` of `mynotes models` | — | -32602, -32001 |
| `models.refresh` | job | `provider`: string (codex \| agy \| claude)<br>`saved`?: string<br>`savedEffort`?: string | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes models --refresh` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `notes.batchStatus` | fast | `batchId`: string | `{exitCode, data}` of `mynotes batch-status` | — | -32602, -32001 |
| `notes.history` | fast | `course`?: string | `{exitCode, data}` of `mynotes history` | — | -32602, -32001 |
| `notes.plan` | slow | `course`: string<br>`lessons`?: string<br>`resume`?: string<br>`regenerate`?: boolean<br>`provider`?: string (codex \| agy \| claude)<br>`quality`?: string<br>`instructions`?: string<br>`imageLimit`?: integer<br>`selection`?: json<br>`budgets`?: json<br>`speakerFocus`?: string (lecture \| professor)<br>`contextLessons`?: string<br>`contiguousTranscripts`?: boolean | `{exitCode, data}` of `mynotes notes --plan-only` | — | -32602, -32001 |
| `notes.publish` | job | `lecture`: string<br>`candidate`?: string | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes publish` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `notes.restore` | slow | `backupId`: string | `{exitCode, data}` of `mynotes restore` | — | -32602, -32001 |
| `notes.run` | job | `course`: string<br>`lessons`?: string<br>`resume`?: string<br>`regenerate`?: boolean<br>`provider`?: string (codex \| agy \| claude)<br>`quality`?: string<br>`instructions`?: string<br>`imageLimit`?: integer<br>`selection`?: json<br>`budgets`?: json<br>`speakerFocus`?: string (lecture \| professor)<br>`contextLessons`?: string<br>`contiguousTranscripts`?: boolean | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes notes` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `notes.undo` | slow | `course`: string | `{exitCode, data}` of `mynotes undo` | — | -32602, -32001 |
| `preferences.get` | fast | — | `{exitCode, data}` of `mynotes preferences --show` | — | -32602, -32001 |
| `preferences.saveModels` | slow | `models`: json<br>`expectedRevision`: string | `{exitCode, data}` of `mynotes preferences` | — | -32602, -32001 |
| `profile.save` | slow | `background`?: string<br>`skip`?: boolean<br>exactly one of `background`, `skip` | `{exitCode, data}` of `mynotes profile` | — | -32602, -32001 |
| `provider.login` | job | `provider`: string (codex \| agy \| claude) | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes provider-login` | `job.started`, `job.progress`, `job.output`, `job.finished`, `provider.authChanged` | -32602; the command's error in `job.finished.error` (-32001) |
| `provider.status` | slow | `provider`: string (codex \| agy \| claude) | `{exitCode: 0, data}`: the agent check that `mynotes doctor` runs | — | -32602 |
| `queue.clear` | slow | — | `{exitCode, data}` of `mynotes transcription-queue clear` | — | -32602, -32001 |
| `queue.configure` | slow | `parallel`: integer | `{exitCode, data}` of `mynotes transcription-queue configure` | — | -32602, -32001 |
| `queue.enqueue` | slow | `allUntranscribed`?: boolean<br>`recordingId`?: string<br>`readyRecordings`?: string[]<br>`parallel`?: integer<br>exactly one of `allUntranscribed`, `recordingId`, `readyRecordings` | `{exitCode, data}` of `mynotes transcription-queue enqueue` | — | -32602, -32001 |
| `queue.job` | slow | `action`: string (retry \| restart \| recover \| remove \| cancel)<br>`jobId`: string | `{exitCode, data}` of `mynotes transcription-queue` | — | -32602, -32001 |
| `queue.pause` | slow | — | `{exitCode, data}` of `mynotes transcription-queue pause` | — | -32602, -32001 |
| `queue.resume` | slow | — | `{exitCode, data}` of `mynotes transcription-queue resume` | — | -32602, -32001 |
| `queue.status` | fast | — | `{exitCode, data}` of `mynotes transcription-queue status`, computed in-process | — | -32602, -32001 |
| `session.close` | server | `release`?: boolean | `{ok}` | — | — |
| `session.open` | server | `visible`?: boolean | `{sessionId}` | — | — |
| `session.setVisible` | server | `visible`?: boolean | `{visible}` | — | -32602 |
| `speech.installModel` | job | — | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes install-speech-model` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `speech.verify` | job | `audio`?: string<br>`language`?: string (en \| it) | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes verify-transcription` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |
| `style.edit` | slow | `course`?: string<br>`style`?: json<br>`reset`?: boolean<br>exactly one of `style`, `reset` | `{exitCode, data}` of `mynotes style edit` | — | -32602, -32001 |
| `style.preview` | slow | `course`?: string<br>`output`?: string | `{exitCode, data}` of `mynotes style preview` | — | -32602, -32001 |
| `style.show` | fast | `course`?: string | `{exitCode, data}` of `mynotes style show` | — | -32602, -32001 |
| `system.hello` | server | `protocol`?: integer | `{protocol, server, version, python, platform, pid, workspace, methods, topics, limits}` | — | -32005 |
| `system.ping` | server | — | `{ok, uptimeSeconds}` | — | — |
| `system.shutdown` | server | — | `{ok, jobsRunning}`, then the server exits | — | — |
| `system.stats` | server | — | `{pid, uptimeSeconds, cpuSeconds, childCpuSeconds, rssBytes, threads, requests, notifications, runnerSpawns, jobsStarted, jobsRunning, assistantRunning, spawns, topicComputations}` | — | — |
| `transcription.run` | job | `lectureId`?: string<br>`recordingId`?: string<br>`audio`?: string<br>exactly one of `lectureId`, `recordingId` | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes transcribe` | `job.started`, `job.progress`, `job.output`, `job.finished`; `job.progress` stage `TRANSCRIBING` with `phase`, `elapsedSeconds` | -32602; the command's error in `job.finished.error` (-32001, -32002, -32003) |
| `transcription.status` | fast | `lectureId`?: string<br>`recordingId`?: string<br>exactly one of `lectureId`, `recordingId` | `{exitCode, data}` of `mynotes transcription-status` | — | -32602, -32001 |
| `usage.get` | fast | `lectureId`?: string<br>`batchId`?: string<br>exactly one of `lectureId`, `batchId` | `{exitCode, data}` of `mynotes usage` | — | -32602, -32001 |
| `watch.snapshot` | server | `topic`?: string<br>`fresh`?: boolean | Snapshot (cached unless `fresh`) | — | -32602 |
| `watch.subscribe` | server | `topics`?: string[] | `{topics, snapshots: {<topic>: Snapshot}}` | `<topic>.changed` (a Snapshot) | -32602 |
| `watch.unsubscribe` | server | `topics`?: string[] | `{topics}` still subscribed | — | -32602 |
| `word.verifyRenderer` | job | — | `{jobId, job}`; then `job.finished.result` = `{exitCode, data}` of `mynotes verify-word-renderer` | `job.started`, `job.progress`, `job.output`, `job.finished` | -32602; the command's error in `job.finished.error` (-32001) |

A parameter marked `?` is optional. Types: `json` is any JSON value, written to a request file when the flag takes a file; `string[]` repeats the flag; `object<string>` passes `--flag KEY VALUE` per entry.
