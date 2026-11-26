# MyNotes Desktop

A calm, button-driven window for MyNotes: record lectures, transcribe them on this computer and
grow one Word handout per course, without a terminal. Tauri 2 shell, React 19 + TypeScript
(strict) UI, plain CSS with design tokens. English and Italian.

> Status: foundation. The shell, design system, component library, RPC client, store, i18n and
> the **Today** screen are done and run against a fixture mock; the other screens are
> placeholders. The Rust shell supervises the real `mynotes serve --stdio` (proved on Linux by
> `--self-check` and the contract tests). The app identifier `io.github.gabrizanni.mynotes.desktop`
> is a placeholder awaiting the maintainer's approval.

Product and UX spec: [`docs/UX-SPEC.md`](docs/UX-SPEC.md).

## Run it

Requirements: Node 22.12 or newer, Rust stable (for `src-tauri`), and the Tauri Linux system
libraries (`webkit2gtk-4.1`, `libsoup3`, GTK 3, `libayatana-appindicator3` for the tray) when
building the shell on Linux. The contract tests and the self-check also need `uv` and a checkout
that contains `mynotes serve` (this repository once the serve work is merged).

```sh
npm ci
npm run dev            # Vite on http://localhost:1420 with the mock backend (fixtures)
npm run typecheck      # tsc, strict + noUncheckedIndexedAccess, app and tooling
npm run lint           # typescript-eslint, no-explicit-any is an error
npm test               # Vitest + Testing Library (jsdom)
npm run test:contract  # the app's zod schemas against the real `mynotes serve` (see below)
npm run test:e2e       # Playwright, mocked end-to-end flows (headless Chromium)
npm run shots          # Playwright screenshots → tests/shots/<name>-<lang>-<theme>.png
(cd src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test)
```

Useful URL parameters in the browser: `?lang=it`, `?theme=dark`, `?scenario=recording`
(fixture scenario), `?simulate=0` (freeze mock timers). The component gallery lives at
`/__gallery`.

### Which backend the app starts

The shell starts `mynotes serve --stdio` itself, with the `PATH` of your login shell (so agent
CLIs and `uv` are found when the app is launched from a dock or launcher). It looks, in order:

| Variable | Effect |
| --- | --- |
| `MYNOTES_DESKTOP_PYTHON` | this interpreter: `<python> -I -X utf8 -m mynotes serve --stdio` |
| `MYNOTES_DESKTOP_REPO` | a source checkout: `uv run --project <repo> mynotes serve --stdio` |
| (none) | the installed runtime: `~/.local/share/mynotes/app/current/venv/bin/python` on Linux, `~/Library/Application Support/MyNotes/…` on macOS, `<install dir>\runtime\python.exe` on Windows; `MYNOTES_HOME` overrides the runtime home |

A variable that points nowhere is an error, never a silent fallback. The backend resolves its
workspace as the CLI does (`MYNOTES_WORKSPACE`, then the default). Without a runtime the supervisor
reports `failed` with code `NOT_INSTALLED` (`backend://status`, `src/rpc/native.ts`): the cue for
the onboarding to run setup (`src/rpc/setup.ts`).

`npm run tauri dev` opens the real window against `MYNOTES_DESKTOP_REPO=/path/to/checkout`.

### Headless self-check

`mynotes-desktop --self-check` (or `MYNOTES_DESKTOP_SELF_CHECK=1`) opens no window and loads no
webview: it locates Python, starts the supervisor, waits for `system.hello`, calls
`capture.dashboard`, shuts down and checks that no backend process is left, then prints one JSON
line and exits 0 or 1.

```sh
(cd src-tauri && cargo build)
MYNOTES_DESKTOP_REPO=/path/to/checkout MYNOTES_WORKSPACE=/tmp/ws src-tauri/target/debug/mynotes-desktop --self-check
```

### Contract tests

`npm run test:contract` starts the real server from `MYNOTES_SERVE_REPO` (default: this
repository) on a temporary workspace with a private `HOME` and no agent CLIs on `PATH`, builds a
course, a schedule, a recordings folder and one finished job through the API, then calls every
method of `src/rpc/schema.ts` that needs no agent, recorder or network through the app's own
`RpcClient` (Node stdio transport, `tests/contract/stdioTransport.ts`). Every method of the table
is classified once: called with the params the app sends, `NOT_CALLED` with its reason, answered
by the shell, or `PENDING_METHODS` (modelled but not in SERVE.md yet; the suite fails the day the
server knows them). The `EXPECTED` table pins which answers the schemas accept; a `drift` entry
fails the suite once the schema is fixed, so the entry goes away with the fix.

