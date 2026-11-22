# Windows 11 x64 release acceptance

Reference build is macOS v0.4.0. For each row record the tested commit,
Windows build and architecture, test run or manual evidence, and outcome.
A row is accepted only after every listed behavior has passed.

| macOS screen or flow | Shared command or state contract | Acceptance on Windows |
| --- | --- | --- |
| Setup and first launch | `init`, `doctor --json`, `preferences`, private workspace | Clean install as a standard user, complete onboarding in Italian and English, keep runtime and workspace separate, present useful remediation for missing tools or denied microphone. |
| Library and courses | `capture dashboard`, `transcription-queue lessons`, `course` | Create and edit courses and profiles, filter lessons, add a past lesson, mark cancelled and unrecorded lessons, hide or restore entries, inspect linked folders and materials. |
| Lesson detail | `transcription-queue lesson-edit` and library actions | Reassign, swap, merge or shift numbers with conflicts previewed, edit timezone, preserve recording identity, select independent audio and TXT inputs, import duplicates, move managed copies, and recover a cancelled or partially completed deletion. |
| Calendar | `capture import`, `subscribe`, `sync`, `source`, and `schedule.json` | Import a timetable, connect and disconnect a feed, set its import mode, render local dates and daylight-saving changes, edit one scheduled occurrence, and display synchronization failures. |
| Capture | Workspace request, response, runtime, session and audio sidecars | Start and stop manually, schedule with the window closed, prevent duplicate recordings, test microphone and playback, recover after restart or device loss, preserve a usable partial and label missed coverage honestly. |
| Activity | `transcription-queue status` and job actions | Enqueue one recording and a course batch, pause, resume, cancel, retry, recover and export. Verify duplicate inputs, pending course association, worker crash, and preservation of existing transcripts. |
| Offline transcription | ASR engine registry and normalized transcript format | Transcribe representative short and long lessons in Italian and English on CPU. Validate timestamps, speaker attribution, uncertainty indicators, TXT output, cache reuse and cancellation. Compare errors and elapsed time against the same audio on the Mac. |
| External chat | `chat --provider`, shared workspace rules, saved provider preferences | Use installed Codex, Antigravity and Claude Code on Windows. Verify account status, startup rules, model and reasoning selection, cancellation, and private cache paths. |
| Notes and Word | Existing preparation, notes, publication and undo commands | Build an editable Word with formulas, validate rendering, finish a retained attempt, preserve personal edits on the cumulative update, and verify preview, conflict, backup and undo behavior. |
| Distribution | Authenticode-signed x64 setup and SHA-256 | Install, upgrade and uninstall on two clean Windows 11 x64 systems. Defer upgrades during recording/export. Preserve notes and workspace, and remove the per-user scheduled task on uninstall. Test the public GitHub asset without signing into GitHub. |

Windows CI can cover builds, CLI and scripted fixtures. Require a Windows 11
desktop session for UI, and a physical Intel PC for microphone, standby and
Word checks. Record live provider acceptance separately from simulated CI.
Verify the signature and the SHA-256 of the exact downloaded executable before
recording an installation pass. Release only when all rows have passed on the
same candidate commit and both Windows and macOS CI checks are green.
