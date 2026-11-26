# MyNotes Desktop: UX spec (Tauri 2 + React)

**Core decisions:**
- The UI is one calm window, plus a floating recorder capsule and a tray icon.
- Pages use a Paper canvas with deep-blue ink. Primary buttons are filled deep blue with Paper text (10.75:1). Coral appears only as the bookmark ornament.
- Onboarding asks its questions while about 1 GB downloads. The first value, a sample of your note style rendered in Word, arrives in about 3.5 minutes.
- Every backend status gets one human label and one fix.

## 1. Information architecture

| Sidebar (⌘/Ctrl 1–5) | Route | Contents |
|---|---|---|
| Today / Oggi | `/today` | Next-lesson hero, "Needs you", today's lessons, Finish-setup card |
| Calendar / Calendario | `/calendar?view=week\|month&d=` | Grid, lesson popover, Schedule lesson |
| Courses / Corsi (courses nested) | `/courses/:code/{lessons\|notes\|style\|profile\|files}` | Course workspace |
| Library / Raccolta | `/library?course=&q=&unassigned=1` | All audio and transcripts, import, unassigned, hidden |
| Activity / Attività | `/activity` | Transcription queue and notes jobs |

- **Sidebar bottom:** a recorder status row ("Next: Statistics 10:15"), Assistant (⌘J opens a right panel), and Settings.
- **Settings:** `/settings/{assistant|transcription|recording|calendars|general|profile|diagnostics}`.
- **Other routes:** `/lessons/:id` (a split view inside Library at ≥1100 px) and `/welcome/:step`.

## 2. Onboarding (target: 3½ minutes to first value)

**Rules**
- **Install in the background:** the install starts when the assistant is picked (`setup_runtime.py --provider X --progress-file`). A header pill shows "Setting up · 62%". Expanded, it shows the stage title, "LibreOffice 212 / 300 MB" and "Last data 4 s ago".
  - After 60 s with no data: "Waiting for network. Downloads resume on their own."
  - On `failed`: "Setup paused: {error}. [Retry]" (the install resumes where it stopped).
- **Draft, then save:** answers are kept in a local draft and saved in order once the `mynotes` command is available. If the user is faster than the download, an interstitial reads "Almost there, finishing downloads" with a live checklist.
- **Resume:** on launch, open the first incomplete required step (1, 2, 4, 6, 7, 8). Completion is read from `config show --json` (`onboarding.completed_groups`), `doctor --json` and the app's own `onboarding.json`. Skipped optional steps become Today's "Finish setup" card.
- **Header:** three labeled segments, "Setup · Your course · Recording". Back always works.

