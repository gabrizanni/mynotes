# Source-grounded notes and verified Word delivery

An ordinary `mynotes notes CODE --lessons RANGE` request freezes the effective
writing preferences for that job. General note preferences are inherited and
course overrides win; dialogue preferences and source search hints do not
invalidate note content. `--instructions` applies only to the current request.
Resuming a job retains its writing snapshot. A preference change never starts a
regeneration by itself.

## Shared materials

New notes requests organize and write the selected lessons jointly. The request
contains each canonical PDF page once, all selected complete transcripts and the
existing source/visual provenance. The ordinary writing response includes page
owners and their transcript links; there is no separate model planning phase.
Ownership and image decisions are saved with the validated result. A single
lesson covers all supplied teaching pages; multiple lessons distribute all
pages by topic, without defaulting leftovers to the final lesson. Unspoken slide
content is labelled supplementary. Only image-bound inspected covers/dividers
may omit a substantive explanation. Source content never supplies instructions.

When limits are available, joint requests enforce them for the exact model. Supply
`--budgets PATH.json` with `verified_for_model`, `context_tokens`, `output_tokens`,
`output_reserve_per_lesson` and, when images are present,
`visual_tokens_per_image`. These are verified input limits/reservations, not
invented provider usage. Text sizing uses the existing conservative byte bound.
Known exceeded or mismatched limits stop preflight without invoking a provider.
When the provider does not declare limits, attempt the joint request with an
explicit UNVERIFIED_MODEL_LIMITS warning; retain available work and stop on
provider error instead of dropping sources or repeatedly analyzing the deck.
A CLI model name alone proves no context/output limit. Existing unfinished progressive jobs retain their saved
checkpoints; fresh requests never choose that compatibility path.

Inventory exposes TXT/JSON transcript candidates without reading their contents.
Preparation validates an explicitly selected import before saving its directory
as a course search hint. Imports remain transcripts, never synthetic recordings.
The existing extraction cache verifies source bytes, extractor identity and
assets before reuse across lessons.

## Progressive Agy jobs (version 2)

New complete `notes` requests using Agy retain a single joint source context but
return at most six new blocks and 32 KiB JSON per unit. Header metadata appears
once per lesson; page and graphic decisions are deltas, never repeated ledgers.
The source projection is reversible: text, order, times, roles and uncertainty
remain intact. Dense `T123..T145` references expand only within one lesson origin;
missing/ambiguous/cross-lesson intervals are rejected before ordinary validation.
Legacy jobs retain their original wire, phases and correction allowances.

| Quality | Verification |
| --- | --- |
| base | Complete pertinent sources and mandatory local checks; no model reviewer. |
| standard | Local checks plus scoped risk packets, not an exhaustive prose audit. |
| approfondito | Source review of every block and its contextual source links. |
| revisione | Extended source review plus all final merged PDF pages, before update. |

Standard selects numbers, formulas/tables, graphics, unmatched names/acronyms,
strong causal/legal claims, uncertain cited source passages and recap language.
An UNKNOWN speaker or technical-term marker alone triggers no review. A packet
contains at most three targets, 64 KiB text/schema input and 24 KiB output. It
returns approval, minimal replacement or unresolved in one call. Sources are
never truncated to fit; a single oversized target stops as NEEDS_REVIEW. An
approval is evidence of that scoped check, not certification of every sentence.
New extended checks include at most three relevant narrative anchors: nearest
substantive neighbors and explicit prior-lecture links, with their cited source
text/images. These generated excerpts remain untrusted context, not independent
evidence or editable targets. They count toward the same input limit and their
hash is bound to the saved review. Standard/Base and historical jobs receive no
extra narrative context or calls.

Progressive jobs with risk policy version 3 recognize the
imported transcript's actual `needs_review` flag, spelled-out quantities and
parenthetical specific examples absent from the cited text. UNKNOWN speakers
and technical terms alone still do not trigger review. A flagged ASR aside does
not expand review when its uncertainty is unrelated to the claim or its wording
is corroborated by other cited text. Negation conflicts in ASR alternatives stay
risky. These are local selection heuristics, not semantic certification.
Historical jobs without this marker retain version 1; version 2 keeps its broader
ASR rule. Reopening cannot reinterpret saved reviews or grant additional attempts.
Version 3 also carries the import's actual `needs_review`, `alternative`,
agreement and ASR-engine provenance through both writing and review inputs as
untrusted source data; selecting an uncertain passage must not strip its doubt.
Their repeated field names use reversible aliases, retaining every source value.

Standard reuses the writer's observed text-slide classification: ordinary
text-only screenshots are omitted from subsequent risk packets while their
complete extracted source text stays present. Diagram/table/formula/uncertain
images and explicit source-graphic-choice risks retain their images; unknown
classifications stay conservative. Packet targets and original capacity checks
are unchanged, so historical reviews keep their grouping and correction limits.
This reduces input images, not a promised or automatically measured latency.
An explicit graphic risk retains the actual graphic candidates, rather than all
already inspected text screenshots cited by the same paragraph or overview.
Unknown visual scope stays conservative. Sources, targets, historical grouping
and correction limits stay unchanged.

