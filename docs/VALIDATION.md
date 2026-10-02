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
| Natural terminal alias | `mynotes codex|agy "... COURSE-LNN ..."` resolves a lecture and quality without publishing Word | Passed automatically |
| Real Codex candidate | One isolated generic demo source pack generated a validated, non-published `standard` candidate with `gpt-5.6-terra`, medium | Passed locally; no real-course quality benchmark |
| Real Agy candidate | One isolated generic demo source pack generated a validated, non-published `standard` candidate with `gemini-3.8-flash-high`, high | Passed locally; no real-course quality benchmark |
| FluidAudio and diarization | Adapters and speaker-enrollment path implemented | Automated only; no real run yet |
| Exact-manifest legacy migration | Reviewed-manifest hash, changed-source rejection, SQLite backup, repeatability, path map and excluded capture state | Passed automatically and on synthetic private copy; real cutover not authorized |
| GitHub CI and release download | Workflow prepared but no remote exists | Not run; hosted publication requires repository/account authorization |

No current public command produces AI notes through a fallback generator. The
tests establish local state, ingestion, extraction, source-pack and hardened
DOCX publication mechanics. The short WhisperKit run proves execution and timestamps, not a WER
target, full-lecture throughput, semantic quality, online retrieval, or fitness
for unattended course publication.
