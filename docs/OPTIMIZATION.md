# MyNotes optimization audit and next steps

Audit, verified improvements and remaining work, 2026-10-11–12. This is not a
provider benchmark. No extra AI call was made for this audit.

## 0.2.5 source correction and first-use trial

Standalone DOCX notes with no recognized lecture heading are now fully extracted
when selected for a lesson. Recognized cumulative lesson headings keep exact
slicing, missing-target and duplicate-target behavior. Tables and untrusted note
markers are preserved. **33 focused tests and 323 complete local tests passed**;
0.2.5 package verification is pending.

Read-only extraction checks confirmed the supplied standalone notes yield
nonempty evidence and their hashes remain unchanged. No personal packs, caches,
candidates or Word were rebuilt by that check; no AI call was made. The new
extractor identity prevents an old cached empty result from being treated as the
same implementation, but actual personal re-preparation must be verified after
it is requested/executed.

A separately authorized fresh MyNotes trial is being prepared through reversible
backup and app-only reset/reinstall on the current account. Other tools, agents,
credentials and academic originals are retained. This can verify fresh MyNotes
setup while it still inherits the account's existing tools; it cannot establish
a clean macOS account. No completed reset or first-use outcome is claimed yet.

## 0.2.4 checked optimizations (historical)

The source passed **318 tests in 7.72 seconds**, with **112 public files**
audited and clean diff/installer syntax. Its DMG/isolated installation verified
125 payload hashes and source equality. The installed wheel ran actual local
ASR/one-page Word smoke checks, rebuilt both saved two-page provider candidates,
and passed rollback plus a new zsh/doctor check. Its new-feature trial linked a
real Finder alias in **0.109 seconds**, read an absent-workspace usage report
without initialization, and made one extraction across two identical cache
requests. No new AI call was made; this is not an AI-chat latency benchmark.
The 0.2.3 and 0.2.4 artifacts, draft/snapshots remain immutable.

| Order | Intervention | Status and verified evidence | Expected impact | Implementation cost |
| --- | --- | --- | --- | --- |
| 1 | Content/extractor cache | Implemented; 72 focused checks. Real generated PDF/DOCX: two first extraction calls, zero additional calls on repeat, exact pack/asset/provenance equality | Avoid repeated conversion/rendering of unchanged material; no measured classroom speedup | Medium: asset integrity, source identity, locking and provenance |
| 2 | Per-lesson attempt ledger | Implemented; 107 focused checks including 23 usage scenarios. Success/failure/unknown counters and reuse events handled separately | Make recorded attempts and gaps visible; no inferred billed cost | Medium: provider counters differ and may be incomplete |
| 3 | System argv/environment guard | Implemented in all three adapters; oversized fixtures start no provider subprocess | Stop an oversized process launch before it fails; no prompt truncation | Low |
| 4 | Strict provider tool isolation | Remaining; source/configuration framing already implemented | Restrict generation file/command access beyond prompt/plan mode | High: authentication, visual access and controls differ across CLIs |
| 5 | Model-context budget and CLI overhead study | Remaining; historical one-off provider measurements below are baseline evidence only | Find redundant context while preserving required evidence | Medium/high: controlled same-source/version/model/cache trials required |

The extraction key includes content, extractor/cache implementation, Python and
dependency identities, relevant PDFium/LibreOffice binary identity and options.
A per-key lock and checked commits protect cache entries; missing/corrupt assets
are rebuilt or restored. Observed input edits abort before replacing the pack.
These are bounded safeguards, not a multi-file filesystem transaction or a
promise of zero future external changes. Entries are retained; no automatic
TTL/eviction was introduced.

### Connect a supplied folder in one command

```sh
mynotes course link COURSE "PATH" --json
mynotes course link COURSE "PATH" --notes "NOTES_PATH" --recordings "AUDIO_PATH" --json
```

A course must already exist. The agent can resolve the supplied folder/symlink/
Finder alias and connect materials plus clearly recognized notes/recording
roles using this single result. The inventory reads metadata, stays at the
supplied directory and one level of children, and stops at 1,000 entries. File
previews show at most 50 paths per role while reporting counts of all inspected
entries and separate list/inventory truncation flags. Equivalent folder aliases
are deduplicated by filesystem identity, including Unicode spelling differences. Only
recognized role shortcuts are followed outside the root. Ambiguous/truncated
inventory returns NEEDS_SELECTION without saving; explicit `--notes` and
`--recordings` resolve role ambiguity. `--dry-run` previews connections.