The mock fixtures use the same wire shapes as the server (job records, watch snapshots, the
`{exitCode, data}` envelope). Features read the app's `Job` view, built from records and job
notifications in one place, `src/rpc/jobs.ts`.

`MYNOTES_CONTRACT_RECORD=1 npm run test:contract` writes every real answer, temporary paths
replaced by `/tmp/mynotes-contract`, to `tests/fixtures/rpc-real/`. They do not overwrite
`tests/fixtures/rpc/`: those fixtures carry the screens' scenarios (a lesson recording, a notes
run), which an empty fixture workspace cannot produce.

### Build the installers

```sh
npm run tauri build -- --bundles deb        # or appimage; Linux
npm run tauri build -- --bundles dmg        # macOS (APPLE_SIGNING_IDENTITY=- for an ad-hoc signature)
npm run tauri build -- --bundles msi,nsis   # Windows
```

CI (`.github/workflows/desktop.yml`) builds all three unsigned, with SHA-256 files, and runs a
real-app smoke test on Linux (Xvfb, `webkit2gtk-driver`, `tauri-driver`, `tests/wdio/smoke.ts`).

### Desktop integration

- **Single instance.** A second launch focuses the window; `--action record-dialog` is
  forwarded to it as the `app://action` event (`src/rpc/native.ts`).
- **Global shortcut.** `Ctrl+Alt+Shift+R` (`⌘⌥⇧R` on macOS) asks for the record dialog on
  macOS, Windows and X11. Wayland gives apps no global shortcuts: bind the action in the
  compositor instead, for example on Hyprland
  `bind = SUPER SHIFT, R, exec, mynotes-desktop --action record-dialog`.
- **Tray.** On by default on Linux and Windows (red dot and "Stop recording" while a lesson
  records), off on macOS where Capture owns the menu bar; `MYNOTES_DESKTOP_TRAY=1` or `0`
  overrides. Linux needs a StatusNotifierItem host (waybar's `tray` module on Hyprland).
- **Recording capsule.** A small always-on-top window (`capsule.html`) appears only while a
  lesson records or is saved.
- **Window size** is restored; Wayland does not allow restoring the position. If the window
  stays blank on some Wayland GPUs, start with `WEBKIT_DISABLE_DMABUF_RENDERER=1`.
- A system notification says "Notes ready" when a notes job completes while the window is not
  focused.

## Architecture

```
desktop/
  src/
    design/        tokens.css (light + dark), fonts.css, base.css, theme.ts, cx.ts,
                   contrast.ts + contrastPairs.ts (WCAG check, see contrast.test.ts)
    components/    one folder per component: Name.tsx + Name.module.css + index.ts
    rpc/           schema.ts (zod, per method), client.ts, transports/{tauri,mock}.ts
    state/         store.ts, slices/<topic>.ts, routes.ts, hooks.tsx, sync.ts
    i18n/          en/<feature>.ts (source), it/<feature>.ts (typed against en)
    features/      one folder per screen: today/ (done), calendar/, courses/, library/,
                   activity/, lesson/, settings/, onboarding/, assistant/, capture/, gallery/
    app/           Shell (sidebar · page · assistant dock), Routes, shortcuts, boot
  src-tauri/       Rust shell: backend/{protocol,locate,supervisor}.rs, commands.rs,
                   paths.rs, tray.rs, installer.rs
  tests/
    fixtures/rpc/  mock answers per RPC method, plus scenario patches
    unit/          shared test helpers (unit tests sit next to their code)
    e2e/           Playwright flows against the mock
    screenshots/   screenshot scenes (output goes to tests/shots/, git-ignored)
```

**Data flow.** `rpc/client.ts` checks every call's params and result with zod (unknown fields
pass through, unknown statuses never crash). The transport is Tauri inside the app
(`invoke("rpc_call")`, which Rust checks against an allowlist) and the fixture mock everywhere
else. `state/sync.ts` subscribes once (`watch.subscribe`) and then applies the server's
notifications (`capture.changed`, `job.progress`, …) to the store. Components read the store
with `useAppState(selector)` and act through `useActions()`.

