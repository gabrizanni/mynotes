# Contributing

MyNotes uses `main` for stable source and `develop` for integrated development.
Create a short-lived branch from `develop` for each coherent change:

```sh
git switch develop
git pull --ff-only
git switch -c feat/describe-the-change
```

Use `fix/`, `docs/`, `test/`, or `chore/` when those prefixes better describe the
work. Commit related changes together with a concrete message such as
`fix: preserve transcript identity during recovery`. Review `git diff --staged`
before committing. Do not mix source changes with generated artifacts or personal
data. Existing historical commit messages do not need rewriting.

Set up Python 3.12 or later and the locked environment:

```sh
uv sync --locked
uv run pytest
uv run python scripts/check_public_tree.py
git diff --check
```

For native Capture or installer changes, also run the relevant native checks
described in `scripts/README.md`. Local synthetic tests do not certify a real
model call, microphone capture, Word review or installed-app update.

Push the topic branch and open a pull request targeting `develop`. Explain the
problem, resulting behavior, checks performed and any unresolved limitation.
Keep meaningful commits; prefer merge commits when integrating so the branch
context and individual commits remain visible. Delete merged topic branches
when they are no longer needed.

Stable releases use a separate pull request from `develop` to `main`, followed by
an appropriate version tag and the existing release process. Do not tag an
unreviewed development snapshot as a release. See `docs/GIT_WORKFLOW.md`.
