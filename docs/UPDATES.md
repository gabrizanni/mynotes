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
