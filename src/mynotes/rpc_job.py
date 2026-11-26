"""Run one CLI command as a `mynotes serve` job: `python -m mynotes.rpc_job --record PATH -- ARGS`.

The job holds `<record>.lock` for its whole life, so a restarted server can
tell a live job from a finished or killed one without trusting a reused PID.
Its PID goes to `<record>.pid` and its exit code to `<record>.exit`, written
last. Cancellation reuses the commands' existing KeyboardInterrupt paths:

- POSIX: SIGINT, then SIGTERM, both become KeyboardInterrupt in the main
  thread (code that installs its own SIGTERM handler while an agent or a
  transcription engine runs keeps that handler);
- Windows: the server creates `<record>.cancel`; a watcher thread then
  interrupts the main thread, because the job has no console for CTRL_BREAK.
"""
from __future__ import annotations

import _thread
import os
import signal
import sys
import threading
import time
from pathlib import Path


def _interrupt(signum, frame):
    # A second signal terminates the job if the interrupt path hangs.
    signal.signal(signum, signal.SIG_DFL)
    raise KeyboardInterrupt


def _write_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _watch_cancel_file(path: Path) -> None:
    while not path.exists():
        time.sleep(0.5)
    _thread.interrupt_main()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < 3 or arguments[0] != "--record" or arguments[2] != "--":
        print("usage: python -m mynotes.rpc_job --record PATH -- ARGS", file=sys.stderr)
        return 2
    record, command = Path(arguments[1]), arguments[3:]
    from . import file_lock
    lease = record.with_name(record.name + ".lock").open("a+")
    file_lock.flock(lease, file_lock.LOCK_EX)
    _write_atomically(record.with_name(record.name + ".pid"), str(os.getpid()))
    signal.signal(signal.SIGINT, signal.default_int_handler)
    if os.name == "nt":
        threading.Thread(target=_watch_cancel_file, args=(record.with_name(record.name + ".cancel"),), daemon=True).start()
    else:
        signal.signal(signal.SIGTERM, _interrupt)
    code = 1
    try:
        from .cli import main as cli_main
        code = int(cli_main(command) or 0)
    except KeyboardInterrupt:
        print('{"stage": "INTERRUPTED", "job_cancelled": true}', file=sys.stderr, flush=True)
        code = 130
    except SystemExit as error:
        if isinstance(error.code, int) or error.code is None:
            code = error.code or 0
        else:
            print(error.code, file=sys.stderr)
            code = 1
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        finally:
            _write_atomically(record.with_name(record.name + ".exit"), str(code))
            lease.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
