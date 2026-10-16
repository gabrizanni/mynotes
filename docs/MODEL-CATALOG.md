# Dynamic models in MyNotes

The MyNotes tab uses a compact model menu with effort options in each model's
submenu. **Refresh models** queries the selected installed CLI; model
names and effort names are not an application-maintained allowlist. New releases
appear when that CLI exposes them to the active account, without rebuilding
MyNotes. Older CLIs may need a provider update before exposing a new release.

The three historical presets bootstrap the initial selection when no snapshot
has been saved. Saved models are treated as available without a fresh check. Agy can
expose Gemini, Claude and GPT models through the same agent. Its High/Medium
model variants and agent-level effort are distinct settings.

```sh
mynotes models --provider codex --refresh --json
mynotes models --provider agy --refresh --json
mynotes models --provider claude --refresh --json
```

The version-2 JSON response retains `provider`, `choices`, `default_preset` and
`message`, and adds `presets`, status, account metadata, source and timestamps.
Choices use real backend IDs; effort names remain extensible strings. The SDK
and CLI own authentication. MyNotes does not read OAuth tokens, start a model
turn, or infer access from a public subscription table.

- Codex: stdio app-server handshake, account/read and paginated model/list.
  This catalog can reflect the CLI's cached model metadata; the UI distinguishes
  acquisition time from the provider/cache timestamp. It does not assert a
  generation test or subscription entitlement from listing alone.
- Agy: models and help for agent effort capabilities. The local `/usage` report
  is optional and bounded; absence of quota metadata is not a failed model list.
- Claude Code: SDK control initialization with no user frame, session persistence
  disabled and an empty MCP configuration. User hooks are disabled for this
  process; managed policy can still impose its own startup behavior. Unsupported
  CLI versions or missing installations remain explicit rather than guessed.

The operation has a 20-second metadata deadline. Cancellation terminates only
the owned metadata process group. stdout and stderr are drained separately and
bounded. GUI subprocesses restore a missing HOME from the OS account, preserving
the normal authentication directory.

Private snapshots live under `MYNOTES_HOME/model-catalogs`, mode 0600. Opening
the app, changing agent, returning from login, account/config changes and elapsed
time never refresh or invalidate the saved model list. Failed refreshes retain
the last valid snapshot and keep its models selectable. Only a successful explicit
refresh adds/removes models. A removed saved selection remains identifiable but
is omitted from the selection menu; choose another model explicitly. Models are
not automatically substituted, and real provider errors remain actionable.

**Save choice** persists the model/effort pair for that agent. Chat and note
generation use the same pair. Only the user's **Refresh models** action (or an
explicit CLI `--refresh` request) queries provider metadata. Reading the saved
list and changing preferences do not call discovery. No background inference or
automatic CLI update occurs.
