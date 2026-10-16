# Versioned updates and recovery

Download the intended new DMG, replace the MyNotes setup app in Applications, and reopen it. Component setup prepares a versioned runtime and switches the `current` launcher target only after its checks pass. Completed setup/download work is reused on retry. Earlier runtime versions remain available for rollback. The update is explicit; no unattended GitHub publication or automatic legacy migration is performed.

Capture holds an exclusive lease during recording and export. Both update and rollback defer while it is held. For an active, idle Capture workspace, the installer stops and restarts the LaunchAgent around the update and refreshes its Python runtime path. Rolling back to a version without Capture requires disabling the recorder first. Failed dependency installation preserves the previous selection and removes the incomplete installation.

After an app update, check microphone access again with a short recording and playback. A successfully verified executable alone does not verify microphone authorization or the audio you need.

## Advanced recovery

The retained manual ZIP installer supports `--list-versions` and `--rollback VERSION`:

```sh
zsh scripts/install-mynotes.sh --list-versions
zsh scripts/install-mynotes.sh --rollback VERSION
```

Run these from a verified standalone release with its required Python available. They do not overwrite course preferences or generated handouts. `mynotes setup` revisits component checks; `mynotes doctor --json` reports next actions separately for installation, provider, transcription, Word and Capture.

No current release performs a private-workspace schema/database migration. Before introducing one, create a timestamped backup and document the transition. Updates must never reprocess lectures, replace generated handouts or discard personal settings.

For a development clone, preserve local modifications before updating. Do not use an automated update to discard them.