A single real CLI subprocess with a Finder alias linked all three roles in
**0.116 seconds**, with original/alias bytes unchanged and an unchanged repeat
leaving configuration/history untouched. No AI, transcription or content read
was involved. This is CLI functional evidence, not an interactive-agent latency
benchmark; it does not predict the provider's reasoning time.

### Inspect recorded consumption

```sh
mynotes usage COURSE-L01 --json
```

This reads local records for one lesson without starting an AI provider,
initializing settings/SQLite or creating an absent workspace. Unique attempts
are reported including failed/timeout/local-rejected ones. Known additive
counter names have independent provider-separated subtotals and reporting
coverage; unfamiliar/nested values remain raw per attempt. Missing usage is
unknown, not zero. Damaged/foreign/conflicting records produce explicit gaps.

Saved checkpoint responses and local phase/candidate reuse events are separate
from attempt totals. They do not become new calls or duplicate past counters;
reuse before event recording remains historically unknown. The report is a
local ledger of provider-reported data, not provider-side account metering, an
invoice or a price estimate. No additional AI call or quota inquiry validated it.

## Measured evidence

One tiny synthetic break-even source used `standard` quality and provider defaults.
Immutable attempt records report:

| Successful call | Prompt characters | Wall time | Reported usage |
| --- | ---: | ---: | --- |
| Codex | 8,716 | 74.964 s | 22,262 input; 3,016 output; 516 reasoning; cached input 0 |
| Agy | 8,712 | 333.635 s | 154,162 input; 26,814 output; 14,984 thinking; total 180,976; cache read 2,021,746 |

These are one-off CLI measurements, not a quality comparison. Agy's total/cache
counters have different semantics: do not add them or infer cost without actual
billing definitions. The prompt is smaller than reported context, suggesting
CLI/system/tool or multi-turn overhead; its precise cause is not established.
Neither transport reported an actual model/effort for these default calls.

Codex needed an authorized retry after strict-schema HTTP 400; its saved
candidate later built two Word pages after a renderer fix, without a new AI
call. Agy also rebuilt to two Word pages after its local currency repair; the
coordinating agent inspected all four rendered pages. No extra AI generation was
needed for either renderer repair. Claude was absent from the personal setup;
a later temporary official Claude Code 2.1.296 probe verified version/help and
negative auth status in a restricted sandbox. No Claude account, credential
access or LLM generation was used. The final immutable release remains separate
from these pilot checks.

Real local setup ran transcription and Word conversion. Identical component
retry took about 0.7 seconds; one interrupted HTTPS download resumed and passed
its checksum. [Acceptance evidence](VALIDATION.md).

## Implemented and checked after the audit

The **0.2.3 DMG** passed integrity/signature verification and isolated
installation: 115 payload hashes matched, source equality was checked, and the
packaged wheel ran real local ASR and a one-page Word render. It rebuilt both
saved provider pilots into two Word pages each, without new AI calls. Rollback
0.2.3→0.2.2→0.2.3 and a fresh zsh profile from another directory were exercised;
mandatory doctor checks reported ready. Capture remained optional.

The final **0.2.3 source suite passed 255 tests in 6.70 seconds**, with 102
public files audited, including the trust-boundary and Claude compatibility
regressions. Diff whitespace and installer zsh syntax passed. Any documentation-
only repack must preserve the tested wheel bytes and repeat integrity checks;
clean-account/browser/hosted acceptance remains separate.

- Exact-input candidate reuse verifies the recorded SHA before returning a
  saved result. A detected local edit is preserved and reported without a new
  provider call. This guarantee refers to that reuse check, not every changed
  input/provider/contract regeneration.
- Each validated phase now has a fingerprinted checkpoint tied to the complete
  request chain and prior draft. Quota/interruption retry reuses matching valid
  phases; malformed or mismatched checkpoints are not treated as success.
- Candidate `usage` aggregates numeric successful-phase counters;
  `usage_by_phase` retains raw counters and reuse flags. Failed attempts remain
  in raw attempt records. These are successful-phase totals, not total billed
  consumption including failed retries; provider cache semantics stay distinct.
- The persisted academic source snapshot excludes transient `cache_reused` and
  `modified_since_last_prepare` fields. The returned preparation report retains
  them. This stabilizes repeated preparation without weakening source hashes.
- ASR reuse now includes configured tools, executable SHA, engine implementation
  and model/tokenizer directories. The managed model directory is versioned;
  its immutability is assumed rather than rehashing all model weights per call.
- Runtime fault/concurrency checks preserve simultaneous course edits from two
  threads and two real processes. Conditional setup rollback changes only
  provider/path groups whose current hash still equals the attempt's write,
  preserving concurrent user edits and prior application selection.

