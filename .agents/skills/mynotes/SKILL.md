---
name: mynotes
description: Coordinate MyNotes course setup, evidence preparation, note generation, Word validation and safe publication.
---
# MyNotes

Use the installed `mynotes` command and the workspace in `MYNOTES_WORKSPACE`.
Run `mynotes doctor` and `mynotes status` first. For a new lecture use the real
pipeline: `prepare`, `generate`, `build`, then `publish`. Building is candidate-only;
publishing requires a validated rendered DOCX. Never invent a provider success,
silently drop evidence, overwrite an externally edited Word, or launch `mynotes chat`
from inside this session. Use `redo` only when the user explicitly requests a rewrite.
Private recordings, notes, paths and credentials must remain in the private workspace.
