# Versioned updates and recovery

An update is explicit: download the intended versioned release and run that
release's installer. The installer creates a version-specific application
directory and switches only the `current` launcher target after the new
environment has passed `mynotes doctor`. The earlier application directory is
kept for rollback.

No current release performs a private-workspace schema or database migration.
Before a future release introduces one, it must first create a timestamped
backup and document the schema transition. Updates must never overwrite course
preferences, reprocess lectures, or replace generated handouts.

For a development clone, stop before updating if `git diff --quiet` reports
local modifications. Commit, stash, or copy the changes first; do not use an
automated update to discard them.

## Version 0.1.1

- Agy completes its initial greeting without exploring the project. Source,
  configuration and Word checks are retained before concrete work.
- Bundled, content-free Word template: 1.5 cm margins, Aptos 11 body,
  native Heading 1/2/3 for lessons/sections/subsections and visible orange
  character style `Termine tecnico` for technical terms.
- Ordinary black emphasis is distinct from technical terms; native equations,
  lists, repeated table headers and captions are preserved.
- Generation supports source-aware style checks and scoped review.

The application includes the neutral template and its builder, not the private
reference document or generated lecture notes. Existing workspaces and handouts
are preserved; formatting changes apply to new candidates.
