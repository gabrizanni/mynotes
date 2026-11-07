# Git and GitHub workflow

## Branches

| Branch | Purpose |
| --- | --- |
| `main` | Stable release source; changes arrive through pull requests. |
| `develop` | Integrated development; ready for the next focused change. |
| `feat/<topic>` | A new behavior based on `develop`. |
| `fix/<topic>` | A targeted correction based on `develop`. |
| `docs/<topic>`, `test/<topic>`, `chore/<topic>` | Documentation, verification or maintenance. |
| `release/<version>` | An existing or explicitly prepared release branch. |

Topic branches normally merge into `develop`. A release pull request promotes
`develop` to `main` after the required checks and review. Preserve meaningful
commits with a merge commit. Never force-push shared branches or reset them to
erase history. Use `git revert` for an already shared change that must be undone.

Both shared branches require a pull request, the `test-build-audit` CI check and
resolved review conversations. Protection applies to administrators and blocks
force pushes and deletion. No separate approving reviewer is required for this
single-maintainer repository. GitHub merge commits preserve the individual
commits; squash and rebase merging are disabled.

## Everyday commands

```sh
git switch develop
git pull --ff-only
git switch -c fix/describe-the-problem
# Make a focused change and run the relevant checks.
git add path/to/changed-file
git diff --staged
git commit -m "fix: describe the resulting behavior"
git push -u origin HEAD
gh pr create --base develop
```

The local repository uses fast-forward-only pulls, pruned remote tracking refs
and simple pushes. Authentication belongs in the system credential store, never
in remote URLs or committed files. Use the GitHub no-reply email for new public
commits.

## Recovered development history

The Git setup retains stable commit `34e01e3` and the seven subsequent local
development commits through `77f9704`, preserving their original identities.
The previously uncommitted batch asset-root correction and soft-progress policy
are recorded separately. Generic agent exports are synchronized with that
policy and retain verified content hashes. This is recovered development work for the next
version, not a new public release or proof of deployed behavior.

The previous local checkouts remain intact. The new working checkout points
`origin` at the existing GitHub repository rather than a temporary local clone.
Original recordings, transcripts, personal databases, Word files, validation
receipts and private recovery material stay outside the public source tree.

## Verification

CI runs the existing test, native verification, public-tree audit and candidate
build job on pushes to `main` and `develop`, and on pull requests. Topic-branch
pushes run through their pull request to avoid duplicate candidate builds.
CI artifacts are candidates, not published releases. A branch being pushed or
merged does not by itself prove that checks passed; inspect the actual run.