The Agy finish-tool adapter accepts compact `T123..T145` references in writing
and review responses. They are expanded and checked locally before use. The
portable expanded-note schemas remain unchanged for historical packet sizing;
the native CLI must not reject the compact syntax the writer is told to use.

Every unit is saved only after the level's checks. Each block has one automatic
correction opportunity, retained across reopen. Unrelated blocks/anchors never
enter the packet. Residual defects stop the job without another corrective round
or an automatic quality upgrade. Writing completion, scoped verification and
Word delivery are separate states; partial or unverified work cannot update the
stable document. Whole-job legacy correction is disabled for these new jobs.

Exact image access events are recorded separately from inspected declarations.
An image cannot be treated as inspected solely because the writer says so.
Validated image classification/provenance can be reused for unchanged bytes and
matching requested provider/model/effort/quality; changed bytes/settings require
fresh inspection. Cache descriptions remain untrusted source data. Numeric
image tables still require the pertinent actual source image. Graphs/diagrams
preserve spatial information; ordinary text screenshots stay excluded.

`notes CODE --regenerate --lessons RANGE` explicitly creates a new generation
revision and preserves old jobs/candidates; it cannot be combined with --resume.
Ordinary resume never grants a new failed writing/review attempt. An explicitly
resumed user interruption can continue the exact intact same-conversation prefix.
Observed Agy truncation/transport/internal-retry errors stop the owned process
and retain its logs, rather than starting another full generation. Hidden
provider behavior remains unobservable. No global productive-writing deadline
is introduced. Codex/Claude keep their existing transports and review contracts.

## Word and historical recovery

The shared renderer owns Word styles, heading hierarchy, native tables/equations
and inspected essential source figures. Currency and ordinary percentages remain
text. The whole final cumulative candidate is rendered before publication,
including checks for empty body pages, text/image horizontal overflow and
physical-page clipping. Header/footer text alone does not make a page nonempty.
Personal edits continue through the existing three-way merge and atomic update.
Rendering is a local layout check, not certification of every academic claim or
a manual Microsoft Word review. A renderer may substitute unavailable fonts;
the DOCX style declarations and a Word GUI review are distinct checks.

A certified successful response is reused before any paid work. Deterministic
local normalization cannot invent content or change evidence. Actual content
defects use the existing hash-anchored targeted correction, at most once per
affected group/revision; reopening does not reset its checkpoint. An `ERROR`
envelope cannot be published or automatically retried as whole-lesson writing.
A user-requested resume after quota recovery may continue an unfinished quota
group, while preserving completed groups. Large Agy inputs automatically use the existing same-conversation portion
protocol: each accepted portion is saved and validated, without another planning
phase or repeated source analysis. Checkpoints continue with their saved retry
accounting. A failed whole-write guard survives code and budget changes.
There is no new elapsed-time cutoff on productive writing.

