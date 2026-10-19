#!/usr/bin/env python3
"""Resumable, verified setup, runnable with only a private stdlib Python."""
from __future__ import annotations

import fcntl
import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


class SetupError(RuntimeError):
    pass


class SlowMirrorError(SetupError):
    pass


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(data, output, indent=2)
            output.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            result.update(block)
    return result.hexdigest()


def safe_relative(root: Path, value: str) -> Path:
    relative = Path(value)
    path = (root / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(root.resolve()) or relative == Path("."):
        raise SetupError(f"Unsafe component path: {value}")
    return path


def validate_file_spec(spec: dict) -> None:
    if not isinstance(spec.get("url"), str) or not spec["url"].startswith("https://"):
        raise SetupError("Components require HTTPS URLs")
    if not re.fullmatch(r"[0-9a-f]{64}", spec.get("sha256", "")):
        raise SetupError("Components require a pinned SHA-256 checksum")
    mirrors = spec.get("mirrors", [])
    if not isinstance(mirrors, list) or len(mirrors) > 3 or any(not isinstance(url, str) or not url.startswith("https://") for url in mirrors):
        raise SetupError("Component mirrors require at most three HTTPS URLs")
    if type(spec.get("size_bytes")) is not int or spec["size_bytes"] <= 0:
        raise SetupError("Components require a positive download size")


def download(spec: dict, destination: Path, progress=None) -> Path:
    """Resume bytes only when the server proves the requested range is exact."""
    validate_file_spec(spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size == spec["size_bytes"] and digest(destination) == spec["sha256"]:
        return destination
    partial = destination.with_name(destination.name + ".part")
    identity = partial.with_name(partial.name + ".json")
    expected = {key: spec[key] for key in ("url", "sha256", "size_bytes")}
    try:
        same = json.loads(identity.read_text()) == expected
    except (OSError, ValueError):
        same = False
    if not same or (partial.exists() and partial.stat().st_size > spec["size_bytes"]):
        partial.unlink(missing_ok=True)
    atomic_json(identity, expected)
    offset = partial.stat().st_size if partial.exists() else 0
    if offset == spec["size_bytes"]:
        if digest(partial) == spec["sha256"]:
            os.replace(partial, destination)
            identity.unlink(missing_ok=True)
            return destination
        partial.unlink()
        offset = 0
    urls = list(dict.fromkeys([*spec.get("mirrors", []), spec["url"]]))
    for index, url in enumerate(urls):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "MyNotes-Setup/1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if not response.geturl().startswith("https://"):
                    raise SetupError("Component download redirected outside HTTPS")
                status = response.status
                if offset and status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-") or not content_range.endswith(f"/{spec['size_bytes']}"):
                        raise SetupError("Download resume returned an unexpected byte range")
                elif status == 200:
                    offset = 0
                else:
                    raise SetupError(f"Unexpected download response: {status}")
                available = shutil.disk_usage(destination.parent).free
                if available < spec["size_bytes"] - offset + 256 * 1024 * 1024:
                    raise SetupError("Not enough free space; free disk space and retry setup")
                began, received = time.monotonic(), 0
                read = getattr(response, "read1", response.read)
                with partial.open("ab" if offset else "wb") as output:
                    while block := read(64 * 1024):
                        if output.tell() + len(block) > spec["size_bytes"]:
                            raise SetupError("Download is larger than the pinned artifact")
                        output.write(block)
                        received += len(block)
                        if progress:
                            progress(output.tell(), spec["size_bytes"])
                        elapsed = time.monotonic() - began
                        if index + 1 < len(urls) and output.tell() < spec["size_bytes"] and elapsed >= 15 and received / elapsed < 128 * 1024:
                            raise SlowMirrorError("Download mirror is slow; resume from the next pinned mirror")
            break
        except (OSError, urllib.error.URLError, http.client.HTTPException, SlowMirrorError):
            if index + 1 == len(urls):
                raise
            # A different server must prove its exact Range; final SHA is mandatory.
            continue
    if partial.stat().st_size != spec["size_bytes"]:
        raise SetupError("Download interrupted; retry to resume")
    if digest(partial) != spec["sha256"]:
        partial.unlink(missing_ok=True)
        raise SetupError("Component checksum failed; retry to download a clean copy")
    os.replace(partial, destination)
    identity.unlink(missing_ok=True)
    return destination


def component_download(spec: dict, destination: Path, emit, component: str, label: str) -> Path:
    started_at = time.time()
    last_emit = float("-inf")
    cached = destination.is_file() and destination.stat().st_size == spec["size_bytes"]
    emit("components", f"Checking {label}" if cached else f"Connecting to download {label}", 0, spec["size_bytes"],
         component=component, phase="verify" if cached else "download", downloaded_bytes=0,
         total_bytes=spec["size_bytes"], started_at=started_at, last_data_at=None)
    def update(complete: int, total: int):
        nonlocal last_emit
        now = time.monotonic()
        if now - last_emit < 0.5 and complete != total:
            return
        last_emit = now
        emit("components", f"Downloading {label}: {complete // 1048576}/{total // 1048576} MiB", complete, total,
             component=component, phase="download", downloaded_bytes=complete, total_bytes=total,
             started_at=started_at, last_data_at=time.time())
    result = download(spec, destination, update)
    emit("components", f"Verified {label}", spec["size_bytes"], spec["size_bytes"], component=component, phase="verify")
    return result


def install_dmg(archive: Path, target: Path, app_name: str) -> Path:
    """Install a verified application privately, always unmounting its image."""
    if Path(app_name).name != app_name or not app_name.endswith(".app"):
        raise SetupError("Invalid component application name")
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mynotes-mount-") as mount:
        mounted = False
        try:
            subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", mount, str(archive)], check=True, capture_output=True, text=True)
            mounted = True
            app = Path(mount) / app_name
            if not app.is_dir():
                raise SetupError(f"Download does not contain {app_name}")
            subprocess.run(["ditto", str(app), str(target / app_name)], check=True)
        finally:
            if mounted:
                subprocess.run(["hdiutil", "detach", mount], check=False, capture_output=True)
    return target / app_name


def ensure_shell_path(home: Path, prefix: Path) -> None:
    """Replace exactly one marked block and preserve all unrelated shell text."""
    path = home / ".zshrc"
    # Preserve a user-managed symlink (for example dotfiles) and edit its target.
    if path.is_symlink():
        path = path.resolve()
    previous = path.read_text() if path.exists() else ""
    begin, end = "# >>> MyNotes managed PATH >>>", "# <<< MyNotes managed PATH <<<"
    if previous.count(begin) != previous.count(end) or previous.count(begin) > 1 or (begin in previous and previous.index(end) < previous.index(begin)):
        raise SetupError("Ambiguous MyNotes PATH block; preserve .zshrc and repair it before retry")
    escaped = str(prefix / "bin").replace("'", "'\"'\"'")
    block = f"{begin}\nexport PATH='{escaped}':\"$PATH\"\n{end}"
    if begin in previous:
        start, stop = previous.index(begin), previous.index(end) + len(end)
        content = previous[:start] + block + previous[stop:]
    else:
        content = previous.rstrip("\n") + ("\n\n" if previous else "") + block + "\n"
    if content != previous:
        fd, name = tempfile.mkstemp(dir=path.parent, prefix=".mynotes-zshrc-")
        try:
            with os.fdopen(fd, "w") as output:
                output.write(content)
            if path.exists():
                os.chmod(name, path.stat().st_mode & 0o777)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)


def install_components(root: Path, manifest: dict, home: Path, emit) -> dict:
    tools = {"schema_version": 1}
    for number, component in enumerate(manifest["components"], start=1):
        component_id, version = component["id"], component["version"]
        if not re.fullmatch(r"[a-z0-9-]+", component_id) or not re.fullmatch(r"[a-zA-Z0-9._-]+", version):
            raise SetupError("Unsafe component identifier or version")
        destination = home / "tools" / component_id / version
        emit("components", f"Preparing {component_id} {version}", number, len(manifest["components"]), component=component_id, phase="prepare")
        if component.get("format") == "dmg":
            archive = component_download(component, home / "downloads" / f"{component_id}-{version}.dmg", emit, component_id, "LibreOffice")
            receipt = destination / ".component.json"
            app = destination / component["app_name"]
            executable = safe_relative(app, component["executable"])
            try:
                installed_digest = json.loads(receipt.read_text()).get("sha256")
                if installed_digest and installed_digest != component["sha256"]:
                    raise SetupError("A completed component already exists with a different artifact for this version; preserve it and use a newer component version.")
                complete = installed_digest == component["sha256"] and executable.is_file()
            except (OSError, ValueError):
                complete = False
            if not complete:
                emit("components", "Preparing the Word renderer", component=component_id, phase="prepare")
                install_dmg(archive, destination, component["app_name"])
                if not executable.is_file():
                    raise SetupError("Component application has no expected executable")
                atomic_json(receipt, {"sha256": component["sha256"]})
            tools["soffice"] = str(executable)
        elif "files" in component:
            destination.mkdir(parents=True, exist_ok=True)
            with (destination / '.asr-model-preparation.lock').open('a+') as model_lease:
                fcntl.flock(model_lease, fcntl.LOCK_EX)
                for file_number, spec in enumerate(component["files"], start=1):
                    emit("components", f"Checking model file {file_number}/{len(component["files"])}: {spec["path"]}", file_number, len(component["files"]), component=component_id, phase="verify")
                    component_download(spec, safe_relative(destination, spec["path"]), emit, component_id, f"speech model file {file_number}/{len(component["files"])}")
            tools["model_cache"] = str(destination)
        else:
            raise SetupError("Unknown component format")
    source = root / "bin"
    argmax = source / "argmax-cli"
    if not argmax.is_file():
        raise SetupError("Release is missing its precompiled speech engine")
    engine_version = manifest.get("argmax_version", "bundled")
    if not re.fullmatch(r"[a-zA-Z0-9._-]+", engine_version):
        raise SetupError("Unsafe speech engine version")
    # Different release builds of an upstream tag remain immutable for rollback.
    signature = hashlib.sha256()
    for item in sorted(source.rglob("*")):
        if item.is_file():
            signature.update(str(item.relative_to(source)).encode())
            signature.update(digest(item).encode())
    binary_root = home / "tools" / "argmax" / f"{engine_version}-{signature.hexdigest()[:16]}"
    shutil.copytree(source, binary_root, dirs_exist_ok=True)
    (binary_root / "argmax-cli").chmod(0o755)
    tools["argmax"] = str(binary_root / "argmax-cli")
    return tools


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--provider", choices=("codex", "agy", "claude"), required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--progress-file", type=Path)
    options = parser.parse_args(argv)
    root = options.release_root.resolve()
    home = Path(os.environ.get("MYNOTES_HOME", str(Path.home() / "Library/Application Support/MyNotes"))).expanduser()
    workspace = (options.workspace or home / "workspace").expanduser().resolve()
    progress = options.progress_file or home / "setup-progress.json"
    app_home = Path(os.environ.get("MYNOTES_APP_HOME", str(home / "app")))
    previous_current = os.readlink(app_home / "current") if (app_home / "current").is_symlink() else None
    registry_backups = {name: (home / name).read_bytes() if (home / name).is_file() else None for name in ("tools.json", "setup.json")}
    selection_changed = False
    previous_handlers = {}
    preferences_receipt = None
    preferences_recovery = None
    launcher = Path(os.environ.get("MYNOTES_PREFIX", str(Path.home() / ".local"))) / "bin/mynotes"
    previous_launcher = launcher.read_bytes() if launcher.is_file() else None
    def emit(stage: str, message: str, completed: int = 0, total: int = 1, **extra):
        payload = {"stage": stage, "message": message, "completed": completed, "total": total, "provider": options.provider, "workspace": str(workspace), **extra}
        atomic_json(progress, payload)
        print(json.dumps(payload), flush=True)
    try:
        # Lease wraps components and configuration, not just the wheel switch.
        if os.environ.get("MYNOTES_INSTALL_CAPTURE_GUARD") != "1":
            return subprocess.run([sys.executable, str(root / "scripts/capture_install_guard.py"), sys.executable, str(Path(__file__).resolve()), *(sys.argv[1:] if argv is None else argv)]).returncode
        previous_handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        def interrupted(_signum, _frame):
            raise SetupError("Setup interrupted; retry to resume downloads and configuration.")
        signal.signal(signal.SIGTERM, interrupted)
        manifest = json.loads((root / "setup-components.json").read_text())
        if manifest.get("schema_version") != 1:
            raise SetupError("Unsupported setup component manifest")
        # Check all packaged payloads before executing bundled code or copying engines.
        release = json.loads((root / "release-manifest.json").read_text())
        for relative, expected in release["files"].items():
            path = safe_relative(root, relative)
            if not path.is_file() or digest(path) != expected:
                raise SetupError(f"Release integrity check failed: {relative}")
        import tomllib
        version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
        app_home = Path(os.environ.get("MYNOTES_APP_HOME", str(home / "app")))
        executable = app_home / "current/venv/bin/python"
        version_dir = app_home / "versions" / version
        emit("installation", "Checking MyNotes installation")
        environment = dict(os.environ, MYNOTES_PYTHON=sys.executable, MYNOTES_APP_HOME=str(app_home), PATH=str(root / "bin") + os.pathsep + os.environ.get("PATH", ""))
        # Retry a successful wheel installation without overwriting its version.
        wheels = list((root / "dist").glob("mynotes-*.whl"))
        if len(wheels) != 1:
            raise SetupError("Release must contain exactly one MyNotes wheel")
        if version_dir.exists():
            try:
                receipt = json.loads((version_dir / "installation.json").read_text())
            except (OSError, ValueError):
                receipt = {}
            if receipt.get("wheel_sha256") != digest(wheels[0]):
                raise SetupError("An existing installation uses a different artifact with this version. Use a newer release; keep the installed version.")
        tools = install_components(root, manifest, home, emit)
        emit("installation", "Installing MyNotes and Capture")
        if not (version_dir / "venv/bin/mynotes").is_file():
            subprocess.run(["/bin/zsh", str(root / "scripts/install-mynotes.sh")], env=environment, check=True)
        elif not executable.is_file() or (app_home / "current").resolve() != version_dir.resolve():
            subprocess.run(["/bin/zsh", str(root / "scripts/install-mynotes.sh"), "--rollback", version], env=environment, check=True)
        selection_changed = True
        try:
            prior_tools = json.loads(registry_backups["tools.json"] or b"{}")
        except ValueError:
            prior_tools = {}
        if all(prior_tools.get(key) == tools.get(key) for key in ("argmax", "soffice", "model_cache")):
            tools["execution_proof"] = prior_tools.get("execution_proof", {})
        # Optional local engines are user-installed, not part of the bundle.
        # A core runtime update must preserve their verified existing location.
        fluid = prior_tools.get('fluidaudio')
        if isinstance(fluid, str) and Path(fluid).is_file():
            tools['fluidaudio'] = fluid
        atomic_json(home / "tools.json", tools)
        emit("workspace", "Preparing your private workspace")
        prepared = subprocess.run([str(executable), "-c", "from mynotes.onboarding import prepare_setup_preferences; from mynotes.workspace import Workspace; import json,sys; print(json.dumps(prepare_setup_preferences(Workspace.from_value(sys.argv[1]), sys.argv[2])))", str(workspace), options.provider], env=environment, check=True, capture_output=True, text=True)
        if isinstance(prepared.stdout, str) and prepared.stdout.strip():
            preferences_receipt = json.loads(prepared.stdout)
        emit("verification", "Testing local transcription and Word rendering. No AI provider call.")
        subprocess.run([str(executable), "-c", "from mynotes.onboarding import verify_local_components; from mynotes.workspace import Workspace; import sys; verify_local_components(Workspace.from_value(sys.argv[1]))", str(workspace)], env=environment, check=True)
        verified_tools = json.loads((home / "tools.json").read_text())
        atomic_json(version_dir / "managed-tools.json", verified_tools)
        user_home = Path(os.environ.get("MYNOTES_USER_HOME", str(Path.home()))).expanduser()
        user_home.mkdir(parents=True, exist_ok=True)
        ensure_shell_path(user_home, Path(os.environ.get("MYNOTES_PREFIX", str(Path.home() / ".local"))))
        atomic_json(home / "setup.json", {"schema_version": 1, "workspace": str(workspace), "provider": options.provider, "version": version})
        emit("ready", "Components installed. Recheck your agent and open the chat.", 1, 1)
        return 0
    except (OSError, ValueError, KeyError, SetupError, subprocess.CalledProcessError, urllib.error.URLError, http.client.HTTPException, KeyboardInterrupt) as error:
        # Do not let a second interruption split rollback halfway through.
        for signum in previous_handlers:
            signal.signal(signum, signal.SIG_IGN)
        pointer_changed = (app_home / "current").is_symlink() and os.readlink(app_home / "current") != previous_current
        if selection_changed or pointer_changed:
            if preferences_receipt:
                try:
                    undone = subprocess.run([str(version_dir / "venv/bin/python"), "-c", "from mynotes.onboarding import restore_setup_preferences; from mynotes.workspace import Workspace; import json,sys; print(restore_setup_preferences(Workspace.from_value(sys.argv[1]), json.load(sys.stdin)))", str(workspace)], input=json.dumps(preferences_receipt), env=environment, capture_output=True, text=True, check=True, timeout=30)
                    preferences_recovery = "restored" if undone.stdout.strip() == "True" else "user_edits_preserved"
                except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    preferences_recovery = "preserved_restore_unavailable"
            for name, original in registry_backups.items():
                if original is None:
                    (home / name).unlink(missing_ok=True)
                else:
                    descriptor, temporary = tempfile.mkstemp(dir=home, prefix=f".{name}.restore-")
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(original)
                    os.replace(temporary, home / name)
            if previous_current:
                (app_home / "current").unlink(missing_ok=True)
                (app_home / "current").symlink_to(previous_current)
            else:
                (app_home / "current").unlink(missing_ok=True)
                if previous_launcher is None:
                    launcher.unlink(missing_ok=True)
                else:
                    launcher.write_bytes(previous_launcher)
        emit("failed", "Setup paused; retry after resolving the problem.", error=str(error) or "Setup interrupted; retry to resume.", preferences_recovery=preferences_recovery)
        return 2
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
