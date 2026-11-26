# Backend performance

**In breve.** Misure su Linux (AMD Ryzen 5 5600H, Python 3.13.18,
26/11/2026), stesso workspace sintetico per `develop` e per questo ramo.
I comandi rapidi partono il 21-34% più in fretta perché non caricano più le
librerie Word e PDF, e l'heartbeat non avvia più un processo inutile quando la
coda è vuota. Con `mynotes serve` la dashboard costa 4 ms invece di un
processo da circa 190 ms, la finestra aperta non avvia alcun processo e
consuma lo 0,47-0,57% di un core secondo il carico della macchina, contro il
43,6% del vecchio ciclo di polling.
Le cifre non misurate sono indicate come tali.

## Method

`scripts/bench_backend.py` builds a synthetic workspace through the measured
checkout's own CLI: one course, 12 scheduled lessons, 12 two-second WAV files
and a queue database, with an isolated HOME and a PATH without agents. It ran
on `develop` (9223673) and then on this branch, back to back, with the
defaults: 15 measured cold starts per command after one warm-up, the native
apps' poll (capture dashboard, queue status, queue library, heartbeat) every
5 s for 60 s, 200 sequential calls per RPC method, 60 s with the window open
and idle, and 6 timed file changes.

- Machine: AMD Ryzen 5 5600H (12 threads), Linux 7.2.15, Python 3.13.18,
  2026-11-26. Another worktree's lint was running during these runs (load
  average about 2.5), so absolute times are higher than on a quiet machine.
- CPU is the children's user and system time from `wait4`. Detached queue
  runners are counted through a Linux subreaper; macOS and Windows numbers
  are not measured.
- Server CPU comes from `system.stats` (the server's own and its children's
  time).

## Results

| Metric | Before (develop 9223673) | After (feat/backend-serve-performance) | Change |
| --- | ---: | ---: | ---: |
| `import mynotes.cli` median (ms) | 110 | 109 | -1% |
| `--version` cold start median (ms) | 209 | 165 | -21% |
| `--version` cold start p95 (ms) | 218 | 276 | +27% |
| `--version` child CPU median (ms) | 207 | 163 | -21% |
| `capture dashboard` cold start median (ms) | 193 | 139 | -28% |
| `capture dashboard` cold start p95 (ms) | 196 | 145 | -26% |
| `capture dashboard` child CPU median (ms) | 191 | 138 | -28% |
| `doctor` cold start median (ms) | 189 | 132 | -30% |
| `doctor` cold start p95 (ms) | 194 | 150 | -22% |
| `doctor` child CPU median (ms) | 187 | 130 | -30% |
| `heartbeat` cold start median (ms) | 196 | 130 | -34% |
| `heartbeat` cold start p95 (ms) | 214 | 274 | +28% |
| `heartbeat` child CPU median (ms) | 194 | 128 | -34% |
| `queue library` cold start median (ms) | 241 | 180 | -25% |
| `queue library` cold start p95 (ms) | 258 | 191 | -26% |
| `queue library` child CPU median (ms) | 238 | 177 | -26% |
| `queue status` cold start median (ms) | 191 | 137 | -28% |
| `queue status` cold start p95 (ms) | 204 | 143 | -30% |
| `queue status` child CPU median (ms) | 189 | 135 | -29% |
| Old poll cycle CPU (% of one core) | 43.6 | 23.3 | -46% |
| Old poll cycle spawns per minute | 60.0 | 48.0 | -20% |
| Server `system.hello` from spawn (ms) | not measured | 304 | n/a |
| RPC `capture.dashboard` median (ms) | not measured | 4.00 | n/a |
| RPC `capture.dashboard` p95 (ms) | not measured | 4.81 | n/a |
| RPC `queue.status` p95 (ms) | not measured | 3.89 | n/a |
| RPC cached capture snapshot p95 (ms) | not measured | 0.91 | n/a |
| Server idle CPU, UI open (% of one core) | not measured | 0.57 | n/a |
| Server idle spawns per minute | not measured | 0.00 | n/a |
| File change to notification median (ms) | not measured | 855 | n/a |
| File change to notification max (ms) | not measured | 1019 | n/a |
| Heavy modules loaded by `import mynotes.cli` | docx, lxml, pypdfium2 | none | |

The `import mynotes.cli` row is not reliable: three complete runs of the
harness gave 115/104, 213/46.5 and 110/109 ms (develop/branch), and in two of
them a value exceeds the same run's full `--version` start. A dedicated run
of the same probe (15 imports after a warm-up, twice per checkout,
alternating) measured a median of 101 and 99 ms on `develop` against 44 and
44 ms on this branch, about 56% faster. The cold-start rows agree across runs.
The `--version` and `heartbeat` p95 rows are single slow outliers under that
load; their medians and the other p95 rows improved.