| # | Title EN / IT | Inputs → backend | Primary / secondary | Skip? |
|---|---|---|---|---|
| 1 | **Your lectures, turned into study notes.** / **Le tue lezioni, trasformate in appunti.** Body: "Record, transcribe on this computer, and get one Word file per course that grows lesson by lesson." / "Registra, trascrivi su questo computer e ottieni un Word per corso che cresce lezione dopo lezione." | Language EN/IT (defaults to the OS language) → `preferences --language` | Get started / Inizia | No |
| 2 | **Choose your AI assistant** / **Scegli il tuo assistente AI** | Three cards: Claude Code, Codex, Antigravity, each with "Uses your Anthropic / OpenAI / Google account". Choosing one starts the install. | Continue / "Which should I pick?" | No |
| 3 | **Sign in to Claude Code** / **Accedi a Claude Code**. "We'll open your browser. Come back when you're done." | Spawn `claude auth login` / `codex login` / `agy`. Poll every 3 s for up to 5 min (`doctor --json --provider`, `checks.agent.status`). | Sign in / Do this later | Yes |
| 4 | **What stays on this computer** / **Cosa resta sul tuo computer** | Two columns. *On this computer:* recordings, transcription, notebook, settings. *Sent to {provider} when you generate notes:* transcript, selected slides, relevant images. "{Provider}'s terms and possible usage costs apply. MyNotes has no account and never sees your password." | I understand / Ho capito | No |
| 5 | **What do you study?** / **Cosa studi?** | Textarea plus optional goal chips (exam / understanding / exercises / work / personal) → `profile --background TEXT`, or `--skip` | Continue / Skip | Yes |
| 6 | **Add your first course** / **Aggiungi il tuo primo corso** | Name*; lecture language; notes language; type (Theory / Exercises / Case studies / Programming / Mixed → `kind`); exam format (with a "Don't know yet" chip); hard topics (tags); **"Anything else?"** (free text) → `course add --name --notes-language --settings-json {kind,languages,assessment,difficult_topics} [--context-answer]` | Continue / Back | No |
| 7 | **How should your notes read?** / **Come vuoi i tuoi appunti?** | Detail: Concise / Clear / Detailed. Layout: Prose / Balanced / Lists. Images: None / Essential / Explained, plus a "max per lesson" stepper. Recap on/off with its language. Review questions 1–5. A live preview card sits on the right. Saved via `--settings-json style` or `style edit --course`. | Use this style / Open sample in Word (`style preview --course`) | No (defaults are fine) |
| 8 | **Where are your course files?** / **Dove sono i file del corso?** | Drop zone or "Choose folder" → `course link CODE PATH --dry-run --json` → preview: "14 PDFs · 2 recordings · Notebook: Appunti personali.docx (new) · Course notes: STAT_COURSE_NOTES.docx". On `NEEDS_SELECTION`, one radio question. Toggle "Review before updating Word" (`--publication-mode review`). | Connect / Use a new folder in Documents/MyNotes | Default folder |
| 9 | **Add your class schedule** / **Aggiungi l'orario delle lezioni** | Tabs: Calendar link (HTTPS/webcal), Google Calendar (paste the link, plus a 3-step "find your private iCal address" guide). Period and timezone. Runs `capture configure`, then `capture connect --file` (`importMode: all_timed`), then `capture dashboard --json`. Shows "34 lessons · 2 time clashes · 3 need a course". | Looks right / Remove calendar (`capture unsubscribe`) | Yes |
| 10 | **Let MyNotes hear your lectures** / **Fai ascoltare le lezioni a MyNotes**. "Audio stays on this computer. Next, macOS asks to allow *MyNotes Capture*, which is our recorder. Choose Allow." | Recorder on with schedule paused (`capture enable`, `capture pause`), then `capture test`, then a timer and an in-app player. "Do you hear yourself clearly?" | **Yes, I hear it** (`capture confirm-test --audio PATH --audible-confirmed`) / Change microphone (opens OS input settings) · Try again | Yes |
| 11 | **Record scheduled lessons automatically?** Off by default. "Your computer must be awake, lid open." Shortcut test: "Press ⌥⇧R." The step is checked once the press is detected. | `capture resume` turns it on; leaving it off keeps `pause` | Turn on / Not now | Yes |
| 12 | **Make your first notes** / **Crea i tuoi primi appunti** | "I have a recording or transcript": drop it → `transcription-queue import-audio`, or `prepare --transcript`, then Generate (runs in the background). "After my next lesson": finish. | Generate / Finish | Yes |
| 13 | **You're ready.** / **Tutto pronto.** A summary, with skipped steps listed as "Finish later". | none | Open MyNotes | n/a |

**Errors**
- **Sign-in states:** `missing` → "Claude Code isn't installed. [Open install guide] then [Check again]". `incompatible` → "Update Claude Code to continue. [Open update guide]". `unverified` (Antigravity has no status check) → "Finish sign-in in Antigravity, then [I've signed in]". `ready` → a check mark and auto-advance after 800 ms.
- **Calendar codes** from `capture_links` map one-to-one. For example, `GOOGLE_FEED_UNAVAILABLE` → "Google won't share this calendar publicly. [Paste your private iCal address]".
- **Mic denied:** "Microphone is off for MyNotes Capture. [Open Privacy settings]".

## 3. Main screens

**Shell**
- Sidebar is 232 px wide on `bg.sunken`. The window opens at 1200×800, minimum 960×640.
- The Assistant panel docks on the right, resizable from 360 to 560 px. Below 1100 px it overlays the page instead.
- Closing the window leaves the app in the tray. Quitting during a job asks: "A notes job is running. Quit anyway? Progress is saved."

**Today**
- **Hero:** a deep-blue panel showing the next lesson, its time, course and recorder chip, plus **one** primary action chosen in this order: live recording (Stop), blocking error (Fix), `AWAITING_APPROVAL` (Review), a transcribed lesson without notes (Generate notes), otherwise Record now.
- **Below the hero:** "Needs you" (up to 5 rows, "See all" goes to Activity), a timeline of today's lessons, and the Finish-setup card.
- **Empty state:** "No lessons today. [Import a recording]".

