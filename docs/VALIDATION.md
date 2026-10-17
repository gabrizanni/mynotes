# Release validation status

This document separates implemented safeguards from checks actually completed.
Version 0.2.13 is a local candidate for continued acceptance testing. Its release
notes identify local results, hosted CI and remaining end-to-end limitations.

## Local 0.2.13 Capture validation (2026-10-17)

- Native regressions cover silent refresh without loading-state changes, one
  outstanding read, stale-result rejection after Stop, retained action messages,
  inactive/active polling intervals and library reads separate from calendar ticks.
- Deterministic ASR fixtures cover saved partial recordings, missing and active
  audio, unassigned recordings, direct requests without a Capture window, frozen
  profiles, assignment reuse and concurrent prepare/chat requests with one engine
  execution. No personal audio was transcribed during these checks.
- Material-routing regressions retain text, Markdown and Word sources as material
  without an ASR call or invented transcript timestamps.
- The actual native preview window was exercised with isolated synthetic data:
  archive filters, search, selection, partial-coverage explanations and an existing
  transcript remain visible across repeated refreshes without a loading spinner.
- Final suite totals, fresh package hashes, isolated upgrade and personal install
  verification are recorded with the release artifacts. Synthetic ASR is not proof
  of personal microphone capture or a real-provider transcription. GitHub
  publication is a separate step.

## Local 0.2.12 pipeline validation (2026-10-17)

- The pipeline revision passed 757 local tests before packaging metadata and
  coordinator guidance were updated; the final package validation is recorded
  separately with its artifacts.
- Verified synthetic SUCCESS-stream recovery, altered-input rejection, UNKNOWN
  speaker handling, preserved budgets, conversation counter deduplication and
  a Word failure followed by resume with provider calls explicitly forbidden.
- A synthetic provider with the real shared renderer/LibreOffice produced two
  standalone documents and one fresh cumulative; all four rendered pages were
  inspected. Reuse and resume made no additional provider calls.
- Two existing real lesson source packs retained all evidence and transcript
  metadata. One complete retained response was diagnosed read-only: reference
  aliases are locally recoverable but two cross-lesson study answers still
  require content review. No prose was rewritten to bypass the content check.
- No new real-model generation, academic ASR, Microsoft Word acceptance or
  academic publication is claimed. Tests do not guarantee model completion.

## Integrated 0.2.10 preview (2026-10-16)

- The integrated generation, Word rendering, Capture UI, transcription queue and
  complete-calendar source passed 686 local tests before the public-version
  metadata change. No functional Python or Swift changes accompany that rename.
- Local native regressions, isolated installation and UI/calendar checks passed.
  A real public feed imported 92 timed events; delayed association reused an
  existing transcript without a second ASR call in a deterministic test.
- Hosted CI independently passed the preceding development candidate, including
  tests, native Capture checks, public-content audit and a fresh DMG build.
  The release page links the matching CI run for the 0.2.10 artifacts.
- A reference generation batch remains incomplete because of source-identifier
  consistency and speaker attribution. Full-lecture/overnight acceptance and
  Apple notarization remain open. Passing tests do not certify those outcomes.

## Dynamic model catalog and MyNotes UI (2026-10-14)

- The full final suite passed 518 tests with real Office/recorder launches blocked.
  Native decoder and transport checks cover new model/effort identifiers,
  separated stderr, inherited app environment, cancellation and deadlines.
  The release arm64 UI build succeeded and the isolated bundle signature verified.
- Actual metadata-only CLI calls returned seven Codex models and six Agy models.
  Codex reported the account type/plan and effort defaults. Agy reported its
  agent-level effort options; its missing default is not inferred from a model
  variant. Claude Code was absent, so its adapter was verified with protocol
  fixtures, not a real logged-in Claude installation.
- Actual macOS UI checks passed in light and dark appearance: agent selection,
  searchable catalog, real Codex/Agy entries, separate preset shortcuts, effort
  selection and preference persistence. A new model and new effort injected only
  through a disposable catalog fixture appeared without rebuilding the app and
  were saved through the UI. Removing that fixture preserved the selected model
  as unavailable rather than silently replacing it; the real Terra preset was
  then restored and saved.