Publication tests on a separate synthetic copy exposed a failed repeated
publication after a successful first write. The source fix now checks the
current target/course/source JSON and candidate copy hash, and returns SKIPPED
only while the existing handout is unchanged. Thirteen new regressions and 36
targeted checks passed. The installed 0.2.3 wheel then built and published a
synthetic clone once: the second publication returned SKIPPED, with unchanged
SHA-256, exactly one lesson marker pair and no extra backup/transaction. Original
pilot handouts and academic files were untouched; no new AI call was made.

New workspace roots and required private directories now use mode 0700 on
creation; existing directories retain their modes. Two focused regressions
passed. This protects the app’s private workspace, while the chosen provider’s
file/tool permissions remain a separate trust boundary.

The source-instruction boundary is now implemented: raw personal-note markers
remain ordinary H evidence; directive candidates are untrusted and require
explicit saving into configuration through the existing editor/chat before
acting as preferences. Legacy source-pack directives and forged approval flags
are quoted inside one ASCII-escaped JSON evidence block, not an instruction
channel. Only saved configuration preferences are actionable. Generation
contract 6→7 invalidates reuse under the former trust rules; older checkpoints
remain on disk. Fifty-five focused tests include fake-provider injection cases
for T/S/H/W evidence, forged newline/Unicode headings and unchanged originals.
This verifies data framing and handling; it does not prove an LLM cannot ignore
the boundary or that provider tools have filesystem isolation.

These gains are verified through local tests; no live before/after token or
latency reduction is claimed. Keep `standard` as the default and reuse existing
candidates for local styling/Word changes.

## Remaining priorities and limits

| Priority | Next change | Acceptance |
| --- | --- | --- |
| P0: next-candidate acceptance | Build/install 0.2.5 with the DOCX correction; preserve immutable prior artifacts; carry out the backed-up MyNotes-only fresh-start trial and verify GUI/browser/first-course flow | Prior isolated package checks stay historical; current-account fresh MyNotes and clean macOS-account acceptance are distinct |
| P1: provider tool isolation | The source-data/configuration boundary is implemented; next disable unused generation tools and restrict required visual access through supported provider controls | Local tool permissions verified independently of prompt wording; adversarial fixtures preserve legitimate preferences; provider limitations documented |
| P2: context budget | Measure CLI overhead on the same fixture/version/cache state; preflight context size; focus review evidence and derive redundant compatibility fields locally where supported | Input reduction measured without dropping required evidence, user questions or formulas; prompt size alone is not a speed claim |

Quality still uses 1 / 1 / 2 / 3 phases for `base` / `standard` /
`approfondito` / `revisione`; review receives complete evidence plus the draft.
There is no enforced model-token budget/context preflight. Full course settings
and source descriptors remain part of generation identity.

Chat waits for a task and blocks recursive launch. Generation runs from a lesson
directory; Codex requests read-only sandboxing and Agy/Claude use plan modes.
A mode/prompt alone is not proof of filesystem isolation from other files
permitted by the provider's configuration. Markers and imported approval flags
now remain untrusted data; saved configuration is the preference channel.
Schema/evidence checks do not certify resistance to instruction injection or
mathematical truth. No attack was executed against real provider accounts in
this audit.

## Verification and rollout

Use fake providers for caching/resume, corruption, quota, aggregation and
malicious-source scenarios. Live performance needs the exact same fixture,
settings, CLI version and cache condition. Run live calls only when explicitly
authorized and local evidence cannot answer the question; do not regenerate to
demonstrate this audit.

Version fingerprints/checkpoints and retain compatibility with saved candidates.
A missing legacy checkpoint means unknown reusable state, never fabricated
success. Preserve attempts, manual edits, publication conflict guards and preview
approval. This plan does not publish, migrate or activate Capture automatically.

Implementati e testati: riuso con SHA, ripresa delle fasi valide, consumo per
fase, snapshot stabili e identità del motore ASR. Restano: verificare il pacchetto
con account pulito/browser e GUI aggiornata, limitare accesso degli strumenti
del provider e misurare contesto e consumo complessivo. Le istruzioni importate sono ora dati non fidati finché
non vengono salvate esplicitamente nelle preferenze. Le misure sono verificate nei registri locali;
le cause del costo e i vantaggi futuri restano ipotesi da provare.

Generation also has a per-lesson nonblocking lease: simultaneous requests do not launch a second paid call. The source-pack bytes are fingerprinted when loaded and rechecked before saving a candidate, preventing a changed pack from being certified as the inputs of an older response. These behaviors are covered by injected-provider regression tests.