**Courses**
- **List:** cards showing name, next lesson and "Notes updated 2 days ago · Lesson 6".
- **Lessons tab:** a table with lesson number, date, and recording / transcript / notes chips. Multi-select leads to "Generate notes for 3 lessons".
- **Notes tab, course notes card:** Open in Word, Show in folder, History, Undo last update.
  - History is a drawer listing `history CODE`; Restore runs `restore ID` after a confirmation.
  - Undo asks "Undo last update? Your current Word is saved as a safety copy first." and runs `undo CODE`.
- **Notes tab, notebook card:** Open notebook, plus a NOTA AI tip.
- **Style tab:** the step-7 controls plus "Reset to defaults" (`style edit --reset`).
- **Profile tab:** the step-6 form, saved via `course edit CODE --file`.
- **Files tab:** linked folders (Change / Reveal) with a drag-and-drop re-link preview, and per-lesson attachments (`lesson_sources`).

**Lesson detail**
- **Header:** course · Lesson 3 · date, status chips, and a ⋯ menu (Edit lesson, Link recording, Link TXT, Manage/remove).
- **Main column, audio player:** play, ±15 s, speed 1×/1.5×/2×, and keys Space/←/→. Alternative recordings are listed with "Use for transcription".
- **Main column, transcript:** ⌘F search with match count and ↑/↓, timestamps that seek the audio, and "Open full TXT".
- **Right rail (320 px):** a Notes card (Generate notes button or job status), "Materials found" (PDFs with page counts, "Notebook section *Lezione 3* found"), and recording metadata.
- **States:** no audio → drop zone. Transcribing → stage label and elapsed time, never a percentage. Multiple sources → "Choose which recording to transcribe".

**Calendar**
- Week view by default, with a now-line. Month view is also available.
- Blocks use neutral course tints. Conflicts show a striped border, an icon and a label.
- Toolbar: Today, ‹ ›, view switch, Add ▾ (Schedule lesson, Import audio), Refresh.
- Local edits come with "Restore from calendar".

**Library**
- Search and filters (course, Needs a course, Hidden).
- Drag audio anywhere to import: `inspect-audio`, then pick a target per file, then `import-audio`.
- "Verify files" runs `verify-library`.

**Activity**
- **Sections:** In progress / Ready to transcribe / To check / History, plus Notes jobs (from `batch-status`).
- **Controls:** Pause/Resume all, "At the same time: 1–4" (segmented), Transcribe all, Clear waiting.
- **Row actions:** Remove, Interrupt, Retry, Retry from scratch, Finish saving.
- **Paused banner:** "Paused · 3 waiting, 1 finishing".

**Settings**
- **Assistant:** assistant and model, thinking depth, a "Use a different assistant for notes" toggle, Refresh models, Sign in / Check again. Uses `preferences --show`, `--models-json --expected-revision` and `models --refresh --json`.
- **Transcription:** parallel jobs; on Windows, `install-speech-model` and `verify-transcription`.
- **Recording:** recorder on/off, automatic schedule, margins, refresh interval, recordings folder, mic test, "Recover saved audio", shortcut.
- **Calendars:** connected calendars and their actions.
- **General:** language, theme, folders.
- **About you:** edit the background answer.
- **Diagnostics:** `doctor` checks with Fix buttons, Verify Word, and **Copy report** (doctor JSON plus versions; home paths and calendar URLs redacted).

### Generate notes flow

1. **Sheet "Notes for Lesson 3 · Statistics":** a source checklist built from `notes CODE --lessons 3 --plan-only --json` plus the lesson registry:
   - Transcript ✓
   - Slides: ✓, or "None found [Add slides]"
   - Notebook section: ✓, or "—"
2. **Options:**
   - **Quality:** Standard (default) / Thorough (`approfondito`) / Full review (`revisione`), each with a one-line explanation. "Standard usually takes under 10 min."
   - **Review before updating Word:** a course-level toggle.
   - **"More":** extra instructions (`--instructions`), max images (`--image-limit`), focus on lecturer (`--speaker-focus professor`), other lessons for context.
   - **Footer:** "Sends the transcript and selected slides to Claude Code."
3. **Run:** `notes CODE --lessons 3 --quality X --json`, reading stage lines from stderr. A stepper shows **Checking sources → Writing notes (GENERATING) → Formatting Word (BUILDING_WORD) → Updating course notes (UPDATING_WORD) → Done**, with elapsed time per stage and no fake percentage. The sheet can be minimized; the job lives on in Activity and the Today hero. Stop kills the process; Resume runs `--resume BATCH`.
4. **Outcomes (problem + one fix):**