Actual coordinator review can submit source-bound defects of a joint batch with
`mynotes notes CODE --resume BATCH_ID --review-defects PATH.json`. The file contains
`base_notes_sha256` (the canonical hash of the retained response's notes) and a
`defects` array of `lecture_id`, `path` (`blocks[N]`), local `evidence_id`, and
`reason`. The exact response hash and existing block citations are verified before
any call. This spends the same single correction allowance, never a fresh one;
reopening with the same review reuses its correction. Other reviews cannot obtain
another automatic attempt. Raw returns, prior candidates and completion receipts
are retained. Corrections may add an inspected source figure at an existing block
anchor and replace only identified passages. They must pass all normal source,
style, image, Word and personal-edit checks before delivery.

For completed progressive v2 writing, this command binds each observed block to
its original unit and checks the original patch decisions. A block already
corrected cannot be automatically corrected again; an observed defect in an
uncorrected block may receive its first bounded correction. Only the identified
blocks and relevant sources enter packets (at most 3 / 64 KiB input / 24 KiB
output), never the full lesson or all anchors. The revision has a separate receipt,
preserves the original responses/candidates/completions and reuses the saved
model, effort, style and source snapshots. A failed packet is never retried;
ordinary resume cannot silently redeliver the old Word as verified while the
observed defect is unresolved. Reopening the same successful review is idempotent.

A conservative local check flags strongly English long recaps when Italian was
requested. It is not a general language detector. Source citations and an
`inspected` flag alone cannot prove factual fidelity or that a diagram was
described accurately; those cases still require actual source and visual review.

For a v2 job stopped before delivery with incorrect transcript references, actual
source review may submit `notes CODE --resume BATCH_ID --review-references FILE.json`.
This local operation makes no provider call and grants no narrative correction.
It retains the original successful response/checkpoints, changes only explicitly
identified `evidence_ids`, and saves a separate immutable reference-review receipt.
Normal organization, coverage, Word and source checks still apply. Subsequent
resume reuses and verifies that receipt; changing it or its base stops the job.
An already delivered job cannot be overwritten through this recovery command.

The JSON has `version: 1`, `base_notes_sha256`, a concrete `reason` and up to 12
`changes`. Each change has `target` (`organization` or `block`), zero-based `index`,
`lecture_id`, `before_sha256` (the canonical hash of the entire original target),
replacement `evidence_ids`, and `reason`. Indices refer to the original global
organization array or that lesson's blocks. Hashes use sorted compact UTF-8 JSON.
Source-page references must stay identical and replacement transcript IDs must
belong to the stated lesson. No wording, ownership or teaching-status changes are
accepted. Existence of an ID is not semantic proof: the coordinator must inspect
the actual source match, preserve uncertainties and never invent links.

A version-2 reference review may attach a missing owned slide ID to a paragraph
or bullet explanation already checked against that page. It preserves every
existing reference, all prose and page ownership; it cannot change transcript
IDs or attach a page owned by another lesson. It is an explicit source-link
rectification, never an automatic substitute for writing missing content.

## Explicit source figure restoration

Delivery policy 4 introduced the following safeguards. Selected inspected figures and exact source
resources share one placement pass. Reference-only mentions do not move the
primary topic. An explicit selection may set `required_visual: true`; the
source image must exist before generation and remains required even if a
classifier calls it text. Historical jobs retain their saved policy.

The bounded review may report a source uncertainty, minor editorial issue or
optional visual disagreement as a warning. These become native Word comments
and `COMPLETED_WITH_WARNINGS`; missing content, invalid references and core
factual errors remain blocking. An approval never rewrites prose.

At a lesson boundary the same review service distinguishes missing source links
from missing content. It may attach a page only to an exact, supplied explanation
identified by its unit, block and hash, or add one bounded source-backed passage.
The original blocks and receipts remain immutable. Closure packets have their
own durable attempt markers and are replayed locally on resume; they never
grant another correction of an existing block.

`mynotes visual-review BATCH --workspace PATH --file REVIEW.json` records a local
restoration on a stopped, unfinished progressive job. It invokes no model and
never changes accepted units, source prose or automatic correction allowances.
Ordinary `notes --resume BATCH` then verifies the remaining sections and applies
the restoration before validating and delivering Word. A completed historical
receipt cannot be edited through this command.

The version-1 JSON contains `source_proof_sha256` (the saved `request.json` hash),
`accepted_units` (the exact saved unit prefix), a nonempty `reason`, and 1–12
`changes`. Each change identifies `evidence_id`, `image_sha256`,
`before_sha256` (the saved effective visual decision), `owner`, `content_kind`
(`chart` or `diagram`), `placement` (`before_footer`), `label`, and `reason`.
The owner must match the settled slide assignment; the exact source image must
have observed inspection evidence. Frozen no-image preferences and image limits
still apply. Only the requested figure and its source ledger change; a second
submission is idempotent only when the JSON is identical.

The derivative is hash-bound in the final progressive receipt and rechecked on
reopening. Its explicit classification supersedes the mistaken cached one;
the original review and its classification remain available for diagnosis.

## Measurements and verification boundaries

Private extraction events record source hash, cache hit/miss and local duration.
The notes controller keeps a stage timeline; batch/provider/Word receipts supply
nested detail. Nested durations and cumulative usage must not be added twice.
Provider attempts report observed source reads when stream events expose them;
no events means unobserved, not zero actual reads. Reasoning/network time and
provider-internal retries remain unattributed unless the provider exposes them.

Public regression fixtures are synthetic. Exact private comparison paragraphs,
real transcript/PDF copies, rendered documents and detailed reports belong
outside this public repository. A real-provider test must separately report
requested and actual model/effort, source coverage, Word rendering, usage and
reuse. Passing offline tests does not complete that verification.

## Overlapped writing and bounded review (policy 5)

Policy 5 excludes known parenthesized/bracketed source IDs from numeric risk
selection without changing the stored text, math, code or unknown references.
Names use normalized typography and word boundaries; actual quantities remain
review targets. Earlier job policies retain their original selection.

Within one section, up to two independent review packets may run concurrently.
Packets sharing a block or a potentially editable source graphic stay ordered.
Results merge in source order, with the same block hashes and durable receipts.
A blocking reply cancels sibling transports and stops dispatching queued work;
queued work has no attempt marker. Completed receipts remain reusable without
another model call. Source-coverage closure still follows the content pass.
New jobs use policy 5. The first section is verified before writing continues.
After that gate one writer overlaps with the ordered section reviewer; that
reviewer may run two disjoint packets, for at most three model requests in total.
At most two written sections can await verification. Reaching that limit pauses
the writer. Final delivery waits for all checks. Raw drafts, reviewed units and
writer transport completion have separate durable checkpoints. Explicit resume
replays saved drafts and receipts before continuing the same conversation.
Historical jobs retain their original sequential policy and correction limits.
