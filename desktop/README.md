# MyNotes Desktop

A calm, button-driven window for MyNotes: record lectures, transcribe them on this computer and
grow one Word handout per course, without a terminal. Tauri 2 shell, React 19 + TypeScript
(strict) UI, plain CSS with design tokens. English and Italian.

> Status: foundation. The shell, design system, component library, RPC client, store, i18n and
> the **Today** screen are done and run against a fixture mock. The other screens are
> placeholders, and the Rust supervisor that starts `mynotes serve --stdio` is a documented stub.
> The app identifier `io.github.gabrizanni.mynotes.desktop` is a placeholder awaiting the
> maintainer's approval.

Product and UX spec: [`docs/UX-SPEC.md`](docs/UX-SPEC.md).

## Run it

Requirements: Node 22.12 or newer, Rust stable (for `src-tauri`), and the Tauri Linux system
libraries (`webkit2gtk-4.1`, `libsoup3`, GTK 3) when building the shell on Linux.

```sh
npm ci
npm run dev            # Vite on http://localhost:1420 with the mock backend (fixtures)
npm run typecheck      # tsc, strict + noUncheckedIndexedAccess, app and tooling
npm run lint           # typescript-eslint, no-explicit-any is an error
npm test               # Vitest + Testing Library (jsdom)
npm run test:e2e       # Playwright, mocked end-to-end flows (headless Chromium)
npm run shots          # Playwright screenshots → tests/shots/<name>-<lang>-<theme>.png
(cd src-tauri && cargo check && cargo test)
```

Useful URL parameters in the browser: `?lang=it`, `?theme=dark`, `?scenario=recording`
(fixture scenario), `?simulate=0` (freeze mock timers). The component gallery lives at
`/__gallery`.

`npm run tauri dev` opens the real window; it needs the backend from the `serve` work
(ARCHITECTURE section A) before it shows live data.

## Architecture

```
desktop/
  src/
    design/        tokens.css (light + dark), fonts.css, base.css, theme.ts, cx.ts
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
`system.hello` version check, with unit tests. `locate.rs` finds Python
(`MYNOTES_DESKTOP_PYTHON`, then `uv run` in development, then the installed runtime).
`supervisor.rs` and `installer.rs` describe their remaining work as TODO lists. The webview
gets no opener, shell or filesystem permission; `open_path`/`reveal_path` validate paths in
Rust first (`paths.rs`).

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
- EB Garamond only at 24 px and up (page titles, hero); Figtree for all UI text; JetBrains Mono
  for timestamps and diagnostics.
- One primary button per screen (`variant="primary"`, or `onInverse` on the deep-blue hero).
  Buttons have radius 12; pills are for chips and the recording capsule only.
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