| Status | Copy | Fix |
|---|---|---|
| COMPLETED | "Lesson 3 is in your course notes." | Open in Word (Undo appears in the toast) |
| COMPLETED_WITH_WARNINGS | "Added. 2 things to check." | View list |
| NEEDS_TRANSCRIPT | "Lesson 3 has no transcript yet." | Transcribe now (`transcribe STAT-L03`), with "Generate when ready" |
| PAUSED_QUOTA | "Claude Code's usage limit is reached. Progress is saved." | Resume |
| PAUSED_AUTH | "You're signed out of Codex." | Sign in (resumes after) |
| PAUSED_NETWORK / PAUSED_SLEEP | "Connection lost / computer slept. Progress is saved." | Resume |
| AWAITING_APPROVAL | "Review the new pages before they join your course notes." | Open preview → **Approve & update Word** (`publish CODE-L03 --candidate PATH`) |
| PERSONAL_CONTENT_CONFLICT | "Your edits overlap the new Lesson 3. Nothing was changed." | Ask the assistant to merge (opens the Assistant with this prefilled) |
| NEEDS_REVIEW / STOPPED | "This needs a closer look. Your work is saved." | Ask the assistant · Copy details |

### Assistant panel

- **Context:** a chip shows the current page ("Lesson 3 · Statistics").
- **Suggested prompts when empty:** "Prepare notes for lesson 3", "Make this course's notes more concise", "From now on show every calculation step", "Quiz me on lessons 1–4", "Set up recordings from my calendar", "Undo the last update".
- **Messages:** Markdown. Tool runs appear as collapsed rows ("Prepared notes for STAT lesson 3 · 2 min") that expand to show the raw command.
- **Permission requests:** inline cards with Allow once / Deny.
- **Composer:** ⏎ sends, ⇧⏎ adds a new line, dropped files become path chips, Stop button.
- **Footer:** "Messages go to Claude Code (Anthropic)."
- **Backend:** each prompt starts `mynotes chat "<message>"`. A real streaming adapter doesn't exist yet (see Gaps). Fallback for v1: a themed embedded terminal (PTY + xterm.js) running `mynotes chat`.

### Recording capsule and tray

**Capsule:** a separate transparent, always-on-top Tauri window, 36 px tall, bottom-center, draggable. Hover or focus expands it to 320 px (title, course, Stop).

| State | Shows |
|---|---|
| Upcoming (≤5 min) | "Statistics in 3 min" · Skip |
| Starting (PENDING) | Spinner, "Starting…" |
| Recording | Red dot + "REC", mm:ss from `currentSession.actualStart`, 5 level bars (only with a real level feed; otherwise no bars), Stop |
| Saving (exporting) | "Saving audio…" |
| Saved | "Saved · 1:24:10 ✓" · Transcribe, collapses after 4 s |
| Problem | "Microphone blocked · Fix" / "Recording failed · Details" |

**Tray:** a monochrome notebook glyph with a red dot while recording. Menu: status line, Record now, Stop & save, Next lesson, Automatic recording ✓, Open MyNotes, Quit.

**Keyboard**
- **Cmd/Ctrl+K palette:** navigate, Generate notes for…, Record now, Open course notes, Undo last update, toggle theme.
- **⌥⇧R / Alt+Shift+R (global, rebindable):** starts the current scheduled lesson, otherwise `capture start --unassigned --title "Recording 26 Nov 10:15" --duration-minutes 180`; pressed again, it stops.
- **Other:** ⌘J (Assistant), ⌘, (Settings), Esc.

## 4. Design system

**Color (light)**

| Token | Hex | Contrast |
|---|---|---|
| bg.canvas | #FFF8ED (Paper) | n/a |
| bg.sunken (sidebar) | #F6ECDC | n/a |
| bg.card / input | #FFFDF8 | n/a |
| bg.emphasis (deep cream) | #EFE3CF | n/a |
| bg.inverse (hero, dark panel) | #163B66 | n/a |
| text.primary | #13233A | 14.97 on canvas |
| text.brand (serif headings) | #163B66 | 10.75 |
| text.muted | #5B6472 | 5.67 canvas / 4.72 emphasis |
| text.onInverse / link.onInverse | #FFF8ED / #8CC8FF | 10.75 / 6.38 |
| action.primary (fill / label / hover / pressed) | #163B66 / #FFF8ED / #1E4A7E / #102D4F | 10.75 / 8.52 |
| action.soft (selected nav, user bubble) | #DDE8F6 with #163B66 text | 9.15 |
| focus (2 px ring, 2 px offset) | #2B67C6 | 5.16 |
| border.hairline / strong | rgba(19,35,58,.12) / .24 | n/a |
| border.input | #9C8E78 | 3.04 (meets 1.4.11) |
| success (text / bg) | #1D6B40 on #E2F2E7 | 5.6 |
| warning (text / bg) | #8A4B00 on #FBEBCF | 5.79 |
| danger and recording | #B42318 on #FBE4E0 | 5.41 |
| accent.coral | #F47B62 | 2.53, ornament only: never text, never the only state cue |