**Rust.** `backend/protocol.rs` implements JSON-RPC 2.0 line framing (one message per line,
16 MiB cap, CRLF tolerated, UTF-8 split across reads, oversize lines skipped) and the
`system.hello` version check. `backend/methods.rs` is the protocol-1 method table with lanes,
tested against `mynotes serve --list-methods --json`. `backend/locate.rs` finds Python and the
login-shell `PATH`. `backend/supervisor.rs` runs the child in its own process group (Unix) or
Job Object (Windows), pings it every 15 s, restarts it with backoff from 0.5 s to 30 s, stops
after 5 crashes in 2 minutes with the last stderr lines, fails pending calls with
`BACKEND_RESTARTED`, and on exit closes stdin before signalling the group. `events.rs` turns
notifications into Tauri events, tray and capsule state, course folders and the notes-ready
notification. `installer.rs` runs `setup_runtime.py` and streams its progress. The webview gets
no opener, shell or filesystem permission; `open_path`/`reveal_path` validate paths in Rust
first (`paths.rs`), and `capabilities/` grants each window only its commands.

## Add a screen

1. **Folder.** Build it in `src/features/<feature>/`; export the page from `index.tsx`. The
   placeholder there shows where it plugs in. Keep a pure view-model in `model.ts` (like
   `features/today/model.ts`) and test it.
2. **Route.** If the screen needs a new URL, add a variant to `Route` in
   `src/state/routes.ts` (parse + format), then a case in `src/app/Routes.tsx`. The switch is
   exhaustive, so `tsc` lists what is missing.
3. **Strings.** Add them to `src/i18n/en/<feature>.ts`, then the same keys to
   `src/i18n/it/<feature>.ts`. A missing Italian key fails `npm run typecheck`. Parameterized
   strings are functions; plurals use `plural(lang, n, { one, other })`.
4. **Data.** Read with `useAppState`; if the screen needs a new backend call, add it to
   `methods` in `src/rpc/schema.ts`, write `tests/fixtures/rpc/<method>.json`, and put state
   and actions in the matching slice (`src/state/slices/<topic>.ts`). A new topic gets its own
   slice file, registered in `state/types.ts` and `state/app.ts`.
5. **States.** Scenario patches (`<method>@<scenario>.json`, JSON merge patch) drive the other
   states. Add scenes to `tests/screenshots/scenes.ts` and run `npm run shots`.

## Conventions

- Components and tokens only: no raw colors, sizes or shadows outside `src/design/tokens.css`.
  Use `--space-*`, `--radius-*`, `--text-*`, `--shadow-e*`, `--duration-*`.
- Palette, roles and measured contrast: [`docs/DESIGN-TOKENS.md`](docs/DESIGN-TOKENS.md). A new
  color on a new surface gets a pair in `src/design/contrastPairs.ts`; `npm test` fails below
  WCAG AA.
- EB Garamond only at 24 px and up (page titles, hero), in ink; Figtree for all UI text. Times
  use Figtree tabular figures (`--text-time`, `.type-time`); JetBrains Mono is for diagnostics
  only.
- One lavender primary button per screen (`variant="primary"`). Buttons have radius 12; pills
  are for chips and the recording capsule only. No dark blocks: `Card variant="inverse"` is the
  light feature surface. Links use `--color-link`; "on" controls use `--color-control-strong`.
- Coral (`--color-accent-coral`) is ornament only: never text, never the only state cue.
- Status reaches the UI through `StatusChip` (icon + label) and its map in
  `components/StatusChip/statusMap.ts`; backend codes never appear in the UI.
- Progress is real or absent: `ProgressBar` only for measured bytes or items, `StageStepper`
  with elapsed time otherwise. No invented percentages, no level bars without a level feed.
- Every interactive element is a `<button>` or `<a>` with an accessible name; focus uses the
  global ring from `base.css`; motion respects `prefers-reduced-motion` through the tokens.
- TypeScript strict, `noUncheckedIndexedAccess`, no `any`; `try/catch` around RPC calls in
  actions; no `console.log`.
- Screenshots are named `<name>-<lang>-<theme>.png` (for example `today-recording-it-dark.png`).

## Public tree

`desktop/` is audited by `uv run python scripts/check_public_tree.py --desktop-source`: only the
listed source suffixes are allowed, fonts and icons are pinned by SHA-256, `node_modules`,
`target` and `dist` are skipped, and `package-lock.json` integrity hashes are stripped before
the private-word scan.
