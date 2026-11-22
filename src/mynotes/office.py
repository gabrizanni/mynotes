"""Locate LibreOffice and render documents with an isolated profile."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .provider_process import run_windows_job


class OfficeError(RuntimeError):
    """A conversion cannot be run or did not produce a fresh PDF."""


def find_soffice(managed: str | None = None) -> str | None:
    override = os.environ.get("MYNOTES_SOFFICE")
    if os.name == "nt":
        installed = [str(Path(os.environ[variable]) / "LibreOffice/program/soffice.com")
                     for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")
                     if os.environ.get(variable)]
        path = [Path(folder.strip('"')) for folder in os.environ.get("PATH", "").split(os.pathsep)]
        bundled = str(Path(sys.executable).parent.parent / "office/program/soffice.com")
        candidates = [override, managed, bundled, *installed,
                      *(str(directory / name) for directory in path if directory.is_absolute()
                        for name in ("soffice.com", "soffice.exe"))]
        return next((str(item) for item in candidates if item and Path(item).is_absolute()
                     and Path(item).suffix.lower() in {".com", ".exe"} and Path(item).is_file()), None)
    bundled = sorted((Path.home() / ".cache" / "codex-runtimes").glob("*/dependencies/bin/override/soffice"))
    candidates = [override, managed, shutil.which("soffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice",
                  *(str(item) for item in bundled)]
    return next((str(Path(item)) for item in candidates if item and Path(item).is_file()), None)


def macos_launch_restriction() -> str | None:
    """Probe permissions without starting AppKit or LibreOffice.

    The native macOS renderer registers with LaunchServices even with
    --headless. Denied mach lookups made it abort in _RegisterApplication.
    sandbox_check is an optional OS probe; the Codex marker is the fallback
    if the probe is unavailable. No sandbox permissions are changed here.
    """
    if sys.platform != "darwin":
        return None
    try:
        library = ctypes.CDLL("/usr/lib/libsandbox.dylib")
        check = library.sandbox_check
        check.restype = ctypes.c_int
        check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        for service in (b"com.apple.coreservices.launchservicesd", b"com.apple.lsd.mapdb"):
            # SANDBOX_FILTER_GLOBAL_NAME = 2; final argument is variadic.
            result = check(os.getpid(), b"mach-lookup", 2, ctypes.c_char_p(service))
            if result == 1:
                return f"macOS sandbox denies {service.decode()}"
            if result != 0:
                raise OSError("macOS sandbox permission probe unavailable")
    except (OSError, AttributeError):
        if os.environ.get("CODEX_SANDBOX") == "seatbelt":
            return "Codex macOS sandbox; LaunchServices access could not be verified"
    return None


def convert_to_pdf(executable: str, source: Path, output_dir: Path, *, timeout: int) -> Path:
    restriction = macos_launch_restriction()
    if restriction:
        raise OfficeError(
            "LibreOffice was not started to prevent a macOS crash: " + restriction
            + ". Run this conversion from the MyNotes app or a normal Terminal outside "
            "the restricted sandbox. No PDF or visual validation has been completed."
        )
    source = source.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mynotes-office-") as temporary:
        root = Path(temporary)
        profile = root / "profile"
        profile.mkdir()
        converted = root / "converted"
        converted.mkdir()
        command = [executable, f"-env:UserInstallation={profile.as_uri()}",
                   "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
                   "--norestore", "--convert-to", "pdf", "--outdir", str(converted), str(source)]
        try:
            if os.name == "nt":
                completed = run_windows_job(command, timeout=timeout)
            else:
                completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as error:
            raise OfficeError(f"LibreOffice conversion timed out after {timeout}s; no automatic retry") from error
        except OSError as error:
            raise OfficeError(f"LibreOffice could not be started: {error}") from error
        fresh_pdf = converted / f"{source.stem}.pdf"
        if completed.returncode != 0 or not fresh_pdf.is_file() or fresh_pdf.stat().st_size == 0:
            detail = (completed.stderr or completed.stdout or "No fresh PDF was produced").strip()
            raise OfficeError(f"LibreOffice conversion failed (exit {completed.returncode}): {detail}")
        target = output_dir / fresh_pdf.name
        descriptor, name = tempfile.mkstemp(prefix=".mynotes-pdf-", dir=output_dir)
        os.close(descriptor)
        try:
            shutil.copyfile(fresh_pdf, name)
            os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
        return target
