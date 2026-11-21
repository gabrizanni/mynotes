# Working on MyNotes

Use `main` for stable releases and `develop` for integrated development.
Start a focused `feat/`, `fix/`, `docs/`, `test/`, or `chore/` branch from the
current `develop` before changing files. Inspect the working tree first and
preserve unrelated user changes. Keep existing history; do not force-push shared
branches or rewrite published commits.

Commit coherent changes with messages that explain the result. Before committing,
review the staged diff, run relevant local tests and `git diff --check`, and run
`scripts/check_public_tree.py` for changes that will reach GitHub. This repository
is public: exclude credentials, personal workspaces, recordings, transcripts,
course documents, private reports and deployment receipts.

Use pull requests into `develop` for ongoing work. Promote tested changes from
`develop` into `main` through a separate pull request. A Git push does not install
the application, authorize paid model calls, or publish personal Word documents.
Read `CONTRIBUTING.md` and `docs/GIT_WORKFLOW.md` for the workflow and checks.
