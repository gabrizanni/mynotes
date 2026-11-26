"""Run unchanged CLI commands inside one process, with each call's own output.

`mynotes serve` answers requests from several threads. Thread-local proxies
stand in for sys.stdout, sys.stderr and sys.stdin, so a command run here prints
exactly the text it prints in its own process, and reads an empty stdin, as it
would without a terminal (an interactive prompt ends with the existing EOFError
path). Threads that are not running a command keep the streams they had.
"""
from __future__ import annotations

import io
import sys
import threading
import traceback
from collections.abc import Sequence
from dataclasses import dataclass

_local = threading.local()
_install_lock = threading.Lock()
_first_run_lock = threading.Lock()


@dataclass(frozen=True)
class Invocation:
    exit_code: int
    stdout: str
    stderr: str


class _Output(io.TextIOBase):
    def __init__(self, name: str, fallback) -> None:
        self._name = name
        self._fallback = fallback

    def _captured(self):
        return getattr(_local, self._name, None)

    def _target(self):
        return self._captured() or self._fallback

    def write(self, text: str) -> int:
        return self._target().write(text)

    def flush(self) -> None:
        self._target().flush()

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False if self._captured() is not None else self._fallback.isatty()

    def fileno(self) -> int:
        if self._captured() is not None:
            raise io.UnsupportedOperation("a captured command stream has no file descriptor")
        return self._fallback.fileno()

    @property
    def encoding(self) -> str:
        return "utf-8" if self._captured() is not None else getattr(self._fallback, "encoding", "utf-8")

    @property
    def errors(self) -> str:
        return "strict" if self._captured() is not None else getattr(self._fallback, "errors", "strict")


class _EmptyInput(io.TextIOBase):
    """Stdin for commands: always at end of file, never a terminal."""

    def readable(self) -> bool:
        return True

    def read(self, size: int | None = -1) -> str:
        return ""

    def readline(self, size: int | None = -1) -> str:
        return ""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise io.UnsupportedOperation("commands run by mynotes serve have no stdin")

    @property
    def encoding(self) -> str:
        return "utf-8"


def install() -> None:
    """Replace the process streams with proxies once; idempotent."""
    with _install_lock:
        if not isinstance(sys.stdout, _Output):
            sys.stdout = _Output("stdout", sys.stdout)
        if not isinstance(sys.stderr, _Output):
            sys.stderr = _Output("stderr", sys.stderr)
        if not isinstance(sys.stdin, _EmptyInput):
            sys.stdin = _EmptyInput()


def installed() -> bool:
    return isinstance(sys.stdout, _Output) and isinstance(sys.stderr, _Output)


def _exit_code(error: SystemExit, stderr: io.StringIO) -> int:
    if error.code is None:
        return 0
    if isinstance(error.code, int):
        return error.code
    print(error.code, file=stderr)
    return 1


def invoke(argv: Sequence[str], *, serialize: bool = False) -> Invocation:
    """Run `mynotes <argv>` in this thread and return its exit code and output.

    `serialize` runs the call alone, for the first commands on a workspace that
    is not initialized yet (two threads must not create it at the same time).
    """
    install()
    from .cli import main
    stdout, stderr = io.StringIO(), io.StringIO()
    _local.stdout, _local.stderr = stdout, stderr
    try:
        if serialize:
            with _first_run_lock:
                code = _run(main, argv, stderr)
        else:
            code = _run(main, argv, stderr)
    finally:
        _local.stdout = _local.stderr = None
    return Invocation(code, stdout.getvalue(), stderr.getvalue())


def _run(main, argv: Sequence[str], stderr: io.StringIO) -> int:
    try:
        return int(main(list(argv)) or 0)
    except SystemExit as error:  # argparse errors, --help and --version
        return _exit_code(error, stderr)
    except Exception:
        # The same report and status as an uncaught error in a separate process.
        traceback.print_exc(file=stderr)
        return 1
