# Release validation status

This document separates implemented safeguards from checks actually completed.
The current artifact is a local development release candidate, not a published
GitHub release.

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
| GitHub CI and release download | Workflow prepared but no remote exists | Not run; hosted publication requires repository/account authorization |

No current public command produces AI notes through a fallback generator. The
tests establish local state, ingestion, extraction, source-pack and hardened
DOCX publication mechanics. The short WhisperKit run proves execution and timestamps, not a WER
target, full-lecture throughput, semantic quality, online retrieval, or fitness
for unattended course publication.

The full-lecture run exposed a wrong integer Total row and a Word equation text
rendering defect. The former now fails candidate validation and the latter is
fixed in the native-equation writer. One real run does not establish that speaker
labels alone caused quality gains; no speaker ablation was run.
