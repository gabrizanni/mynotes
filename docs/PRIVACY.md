# Privacy and release boundary

The public release contains application code, generic documentation, synthetic
examples, locked dependencies, a content-free Word design template, and generic
integration templates only. It does not contain a private workspace,
configuration, provider credentials, logs,
audio, transcripts, course materials, or generated handouts.

By default, a private workspace is created under macOS Application Support.
Its SQLite state remains local. Any recordings, slides, notes, and handouts
remain in user-selected locations and are never uploaded or copied by the
installer.

Legacy migration starts with an inventory-only dry run. An apply creates copies
inside a new private workspace; it does not delete or modify the old project.