Against the targets set before the work:

| Target | Measured | Met |
| --- | --- | --- |
| `import mynotes.cli` at least 40% faster | about 56% (dedicated probe) | yes |
| Dashboard over RPC p95 ≤ 15 ms computed, ≤ 2 ms cached | 4.81 ms computed, 0.91 ms cached | yes |
| Idle CPU with the window open ≤ 0.5% of one core | 0.57% in this run; 0.47% at load 1.3 | on a quiet machine only |
| Process spawns while idle: 0 per minute | 0 (the old poll: 60 on develop, 48 now) | yes |
| File change to notification ≤ 1.2 s | median 855 ms, max 1019 ms | yes |

The old poll on `develop` cost 43.6% of one core, not the 5-10% estimated
beforehand: four interpreter starts every 5 s, plus a queue runner that the
heartbeat started and that exited at once (12 of the 60 spawns per minute).
On this branch the same poll costs 23.3% with 48 spawns per minute: the
heartbeat no longer starts a runner while the queue is idle (0 detached
spawns per call, against 1.0). The server replaces the poll altogether.

Idle cost of the server, measured on the synthetic workspace: the change
checks, the lease renewal every 5 s, and recomputations of capture and queue
every 10 s (about 1.4 and 0.9 ms each) and of the library every 30 s (about
47 ms each, more than a quarter of the total). A separate probe measured 0.55% both in the
first minute after subscribing and over the next two quiet minutes, so the
2 s back-off saves little under load. The remaining lever, a longer library
refresh, would show a recording's change to "stable" later, so it was not
taken: 0.5-0.6% of one core is about 1/75 of the old poll. Earlier versions
measured 2.17% (topics recomputed on every lease renewal) and 0.67% (paths
rebuilt on every check); both were fixed before these numbers.

Not measured: macOS and Windows, a real recorder, workspaces with many
recordings, and job cancellation on Windows.

## What changed, one step at a time

1. **Deferred Word and PDF libraries.** `import mynotes.cli` loaded
   python-docx, lxml and pypdfium2 for every command. `cli.py` now imports
   extraction, sourcepack and publication only in the commands that use them,
   and `workflow.py` imports python-docx inside its two Word helpers. The
   names stay importable from `mynotes.cli`, as the same objects.
   `publication.py` and `extraction.py` are deliberately not edited: their own
   source hashes are part of `word_renderer_hash()` and of the extraction cache
   identity, so editing them would make every user rebuild Word files and
   re-extract materials after updating.
2. **One argument parser per process.** `main()` builds the parser once;
   parsing never changes it. A CLI process is unaffected, and the server does
   not pay the build (about 50 ms, measured with two consecutive
   `build_parser()` calls) on every in-process command.
3. **`mynotes serve --stdio`.** One process answers the app. Read-only
   commands run in-process, so a dashboard refresh costs a function call
   instead of an interpreter start. Notifications replace the four-command
   poll: files are checked with `stat` once a second and a topic is
   recomputed only when its files change (see [SERVE.md](SERVE.md)).
4. **No redundant queue runners.** The heartbeat started a runner process on
   every call, which exited at once when nothing was queued or when a runner
   already held its lock. `launch_runner()` now starts one only when
   `runner_needed()` (queued, running or cancelling jobs, or a recorded memory
   hold to re-check) and no runner holds `runner.lock` (`runner_active()`).
   While work exists nothing changes; the existing macOS and Windows apps
   benefit too.
5. **Cheaper change checks.** The watcher keeps each topic's path strings
   (rebuilding pathlib objects cost more than the `stat` calls), keys the
   queue database by SQLite's `data_version` and the lease by its session,
   and checks every 2 s instead of every second after 30 quiet seconds.

## Not changed, measured

- Building the parser costs about 50 ms of each CLI start; the first build
  also imports `capture.py`, whose `urllib.request` import is kept because
  tests patch `capture.urllib.request`. A lazy subparser design could remove
  most of it, at the price of a larger change to `cli.py`.

## Reproduce

```sh
uv sync --locked
uv run python scripts/bench_backend.py --repo ../develop-checkout --label develop --output before.json
uv run python scripts/bench_backend.py --label branch --output after.json
uv run python scripts/bench_backend.py --compare before.json after.json
uv run python scripts/cli_snapshot.py --repo ../develop-checkout --output develop.txt
uv run python scripts/cli_snapshot.py --output branch.txt
diff -u develop.txt branch.txt
```

The benchmark builds its synthetic workspace through the measured checkout's
own CLI, with an isolated HOME and a PATH without agents. No agent, network,
speech engine or Word renderer runs.
