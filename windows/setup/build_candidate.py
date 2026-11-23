"""Build an internal Windows x64 installer using only pinned build inputs."""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import struct
import subprocess
import tempfile
import tomllib
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
PYTHON_VERSION = "3.12.14"
LIBREOFFICE_SHA256 = "0e6918c273eabbdbbd800ee0dacbbf130a14c13c2e4cec861b06dd59e015bf94"
WHISPER_ARCHIVE_SHA256 = "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"
WHISPER_LICENSE_SHA256 = "94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d"


def run(*args: str | Path, env: dict[str, str] | None = None) -> None:
    subprocess.run(list(map(str, args)), cwd=ROOT, env=env, check=True)


def publish(project: Path, destination: Path, *properties: str) -> None:
    run("dotnet", "publish", project, "--configuration", "Release",
        "--runtime", "win-x64", "--self-contained", "true",
        "--output", destination, *properties)


def stage_runtime(build: Path, stage: Path) -> None:
    distributions = build / "python"
    env = dict(os.environ, UV_PYTHON_INSTALL_DIR=str(distributions))
    env.pop("VIRTUAL_ENV", None)
    run("uv", "python", "install", "--no-bin", PYTHON_VERSION, env=env)
    managed = Path(subprocess.check_output(
        ["uv", "python", "find", "--no-project", "--system", "--managed-python", PYTHON_VERSION],
        cwd=build, env=env, text=True,
    ).strip()).resolve()
    if not managed.is_relative_to(distributions.resolve()):
        raise RuntimeError("uv did not select the standalone managed Python distribution")
    runtime = stage / "runtime"
    shutil.copytree(managed.parent, runtime)
    python = runtime / "python.exe"
    run(python, "-I", "-c",
        "import struct, sys; assert sys.platform == 'win32' and struct.calcsize('P') == 8")
    requirements = build / "requirements.txt"
    run("uv", "export", "--locked", "--no-dev", "--no-emit-project", "--output-file", requirements)
    run("uv", "build", "--wheel", "--out-dir", build / "wheel")
    wheel, = (build / "wheel").glob("mynotes-*.whl")
    lib = runtime / "Lib" / "site-packages"
    run("uv", "pip", "install", "--python", python, "--target", lib,
        "--require-hashes", "--no-deps", "-r", requirements)
    run("uv", "pip", "install", "--python", python, "--target", lib, "--no-deps", wheel)
    publish(ROOT / "windows/launcher/MyNotes.Cli.csproj", build / "launcher",
            "-p:PublishSingleFile=true", "-p:EnableCompressionInSingleFile=true")
    shutil.copyfile(build / "launcher/mynotes.exe", runtime / "mynotes.exe")
    # Smoke test using the copied runtime, not the development venv.
    run(runtime / "mynotes.exe", "--version")
    run(runtime / "mynotes.exe", "init", "--workspace", build / "workspace")


def verify_input(path: Path, digest: str) -> None:
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != digest:
            raise ValueError(f"{path.name} checksum mismatch")


def stage_office(build: Path, stage: Path, msi: Path) -> None:
    verify_input(msi, LIBREOFFICE_SHA256)
    extracted = build / "LibreOffice image"
    msiexec = Path(os.environ["SystemRoot"]) / "System32" / "msiexec.exe"
    run(msiexec, "/a", msi, "/qn", "/norestart", f"TARGETDIR={extracted}")
    renderer, = extracted.rglob("soffice.com")
    shutil.move(renderer.parent.parent, stage / "office")
    python = stage / "runtime" / "python.exe"
    env = dict(os.environ, MYNOTES_HOME=str(build / "renderer proof"))
    env.pop("MYNOTES_SOFFICE", None)
    run(python, "-I", "-c",
        "from mynotes.onboarding import verify_word_renderer; "
        "print(verify_word_renderer()['word_pages'])",
        env=env)


def stage_whisper(stage: Path, archive: Path, license_file: Path) -> None:
    verify_input(archive, WHISPER_ARCHIVE_SHA256)
    verify_input(license_file, WHISPER_LICENSE_SHA256)
    destination = stage / "speech" / "whisper"
    destination.mkdir(parents=True)
    with ZipFile(archive) as package:
        for member in package.namelist():
            name = Path(member).name
            if name in {"whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll"} or (
                    name.startswith("ggml-cpu-") and name.endswith(".dll")):
                with package.open(member) as source, (destination / name).open("xb") as target:
                    shutil.copyfileobj(source, target)
    shutil.copyfile(license_file, destination / "LICENSE.txt")
    run(destination / "whisper-cli.exe", "--help")


def build_candidate(output: Path, iscc: Path, libreoffice_msi: Path, whisper_zip: Path, whisper_license: Path) -> Path:
    if platform.system() != "Windows" or platform.machine().upper() != "AMD64" or struct.calcsize("P") != 8:
        raise RuntimeError("The Windows installer must be built on an x64 Windows runner")
    if not iscc.is_file():
        raise FileNotFoundError("Pass the pinned Inno Setup compiler with --iscc")
    output.mkdir(parents=True, exist_ok=True)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    with tempfile.TemporaryDirectory(prefix="mynotes-windows-") as temporary:
        build = Path(temporary)
        stage = build / "stage"
        stage.mkdir()
        stage_runtime(build, stage)
        stage_office(build, stage, libreoffice_msi)
        stage_whisper(stage, whisper_zip, whisper_license)
        publish(ROOT / "windows/desktop/MyNotes.Desktop.csproj", stage / "desktop", "-p:Platform=x64")
        publish(ROOT / "windows/capture/MyNotes.Capture.csproj", stage / "capture")
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copyfile(ROOT / name, stage / name)
        run(iscc, f"/DStageDir={stage}", f"/DOutputFolder={output}", f"/DMyNotesVersion={version}",
            ROOT / "windows/setup/MyNotes.iss")
    installer = output / "MyNotes-Setup-x64-internal.exe"
    with installer.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    installer.with_suffix(".exe.sha256").write_text(f"{digest}  {installer.name}\n", encoding="utf-8")
    return installer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--libreoffice-msi", type=Path, required=True)
    parser.add_argument("--whisper-zip", type=Path, required=True)
    parser.add_argument("--whisper-license", type=Path, required=True)
    args = parser.parse_args()
    print(build_candidate(args.output.resolve(), args.iscc.resolve(), args.libreoffice_msi.resolve(),
                          args.whisper_zip.resolve(), args.whisper_license.resolve()))


if __name__ == "__main__":
    main()
