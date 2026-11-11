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

## Word and recovery

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

A conservative local check flags strongly English long recaps when Italian was
requested. It is not a general language detector. Source citations and an
`inspected` flag alone cannot prove factual fidelity or that a diagram was
described accurately; those cases still require actual source and visual review.

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
