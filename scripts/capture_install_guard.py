#!/usr/bin/env python3
"""Keep recording/export leases exclusive with package install and rollback."""
from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def main() -> int:
    home = Path(os.environ.get("MYNOTES_CAPTURE_HOME", str(Path.home() / "Library/Application Support/MyNotes/Capture")))
    home.mkdir(parents=True, exist_ok=True)
    with (home / "update.lock").open("a+") as lease:
        try:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Capture is recording or exporting. Retry the installation after audio is saved.", file=sys.stderr)
            return 2
        active = home / "active.json"
        plist = Path.home() / "Library/LaunchAgents/com.gabrizanni.mynotes-capture.plist"
        restart = active.is_file() and plist.is_file()
        domain = f"gui/{os.getuid()}"
        if restart:
            workspace = Path(json.loads(active.read_text())["workspace"])
            runtime_path = workspace / "state/capture/runtime.json"
            runtime = json.loads(runtime_path.read_text()) if runtime_path.exists() else {}
            if runtime.get("pid") and (runtime.get("recording") or runtime.get("exporting")):
                try:
                    os.kill(runtime["pid"], 0)
                except PermissionError:
                    print("Capture recording state cannot be checked. Retry after confirming recording/export has stopped.", file=sys.stderr)
                    return 2
                except OSError:
                    pass
                else:
                    print("Capture is busy. Retry after the recording/export completes.", file=sys.stderr)
                    return 2
        environment = dict(os.environ, MYNOTES_INSTALL_CAPTURE_GUARD="1")
        child = None
        stop_attempted = False
        previous_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}

        def interrupted(signum, _frame):
            # Forward targeted termination too: cleanup must wait until the
            # installer stops writing before restarting the recorder.
            if child is not None and child.poll() is None:
                try:
                    os.killpg(child.pid, signum)
                except PermissionError:
                    child.send_signal(signum)
                except ProcessLookupError:
                    pass
            if signum == signal.SIGINT:
                raise KeyboardInterrupt
            raise SystemExit(128 + signum)

        for signum in previous_handlers:
            signal.signal(signum, interrupted)
        try:
            if restart:
                # Mark before launchctl so an interruption during bootout also
                # attempts recovery. An already-running service needs no restart.
                stop_attempted = True
                subprocess.run(["launchctl", "bootout", f"{domain}/com.gabrizanni.mynotes-capture"], capture_output=True)
            with subprocess.Popen(sys.argv[1:], env=environment, start_new_session=True) as process:
                child = process
                try:
                    return process.wait()
                except BaseException:
                    # The setup can spawn uv/zsh children. Terminate its isolated
                    # process group before recovery so no detached child keeps
                    # modifying the installation while Capture starts again.
                    for signum in previous_handlers:
                        signal.signal(signum, signal.SIG_IGN)
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except PermissionError:
                            process.terminate()
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except PermissionError:
                                process.kill()
                            except ProcessLookupError:
                                pass
                            process.wait(timeout=5)
                    raise
        finally:
            # A second Ctrl-C must not interrupt the short recovery operation.
            for signum in previous_handlers:
                signal.signal(signum, signal.SIG_IGN)
            try:
                fcntl.flock(lease.fileno(), fcntl.LOCK_UN)
                if stop_attempted:
                    try:
                        restarted = subprocess.run(["launchctl", "bootstrap", domain, str(plist)], capture_output=True, timeout=15)
                        if restarted.returncode:
                            print("Capture restart was not confirmed. Open MyNotes Capture and check its status.", file=sys.stderr)
                    except (OSError, subprocess.TimeoutExpired):
                        print("Capture could not restart automatically. Open MyNotes Capture and check its status.", file=sys.stderr)
            finally:
                for signum, previous in previous_handlers.items():
                    signal.signal(signum, previous)


if __name__ == "__main__":
    raise SystemExit(main())
