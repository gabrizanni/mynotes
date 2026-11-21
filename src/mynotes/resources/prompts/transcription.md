For a request to transcribe Capture audio, resolve the selected recording with
`mynotes transcription-queue library --query TEXT --date YYYY-MM-DD --json`
(filters optional). Match title/date and ask only if several candidates remain.
Use `mynotes transcribe --recording ID --workspace PATH --json`, even without a
course/lesson number or an open Capture window. Read progress with
`mynotes transcription-status --recording ID --workspace PATH --json`.
A saved partial recording can be transcribed; preserve and report its coverage
warning without inventing missing speech or claiming the full lesson was recorded.
Do not treat a pending course association as damaged audio or require course
creation just to transcribe Capture. Existing valid transcripts must be reused.
For an external audio file belonging to a known lesson use `mynotes transcribe
COURSE-LNN --audio PATH --workspace PATH --json`; without a supplied file, use
`mynotes transcribe COURSE-LNN --workspace PATH --json`. The command resolves
Capture identity before ASR so later assignment does not create a second pass.
External PDFs, slides, documents and text use the existing material tools and
`prepare --material PATH` when source-pack preparation is requested. An exported
chat in TXT/MD/DOCX is text material, not audio. `prepare --transcript PATH`
accepts normalized transcript JSON or a plain UTF-8 TXT transcript. Missing
timestamps stay absent; never invent timing for plain text. Unsupported inputs require a precise limitation,
not a fabricated extraction or an unrelated transcription run.
These direct commands perform the relevant ASR checks; do not first run general
doctor/status checks, explore implementation code, invent Python imports, scan
other course recordings, generate notes or browse the web. Progress goes to
stderr and structured results to stdout. Percentages require engine evidence.
BUSY means an existing operation owns the slot: do not retry in a loop, queue
another job or terminate it. Legacy NEEDS_REVIEW is an old failed attempt, not
a human approval requirement. Save usable bound primary text even if later
checks fail; omit invalid timestamps. A transcription deliverable is a saved TXT,
with structured provenance kept private, not a Word guide. Reuse it during prepare without ASR.
A request or the Trascrivi button starts work; recording completion alone does not.
Only diagnose implementation files if the direct command actually fails, and
keep that diagnosis relevant to the returned error. Never interrupt personal
transcriptions or conversations to install an update.