**Color (dark):** canvas #0E1726, sunken #0A121D, card #16233A, emphasis #1D2D47, inverse #24406A. Text #F3EBDD (15.17), muted #A7B1C0 (8.29). Primary #8CC8FF with label #0B1A2C (9.86), hover #A9D6FF. Focus #8CC8FF. Input border #5B6E8C (3.47). Success #6CD39A, warning #F2B84B, danger #FF8F80, tints at 14% alpha. The capsule stays dark in both themes (#13233A on #FFF8ED, 14.97).

**Type:** fonts are bundled locally (OFL), with no CDN.
- **EB Garamond, only at 24 px and up:** Display 40/48 · 500; H1 32/40 · 500; H2 24/32 · 500.
- **Figtree:** Title 20/28 · 600; Heading 16/24 · 600; Body 15/22 · 400; Small 13/20 · 400; Label 13/16 · 600; Caption 12/16 · 500 (12 px is the floor).
- **JetBrains Mono 13/20:** timestamps and diagnostics.
- Buttons hug their text, because Italian strings run about 30% longer.

**Spacing:** 4 px base: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64. Reading column max width 760 px.

**Radii:** inputs 8, buttons 12, cards 16, dialogs 20, chips and capsule pill.

**Elevation:**
- e0: hairline only.
- e1 (cards): 0 1px 2px rgba(60,40,10,.06).
- e2 (menus): 0 8px 24px rgba(60,40,10,.12).
- e3 (dialogs, capsule): 0 16px 48px rgba(22,59,102,.18).
- Dark theme: black at 40% plus a 1 px top highlight at rgba(255,255,255,.06).

**Motion:** 150 ms (hover, press), 240 ms (panels, dialogs), 320 ms (capsule expand, step change), easing cubic-bezier(0.4,0,0.2,1). Reduced motion switches to opacity-only at 150 ms and stops any pulsing.

**Icons:** Lucide (ISC). Its 1.75 px outline matches the hairline look, it has the needed glyphs (mic, audio-lines, notebook-pen, calendar-days, file-text), it's tree-shakable, and it works on both platforms. SF Symbols is Mac-only and Fluent is Windows-only. Sizes 16/20/24.

**Components:**
- **Buttons:** primary, soft, ghost, destructive, icon (40 px target).
- **Inputs:** text field, textarea, select, segmented control, stepper, tag input, toggle, drop zone.
- **Containers:** card (with a deep-cream variant), list row (48 px), table, popover, dialog, sheet.
- **Feedback:** toast (bottom-left, 6 s, with an action, `aria-live=polite`), banner, empty state (icon, one line, one action), progress (determinate only with real bytes; a stage stepper otherwise).
- **Chips** (icon + label, never color alone):

| Area | Status → label | Tone |
|---|---|---|
| Notes | GENERATING → Writing notes · BUILDING_WORD → Formatting · UPDATING_WORD → Updating notes | info |
| Notes | COMPLETED → Notes ready | success |
| Notes | AWAITING_APPROVAL → To review · PAUSED_* → Paused · COMPLETED_WITH_WARNINGS → Ready, check | warning |
| Notes | PERSONAL_CONTENT_CONFLICT / NEEDS_REVIEW → Needs you | danger |
| Transcription | QUEUED → In queue · RUNNING → Transcribing · COMPLETED → Transcribed · FAILED/NEEDS_REVIEW/OUTDATED → To check · WAITING → Audio not ready | by meaning |
| Recording | SCHEDULED · RECORDING (live) · EXPORTING → Saving · COMPLETED → Recorded · PARTIAL · MISSED · FAILED · CONFLICT → Time clash | by meaning |
| Assistant | missing → Not installed · incompatible → Update needed · login_required → Sign in · unverified → Not checked · ready → Ready | by meaning |

