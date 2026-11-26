# Backend performance

**In breve.** Misure su Linux (AMD Ryzen 5 5600H, Python 3.13.18,
26/11/2026), stesso workspace sintetico per `develop` e per questo ramo.
I comandi rapidi partono il 24-36% più in fretta perché non caricano più le
librerie Word e PDF. Con `mynotes serve` la dashboard costa 1,6 ms invece di
un processo da circa 190 ms, la finestra aperta non avvia alcun processo e
consuma lo 0,67% di un core, contro il 36,6% del vecchio ciclo di polling.
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
  2026-11-26. Other interactive programs were running (load average about 2).
- CPU is the children's user and system time from `wait4`. Detached queue
  runners are counted through a Linux subreaper; macOS and Windows numbers
  are not measured.
- Server CPU comes from `system.stats` (the server's own and its children's
  time).

## Results

| Metric | Before (develop 9223673) | After (feat/backend-serve-performance) | Change |
| --- | ---: | ---: | ---: |
| `import mynotes.cli` median (ms) | 213 | 46.5 | -78% |
| `--version` cold start median (ms) | 189 | 126 | -33% |
| `--version` cold start p95 (ms) | 193 | 142 | -26% |
| `--version` child CPU median (ms) | 187 | 125 | -33% |
| `capture dashboard` cold start median (ms) | 191 | 123 | -35% |
| `capture dashboard` cold start p95 (ms) | 194 | 132 | -32% |
| `capture dashboard` child CPU median (ms) | 189 | 122 | -35% |
| `doctor` cold start median (ms) | 193 | 148 | -24% |
| `doctor` cold start p95 (ms) | 204 | 170 | -17% |
| `doctor` child CPU median (ms) | 191 | 146 | -24% |
| `heartbeat` cold start median (ms) | 197 | 125 | -36% |
| `heartbeat` cold start p95 (ms) | 206 | 130 | -37% |
| `heartbeat` child CPU median (ms) | 194 | 124 | -36% |
| `queue library` cold start median (ms) | 239 | 173 | -28% |
| `queue library` cold start p95 (ms) | 245 | 176 | -28% |
| `queue library` child CPU median (ms) | 237 | 171 | -28% |
| `queue status` cold start median (ms) | 191 | 123 | -35% |
| `queue status` cold start p95 (ms) | 219 | 127 | -42% |
| `queue status` child CPU median (ms) | 189 | 122 | -35% |
| Old poll cycle CPU (% of one core) | 36.6 | 14.4 | -61% |
| Old poll cycle spawns per minute | 60.0 | 60.0 | +0% |
| Server `system.hello` from spawn (ms) | not measured | 115 | n/a |
| RPC `capture.dashboard` median (ms) | not measured | 1.64 | n/a |
| RPC `capture.dashboard` p95 (ms) | not measured | 1.79 | n/a |
| RPC `queue.status` p95 (ms) | not measured | 1.34 | n/a |
| RPC cached capture snapshot p95 (ms) | not measured | 0.49 | n/a |
| Server idle CPU, UI open (% of one core) | not measured | 0.67 | n/a |
| Server idle spawns per minute | not measured | 0.00 | n/a |
| File change to notification median (ms) | not measured | 828 | n/a |
| File change to notification max (ms) | not measured | 842 | n/a |
| Heavy modules loaded by `import mynotes.cli` | docx, lxml, pypdfium2 | none | |

The `import mynotes.cli` row is not reliable: in an earlier complete run of
the same harness it read 115 ms before and 104 ms after, and in both runs one
"before" value exceeds the same run's full `--version` start. A dedicated run
of the same probe (15 imports after a warm-up, twice per checkout,
alternating) measured a median of 101 and 99 ms on `develop` against 44 and
44 ms on this branch, about 56% faster. The cold-start rows agree across both
complete runs.

Against the targets set before the work:

| Target | Measured | Met |
| --- | --- | --- |
| `import mynotes.cli` at least 40% faster | about 56% (dedicated probe) | yes |
| Dashboard over RPC p95 ≤ 15 ms computed, ≤ 2 ms cached | 1.79 ms computed, 0.49 ms cached | yes |
| Idle CPU with the window open ≤ 0.5% of one core | 0.67% | no |
| Process spawns while idle: 0 per minute | 0 (the old poll: 60) | yes |
| File change to notification ≤ 1.2 s | median 828 ms, max 842 ms | yes |

The old poll on `develop` cost 36.6% of one core, not the 5-10% estimated
beforehand: four interpreter starts every 5 s, plus a queue runner that the
heartbeat started and that exited at once (12 of the 60 spawns per minute).
On this branch the same poll costs 14.4%, and the server replaces it.

The remaining idle cost of the server (0.40 CPU-seconds per minute) is the
per-second file check, the lease renewal every 5 s and the scheduled
recomputations (capture and queue every 10 s, library every 30 s). It was not
broken down further. A first version recomputed the queue and library topics
on every lease renewal and measured 2.17%; that was fixed before these
numbers.

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
   already held its lock. `transcription_queue.runner_active()` now probes
   that lock first, which also benefits the existing heartbeat of the macOS and
   Windows apps when a runner is alive; the server's lease starts a runner only
   when the queue has work.

## Not changed, measured

- Building the parser costs about 50 ms of each CLI start; the first build
  also imports `capture.py`, whose `urllib.request` import is kept because
  tests patch `capture.urllib.request`. A lazy subparser design could remove
  most of it, at the price of a larger change to `cli.py`.
- The heartbeat of the native apps still starts a runner when the queue is
  idle and no runner is alive (only an alive runner is now detected); the
  server avoids it by checking the queue first.

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