- GUI testing found two environment problems that terminal-only checks missed:
  missing HOME for Agy and an explicitly nil Process environment dropping the
  app's private runtime location. Both are repaired and covered by regression
  checks. After repair, the complete future-model UI save succeeded in the
  isolated workspace. Earlier model metadata snapshots could use the default
  runtime; academic sources and personal course preferences were not changed.
- Further resize/long-list UI interaction was stopped when the Mac locked.
  The default-size layout was inspected; no final resized-window acceptance is
  claimed. No inference, real new-model generation, Intel execution, installed
  app update, DMG change or public release was performed for this task.

Catalog listing is not generation/access proof. The UI distinguishes a CLI
acquisition from the provider/cache timestamp; new releases depend on what the
active provider/CLI exposes. See [catalog behavior](MODEL-CATALOG.md).

## Capture interface source validation (2026-10-14)

- The MyNotes window now has MyNotes and Capture tabs. Capture provides calendar
  synchronization, searchable upcoming lessons, local overrides, scheduled
  special lessons and immediate recording requests.
- The final Python/native contract suite passed 507 tests with Office and real
  recorder launches blocked. A release arm64 build of MyNotesSetup succeeded;
  the isolated preview bundle's ad-hoc signature verified.
- Real macOS UI inspection passed on the unlocked Mac: both tabs, calendar
  cards, lesson selection/search, scheduled lesson form/save, persistent local
  title and recording-exclusion edits, cancellation visibility, source reset,
  and addition/removal of calendar mappings. The immediate-recording form was
  inspected using an inert provider fixture whose operational commands were
  blocked; no microphone recording was started in this UI verification.
- Local tests cover override persistence across refresh, latest-source reset,
  active-recording edit protection, conflict recalculation, schedule publication
  before native-watcher notification, private subprocess input files and Stop
  remaining usable during a blocked sync. Subscription/microphone integration
  with the user's personal data was not tested through the new interface.
- The installed app, version and DMG remain unchanged. This validates source
  changes and an isolated local UI build, not an installation or public release.

## Capture source validation and logo (2026-10-14)

- The full Python suite passed: 480 tests. Native regression checks passed on
  the final source, including persisted failure visibility and preservation of
  partial-audio explanations after a scheduled end.
- The source now shows "Saving audio" during export and retains failed recording
  information in the enabled, unpaused menu state.
- A new microphone/waveform logo is included as PNG and ICNS bundle resources.
  The universal arm64/x86_64 app built successfully and its local signature
  verified. Asset hashes and the public-tree audit passed.
- Real isolated tests produced a 60-second microphone recording and a complete
  six-minute recording spanning two segments. Real macOS sleep/wake preserved
  staged audio, resumed within the window and exported a marked partial file.
  MyNotes discovery retained the correct full/partial distinction.
- A local HTTPS calendar fixture passed conditional refresh, stable lesson and
  event identity after time changes, and explicit cancellation. Certificate
  validation stayed enabled; no institutional feed was used.
- A SIGKILL trial after the first complete five-minute segment made the open
  fragment unreadable. Export was rejected and all staged files were retained;
  this is not successful automatic recovery of corrupted audio. Controlled
  SIGTERM handling now closes the segment before the app exits.
- Controlled restart on the rebuilt diagnostic app was blocked by renewed macOS
  microphone authorization (NOT_DETERMINED); the app was closed without recording.
  This trial did not establish real controlled-restart recovery. Subjective audio
  listening and visual menu/Finder verification remain open: the UI connection
  timed out. Intel execution, overnight operation and full lectures were not
  tested. The partial-reason repair was verified in native regression tests;
  the first real pilot used the earlier diagnostic binary. The final build and
  native regression checks include the reason and SIGTERM changes.
- Installed apps, release version, DMG and academic sources remain unchanged.

## macOS renderer crash prevention (2026-10-14)

Six local LibreOffice 25.8.7 crash reports show SIGABRT in
`_RegisterApplication`, before document conversion. Native permission probes
confirmed that the Codex sandbox denies the LaunchServices mach services and
that an unrestricted process can access them. The shared Office launcher now
checks these permissions before spawning LibreOffice. It does not change or
bypass sandbox permissions. An optional OS probe uses the Codex sandbox marker
as a fallback when the API is unavailable.

Word validation and legacy `.ppt` extraction stop with an actionable error in
the restricted context. `.pptx` text and table extraction can proceed locally,
with the rendering failure retained on affected visuals; this is not visual
validation. Visual cache identities include launch permissions so results
without rendered images are not reused after moving to an allowed context.