## 5. Microcopy rules and glossary

**Rules:**
- Second person, imperative verbs, sentences of 12 words or fewer.
- Every error states the problem plus one fix button. No codes in the UI; codes go in Copy details.
- Reassure when it's true: "Progress is saved."
- Never claim what can't be verified: "Notes ready", not "Notes correct".
- Name the provider whenever data leaves the computer.
- Sentence case. Every string lives in `locales/{en,it}.json`, with ICU plurals.

| Old | EN | IT |
|---|---|---|
| Capture (in the UI) | Recorder (the OS prompt still says "MyNotes Capture") | Registratore |
| Agy | Antigravity | Antigravity |
| agent / CLI / provider | AI assistant | assistente AI |
| effort | Thinking depth | Profondità di ragionamento |
| handout / dispensa cumulativa | Course notes (Word) | Dispensa del corso |
| personal notes | Your notebook | Il tuo quaderno |
| publish / preview-first | Approve & update Word / Review before updating | Approva e aggiorna il Word / Rivedi prima |
| approfondito / revisione | Thorough / Full review | Approfondita / Revisione completa |
| link / dry-run | Connect / Preview | Collega / Anteprima |
| sync | Refresh | Aggiorna |
| Da associare | Needs a course | Senza corso |
| quota | Usage limit | Limite di utilizzo |
| doctor | Check setup | Verifica configurazione |
| batch | Notes job | Lavoro |
| parallel | At the same time | In contemporanea |
| speaker focus professor | Focus on the lecturer | Solo il docente |

## 6. Screenshots for the author's review package

1. Step 2–3: assistant choice, with sign-in in the "ready" and "missing" states
2. Step 4: privacy
3. Step 6: course form
4. Step 7: style with live preview
5. Step 8: folder preview with `NEEDS_SELECTION`
6. Step 10: mic test playback and "I hear it"
7. Today (light)
8. Lesson detail with transcript search and the Generate sheet open
9. Generation stepper, plus the `AWAITING_APPROVAL` state
10. Course › Notes with the history drawer and undo dialog
11. Calendar week with a time clash, and Activity with the paused banner
12. Capsule state strip, tray menu, and the Assistant panel in dark theme

## 7. Features with no backend command yet

1. **In-app chat:** `mynotes chat` only launches an interactive terminal app. A `chat --stream-json` mode is needed for each provider, including permission events. Until then, use the embedded terminal.
2. **Live level bars:** Capture publishes no audio level. It needs a level channel.
3. **Pausing a live recording:** `capture pause` only stops future automatic starts. Hide the Pause button until Capture supports it.
4. **Choosing a microphone:** Capture always uses the system default input. "Change microphone" can only open the OS sound settings.
5. **Mic test length:** the test is fixed at 60 s. Stopping it early with `capture stop` still has to be checked against `confirm-test`.
6. **Mic test without a calendar:** `capture enable` requires an existing schedule. An empty `import` might work around it; that needs verifying, or a test that launches the recorder on demand.
7. **Calendar preview:** `capture connect` saves the calendar immediately. The preview works only because "Remove" calls `unsubscribe` afterwards; a `--dry-run` flag would be cleaner.
8. **Local ICS file:** not supported (HTTPS only; `import` expects normalized JSON). It needs a local converter, or the Assistant.
9. **Google sign-in (OAuth):** not supported. Users paste a Google calendar link instead.
10. **Review-first for one run only:** publication mode is set per course. Per run would need a `--review` flag on `notes`.
11. **Resolving an edit conflict:** no command exists. The Assistant handles it.
12. **Sign-in status before install finishes:** `doctor` only exists once the wheel is installed. Either Tauri probes `codex login status` / `claude auth status` itself, or `setup_runtime.py` installs the wheel first. Antigravity stays "Not checked".
13. **Live style preview:** it's a client-side approximation; only `style preview` produces a real Word file.
14. **Listing notes jobs:** no command lists them. The app has to store batch IDs and call `batch-status`.
15. **Tauri-only features:** global shortcut, tray, capsule window and audio playback.
16. **NOTA AI count:** none exists.
17. **Windows recorder:** still under validation (`windows/PARITY.md`).

### Critical files for implementation
- `src/mynotes/cli.py`
- `src/mynotes/capture.py`
- `src/mynotes/notes_run.py`
- `src/mynotes/onboarding.py`
- `scripts/setup_runtime.py`
