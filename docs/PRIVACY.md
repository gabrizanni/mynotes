# Privacy and release boundary

Recording and transcription run locally. The generation stage invokes the selected Codex, Agy or Claude Code CLI: transcript text, selected course materials and relevant visual evidence may be sent to that provider. An interactive agent can read files and run commands under its own permission settings. Its account policies, retention rules and possible usage charges apply; do not treat MyNotes as an entirely offline AI service.

MyNotes does not create provider accounts or collect passwords, API keys or authentication tokens in course configuration. Providers manage their own authentication and may store credentials through their own tools.

The public release contains application code, generic documentation, synthetic examples, locked dependencies, a content-free Word design template, generic integration templates, native setup/Capture apps and the packaged runtime tools. It excludes private workspaces, configurations, provider credentials, logs, audio, transcripts, course materials and generated handouts. The builder uses an explicit public-file allowlist and hashed release manifests.

Private configuration, SQLite state, candidates and temporary files remain under macOS Application Support. Recordings, materials and handouts default to `~/Documents/MyNotes` or locations the user chooses. The installer does not upload course files or migrate an existing personal workspace. Component downloads contact the published Python/package, speech-model and LibreOffice sources; no course material is needed for those downloads.

Capture stores calendar links, timetable revisions, command responses and session manifests locally. A subscription URL may contain an access token; never commit it to the public repository or paste it into public logs. Calendar refresh contacts the configured HTTPS feed. Microphone segments remain local; final audio goes to the selected inbox. A sidecar marks partial coverage. Staged attempts are retained until the user removes them.

Legacy migration is separate and begins with an inventory-only dry run. An explicit apply copies into a new private workspace; it does not delete or modify the old project. Setup and updates do not authorize that migration.