Every conversion uses an independent temporary profile and output directory.
Only a fresh nonempty PDF from a successful process is accepted; stale PDFs
cannot satisfy validation. Failed conversions are not automatically retried.
The macOS native backend can initialize AppKit despite `--headless`; see the
[LibreOffice backend selection code](https://docs.libreoffice.org/vcl/html/salplug_8cxx_source.html).

Regression tests and the real restricted-context preflight passed without
starting Office. After explicit user authorization, the source launcher also
converted two artificial documents with the managed LibreOffice 25.8.7 outside
the sandbox: a Word document with an eight-row, seven-column table, and a
PowerPoint slide with a table. Both PDFs have one page and the expected title
and final table cells. No new soffice crash reports appeared during conversion
or the follow-up check; the input documents remained unchanged. This verifies
these two native conversions, not Microsoft Word GUI acceptance or every
possible document. No DMG, installed application, system crash reporter
setting, or academic document is changed by this source fix.


## Next candidate 0.2.5: standalone Word notes, package pending

The corrected source passed **33 focused tests** and the complete **323-test
local suite**. These are source checks; **0.2.5 packaging/installation has not
yet been verified**. The 0.2.3 and 0.2.4 packaged artifacts/source snapshots are
historical and remain immutable.

Selected standalone DOCX notes without any recognized lecture headings now
extract the whole file for the requested lesson. Cumulative notebooks with
recognized headings still use the exact target slice; a missing target stays
empty and duplicate target headings remain an error. Tables and note markers
remain evidence; imported instructions remain untrusted.

A real generated standalone-DOCX source-pack trial produced personal-note
availability and H evidence, including tables/markers, with original bytes
unchanged. Read-only checks on the already-selected supplied documents also
confirmed nonempty scoped output and unchanged source hashes. This did **not**
rebuild personal packs/caches, regenerate candidates/Word, publish course notes
or make an AI call. Old personal outputs are not certified as corrected by this
extractor check.

A reversible **MyNotes-only fresh-start trial on the current account** is
separately authorized and being prepared. Backup/reset/reinstall outcomes will
be recorded after execution. Other applications, AI agents and credentials are
kept; a clean MyNotes state on this existing account is not a clean macOS-account
test. First-course, microphone/listening and browser/GUI acceptance remain
separate from local component readiness.

## 0.2.4 packaged optimization validation (historical)

The 0.2.4 source passed **318 tests in 7.72 seconds**, with **112 public files**
audited and clean diff/installer syntax. Its built DMG and extracted payload
were verified: **125 file hashes** matched and installed-source equality passed.
The isolated installed wheel ran fresh system-audio ASR and a one-page local
Word render, rebuilt both saved provider candidates to two pages each (Codex
12 native equations, Agy 9), and passed rollback 0.2.4→0.2.3→0.2.4 plus a new
zsh profile/doctor check from another working directory. No new AI call was
needed and original academic/pilot handouts were untouched.

Installed new-feature checks connected a real Finder alias for all three
roles in **0.109 seconds**, read usage for an absent workspace without creating
it, and used one extraction across two identical DOCX-cache requests with
matching results/original bytes. These fixtures confirm installed-module
execution, not AI-chat latency, full-course speed or clean-account acceptance.

| Change | Actual evidence | Limits |
| --- | --- | --- |
| Content/extractor cache | 72 focused cases cover source/dependency/options/cache-code/renderer identity, corrupt payload/assets, repair, concurrent writers and observed source changes. A generated PDF/DOCX trial called the extractor twice on first prepare and zero additional times on repeat; pack bytes, evidence, provenance and PNG hashes matched | Synthetic local functional trial, not full-course throughput. Source change checks are bounded observations, not global filesystem snapshot isolation |
| Attempt ledger | 107 focused checks, including 23 new usage scenarios. Unique attempts include success, failure, timeout and local rejection; unknown usage and reporting coverage are explicit; saved phases/reuse remain separate | Fake-provider/regression evidence. Counters are provider reports, not invoices, billing or unrecorded historical activity |
| Process-size preflight | All three adapter fixtures reject oversized argument/environment input before any provider subprocess. UTF-8 bytes and the exact launch environment are checked; no truncation or paid fallback | Prevents system process-size failure; not a model-context/token/quota budget |
| Folder, symlink and Finder alias linking | 19 combined path/link/first-course alias cases, including native aliases, capped previews and same-inode deduplication. One real CLI subprocess from another directory connected a real Finder alias and all three roles in 0.116 s; repeat preserved settings/history and original/alias bytes | One CLI functional measurement only; not AI-chat latency or a general performance benchmark. Metadata-only inventory is bounded to the supplied folder and immediate children; previews show at most 50 files per role with exact counts of inspected entries |

Use `mynotes course link COURSE "PATH" --json` for a configured course and
`mynotes usage COURSE-L01 --json` to inspect that lesson's local attempts. Link
ambiguity requires an explicit notes/recordings selection; no global material
search is needed for this operation. Missing counters remain unknown, never
invented zero. New reuse events do not add a provider invocation or re-count
checkpoint usage. [Implementation and remaining optimization priorities](OPTIMIZATION.md).

## 0.2.3 packaged setup validation (2026-10-11–12)

The local **0.2.3** DMG was built, integrity-verified and installed on
isolated paths. All **115 extracted payload hashes** matched; source equality
was checked, and the installed wheel executed real local transcription and
Word rendering. Deep/strict ad-hoc signature verification passed. The final
source suite passed **255 tests in 6.70 seconds**, with **102 public files**
audited, plus clean diff whitespace and installer zsh syntax.

These results supersede the older source/package checkpoints. They establish
local source and isolated packaged execution, not a clean macOS account,
browser quarantine acceptance, hosted CI or a published GitHub release. Any
documentation-only repack must keep the tested wheel bytes and repeat DMG/hash
verification.

| Acceptance scenario | Actual evidence | Status |
| --- | --- | --- |
| Documentation/public boundary | Bilingual guides, Open Anyway schematics, allowlist; final source audit passed on 102 files, including the trust-boundary regressions; distribution checks included in full suite | Source and initial 0.2.3 artifact audit passed locally; documentation-only repack requires another integrity check |
| Native app and DMG | 0.2.3 DMG passed `hdiutil verify`, deep/strict ad-hoc signature verification and all 115 extracted payload hashes; installed wheel matched source | Passed locally; no hosted CI or browser/clean-account acceptance |
| Isolated component bootstrap | 0.2.3 packaged wheel executed fresh Argmax 1.1.0 transcription on system-generated speech and LibreOffice 25.8.7 rendering of generated Word to one page. Bundled uv prepared private Python 3.12.13 with minimal PATH | Real packaged local execution; not a clean macOS account or classroom accuracy test |
| Setup reuse | Identical isolated retry completed in approximately 0.7 seconds, reusing installed components | One measured retry, not a throughput guarantee |
| Interrupted download | Real HTTPS tokenizer download stopped at 1,048,576 bytes; retry resumed to 2,480,617 bytes and verified SHA-256 | Passed real-network trial; low-space/corruption faults tested separately |
| User-started setup | User selected Prepare in 0.2.1 development app; progress reached ready on existing account | Real user-triggered setup; existing account had developer/provider tools |
| Provider absence/auth/compatibility | Read-only checks: Codex 0.162.1 ready with capabilities verified; Agy 1.3.3 capabilities verified, auth unverified; Claude absent from personal installation. A temporary official Claude Code 2.1.296 executable had its hash verified and ran version/help/auth-status in a sandbox denying network, private files and credential IPC; it reported loggedIn=false | Non-generative CLI/negative-login checks; combined help-flag compatibility handling covered by three regressions in the final suite; no Claude account or LLM generation |
| Real Codex standard/default generation | First call failed strict schema before generation. After a scoped fix, an authorized retry produced a validated candidate. Aligned-equation repair rebuilt that same candidate into two-page Word with 16 native equation objects | 0.2.3 packaged-wheel rebuild also validated two pages, with 12 native equation objects after currency fixes; original corrected pages visually inspected; no extra AI call |
| Real Agy standard/default generation | One call produced a validated candidate; currency/escaped-dollar parsing was repaired locally, then the saved candidate rebuilt to two Word pages with native equations | 0.2.3 packaged-wheel rebuild validated two pages with 9 native equation objects; original corrected pages visually inspected; no extra AI call |
| Requested/actual settings | Calls requested provider defaults; actual model/effort empty where transport did not report them | No inferred model identity/settings |
| Capture | Earlier microphone/recording evidence below; 11 standalone native regression cases passed during setup work | New-candidate permission, listening and full-lecture trials pending |
| Update/rollback preservation | 97 relevant runtime checks include two-thread and two-process course preservation, signal rollback, same-version conflicts and Capture lease deferral. Conditional rollback restores provider/path groups only if their written hash still matches, preserving concurrent user edits | Fault/concurrency checks passed; actual isolated rollback 0.2.3→0.2.2→0.2.3 worked from another working directory; new zsh profile resolved launcher and doctor reported mandatory components ready |
| Cache/phase recovery and usage | Candidate SHA check preserves edited candidates; validated phase checkpoints resume after quota; successful-phase usage aggregates with raw phase counters retained; source snapshot excludes transient cache/change flags; ASR key includes tool/binary/model-directory identity | Passed in the final combined source suite; no live multi-phase cost benchmark |
| Publication clone / idempotency | First publication of a synthetic copied pilot succeeded; immediate repeat exposed a defect. Source fix covers target/course/source-JSON identity, candidate copy hash and unchanged-handout SKIPPED behavior (13 new regressions, 36 targeted checks) | 0.2.3 installed-wheel clone test: build→PUBLISHED→SKIPPED, unchanged final SHA-256, exactly one start/end marker, no extra transaction/backup; no new AI call or academic file modification |
| Final source checks | 255 tests passed in 6.70 seconds; audit 102 files, diff whitespace and installer zsh syntax passed; native setup targets arm64/macOS 14 | Source and isolated packaged execution passed; hosted CI not run |
| Private workspace creation | Newly created workspace root and required directories use mode 0700; existing directory modes are preserved. Two focused tests passed and are included in the final 255-test suite | Source checks passed; existing personal directory modes untouched |
| Source instruction boundary | Personal-note markers remain ordinary H evidence and untrusted directive candidates. Only saved configuration preferences are actionable; source and legacy directive/approval fields are quoted in ASCII-escaped JSON. Generation contract 6→7 avoids reusing old directive-trust generations. 55 focused tests included forged T/S/H/W instructions, newline/Unicode headers and unchanged originals | Local/fake-provider tests passed; no real-provider attack or complete filesystem isolation claim |
| Native GUI visual review | 0.2.1 setup UI was visually inspected. The latest 0.2.3 caption/UI could not be inspected because the UI tool reported no accessible window while two same-bundle app instances were inventoried | 0.2.3 visual verification remains pending; no new GUI success claim |
| Clean browser install / beginner | Account without inherited tools, browser quarantine/Open Anyway, first-course chat and beginner reaching first Word | Not performed |

Four rendered page PNGs, two for each real-provider pilot, were visually inspected
by the coordinating agent after the local equation/currency repairs. These are
small synthetic fixtures, not full-lecture quality or Microsoft Word export
benchmarks. No additional AI generation was needed for those renderer repairs.

The original provider pilot handouts were not published or modified. A separate
synthetic copied workspace was used for the publication/idempotency test above.
All pilots used synthetic public break-even sources. Their success does not establish lecture quality, all three
live integrations, hosted CI or complete first-user acceptance. The
[read-only optimization audit](OPTIMIZATION.md) records usage and remaining
caching/workflow improvements.

CI builds candidates without publishing. Add a direct stable download link only
after the verified artifact is uploaded. Ad-hoc distribution requires no Apple
Developer account or notarization; browser quarantine acceptance is separate.

## Capture 0.2 validation (2026-10-11)

- The full Python suite passed locally: 128 tests, including 32 Capture cases.
- Eleven standalone native regression checks passed with the macOS frameworks:
  stale-session recovery, expired commands, persisted request replay, duplicate
  acknowledgement, denied microphone, sleep guard, explicit attempt selection,
  single instance, synthetic audio assembly, no overwrite, and corrupt segments.
  These are local checks; XCTest could not run with the installed Command Line
  Tools because its framework is unavailable. The standalone runner is in CI.
- A real, scheduled microphone recording produced 59.883 seconds of mono AAC/M4A
  in an isolated diagnostic workspace. AVFoundation and `afinfo` confirmed its
  duration/format. MyNotes discovered the canonical recording with `partial=false`.
  No audio quality/listening or full-classroom accuracy claim is made.
- The native app autonomously invoked Python at startup against a temporary
  HTTPS ICS server with certificate validation. A second due refresh used HTTP
  validators and preserved event identity/lesson number after a time change.
  No real institutional feed or six-hour continuous run was tested.
- Process snapshots showed 0.0% CPU / about 37 MB RSS in standby and 0.2% CPU /
  about 39 MB RSS while acquiring audio. These are snapshots, not long-run averages.
- The universal app was built for arm64/x86_64 and its local signature verified.
  The extracted archive installed independently of the development checkout.
  Automated checks also cover update deferral while recording/exporting, rollback
  from another working directory, and cleanup of failed dependency installation.

Capture acceptance on a full lecture, actual sleep/wake recovery, login/crash
restart, overnight refresh, Intel execution and menu visual inspection remains
to be performed. The candidate is ad-hoc signed. The chosen distribution does not require an
Apple Developer account or notarization; the documented Open Anyway path still
needs an actual browser-download test on a clean macOS account. Existing recorder
installations and personal course workspaces were not activated or migrated.

| Scenario | Current evidence | Status |
| --- | --- | --- |
| Clean installation | Extracted ZIP installed from its included wheel without the development checkout | Passed locally |
| Different courses and no institution | Generic configurations covered by tests | Passed with test-only provider fixtures |
| Missing optional materials | Availability flags remain false and visible in run state | Passed |
| Paths with spaces and accents | Configuration and release-install tests use nontrivial paths | Passed |
| Unavailable cloud file | Read failure leaves no published state | Passed with simulated missing file |
| Provider unavailable | Records `PROVIDER_UNAVAILABLE`, no DOCX, and no actual provider/model | Passed automatically |
| Provider quota exhaustion | Injected test adapter records `PAUSED_QUOTA` and a retry can publish | Passed in test only |
| Recording discovery | Dynamic codes/aliases, stable-file wait, canonical rename and duplicate blocking | Passed automatically |
| Local ASR cache and metadata | Engine/model/version/settings and input hashes are persisted; unchanged audio is reused | Passed automatically |
| Real local ASR | WhisperKit `v1.1.0` processed a 45-second excerpt with segment and word timestamps | Passed locally; semantic accuracy not benchmarked |
| Material extraction | PDF, PPTX, DOCX lesson blocks and visual rendering | Passed automatically and on one real PPTX deck |
| Visual selection boundary | Structural diagram is a candidate; raster photographs require semantic review | Passed by visual inspection on two real slides |
| Source pack integrity | Unique T/S/H/W/K ids, timestamp and visual-reference validation, source hashes and explicit missing-audio state | Passed automatically |
| Exact lesson sources and imported transcript | Private per-lesson manifest takes precedence over discovery; a 1,142-segment real transcript retains Professor/Student/UNKNOWN labels and long evidence IDs without rerunning ASR | Passed locally on an isolated lesson workspace |
| Legacy PPT and Excel | Old `.ppt` is converted for extraction; `.xlsx` retains sheet, cell, formula and displayed value provenance | Passed on one real PPT and a synthetic workbook; real workbook quality review pending |
| Startup without a task | Launcher supplies workspace/course context and asks the agent to greet without eager file reads; a separately named temporary Agy plugin greeted in 19.907 s total, with 438 ms CLI startup and no file reads or skill error in its log | Passed once in print mode; interactive paired comparison remains unmeasured |
| Two simultaneous starts | Nonblocking publication lock rejects the second writer | Passed locally |
| Word modified externally | Hash conflict prevents overwrite; explicit acceptance rebases and preserves outside content | Passed locally |
| Redo of an earlier lecture | Target block changes while later lecture and outside content remain | Passed locally |
| Identical second run | Returns `SKIPPED` without duplicate output | Passed locally |
| Versioned application update | Installer uses versioned directories and never reads a private workspace | Partially verified |
| Public package privacy | Explicit allow-list, sensitive-reference scan, manifest, and archive hashes | Passed locally |
| DOCX mechanics | Native OMML fractions/roots/scripts, headings, lists, tables, image relationships, exact boundaries, conflict guard, archive/restore and recovery classification | Passed automatically; isolated DOCX also rendered and visually inspected |
| Codex integration | Managed skill plus real interactive launcher with configured model/effort | Implemented; final end-user selection remains a user interaction |
| Agy integration | Managed plugin/agent plus real interactive launcher with configured model/effort | Implemented; final end-user selection remains a user interaction |
| Provider command contracts | Codex/Agy command fakes verify model, effort, read-only/schema flags, envelopes, quota errors and immutable raw attempt records | Passed automatically |
| Candidate quality levels | Four levels execute 1 / 1 / 2 / 3 deliberate phases; malformed evidence is rejected before Word publication | Passed automatically |
| Ordered candidate blocks | Paragraphs, lists, formulas, tables, visuals and recap render in narrative order; saved legacy candidates remain readable | Passed automatically; full-lecture visual review pending |
| Neutral Word template | Global and per-course editorial choices resolve without reading a private reference at runtime; the packaged template has only style, numbering, theme and A4 page parts, with no body or document properties | Passed automatically; the template and synthetic preview were visually inspected |
| Template parity | The same neutral one-page fixture was built once from the private design reference with its body removed and once from the packaged template | The two 1414×2000 rendered page images were pixel-identical locally; the private control is not in the release |
| Word layout profile | A4, 11 pt Aptos body, 16/14/12 pt Aptos Display blue headings, bold numbered overview, orange key-term character style recurring through the narrative, gray italic captions, blue quote-style Q&A, Italian recap and continuous PAGE footer render in a synthetic preview | Passed automatically and in a Microsoft Word PDF export of the synthetic preview; exact Word export of a fresh full lecture remains to be checked |
| Full-lecture style pilot | A fresh `agy` `approfondito` draft was checked against a private quantitative lecture source; a false transcribed slide table was rejected and repaired in a separately tracked local candidate. The resulting 11-page DOCX has two source-aligned questions, 53 orange term runs, native equations in blocks and prose, and page numbers 1–11 in the local PDF. | Passed candidate validation and all-page LibreOffice visual inspection after documented local corrections; this is not an untouched provider response or a Microsoft Word export of the final candidate |
| Unattended quantitative lesson pilot | Native PowerPoint table cells are now extracted into the source pack. A fresh two-phase Agy run yielded a validated candidate without manual content edits; both slide tables matched source rows and a 9-page DOCX rendered with native equations, two questions and continuous page numbers. | Passed local content and LibreOffice checks; one large source image creates excess whitespace and needs layout refinement before release |
| Existing real quantitative candidate with new renderer | Rebuilt a saved full-lecture candidate in a fresh private test workspace; 9 pages rendered with native equations, updated headings and page numbers | Historical layout check only: the saved candidate predates the overview, Q&A and orange-term contract and is not evidence of the final design |
| Natural terminal alias | `mynotes codex|agy "... COURSE-LNN ..."` resolves a lecture and quality without publishing Word | Passed automatically |
| Real Codex candidate | One isolated generic demo source pack generated a validated, non-published `standard` candidate with `gpt-5.6-terra`, medium | Passed locally; no real-course quality benchmark |
| Real Agy candidate | A full private lecture produced a `standard` candidate with `gemini-3.8-flash-high`, high; the 10-page Word was rendered and visually reviewed, with local arithmetic/editorial corrections preserved separately from the raw provider response | Passed in an isolated workspace; no controlled quality comparison or unattended publication claim |
| FluidAudio and diarization | Adapters and speaker-enrollment path implemented | Automated only; no real run yet |
| Exact-manifest legacy migration | Reviewed-manifest hash, changed-source rejection, SQLite backup, repeatability, path map and excluded capture state | Passed automatically and on synthetic private copy; real cutover not authorized |
| GitHub CI and release download | Workflow builds the DMG candidate and uploads artifacts without publishing; current hosted run and browser installation have not been verified for this candidate | Pending |

No current public command produces AI notes through a fallback generator. The
tests establish local state, ingestion, extraction, source-pack and hardened
DOCX publication mechanics. The short WhisperKit run proves execution and timestamps, not a WER
target, full-lecture throughput, semantic quality, online retrieval, or fitness
for unattended course publication.

The full-lecture run exposed a wrong integer Total row and a Word equation text
rendering defect. The former now fails candidate validation and the latter is
fixed in the native-equation writer. One real run does not establish that speaker
labels alone caused quality gains; no speaker ablation was run.
