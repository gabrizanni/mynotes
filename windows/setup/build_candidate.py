"""Build an internal Windows x64 installer using only pinned build inputs."""
from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import struct
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[2]
PYTHON_VERSION = "3.12.14"
LIBREOFFICE_SHA256 = "0e6918c273eabbdbbd800ee0dacbbf130a14c13c2e4cec861b06dd59e015bf94"
LESSMSI_SHA256 = "5b4e187e74b184ad3a63ccf06c3d17dae2b8c4b6c298a996dbd51a9f6db29d21"
WHISPER_ARCHIVE_SHA256 = "6ba69e3482d7826214f90a6a9c84ca07782aec1e1d0c6a7c30c994fd5d816ccb"
WHISPER_LICENSE_SHA256 = "94f29bbed6a22c35b992c5c6ebf0e7c92f13b836b90f36f461c9cf2f0f1d010d"
SEGMENTATION_ARCHIVE_SHA256 = "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488"
EMBEDDING_SHA256 = "f1ee61be04000b50392a63442e5dd9feaea7e81dddd494d3647cf1c3d88a1498"
EMBEDDING_LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"


def run(*args: str | Path, env: dict[str, str] | None = None, timeout: int = 360) -> None:
    subprocess.run(list(map(str, args)), cwd=ROOT, env=env, check=True, timeout=timeout)


def publish(project: Path, destination: Path, *properties: str) -> None:
    run("dotnet", "publish", project, "--configuration", "Release",
        "--runtime", "win-x64", "--self-contained", "true",
        "--output", destination, *properties)


def stage_runtime(build: Path, stage: Path, version: str) -> None:
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
            "-p:PublishSingleFile=true", "-p:EnableCompressionInSingleFile=true", f"-p:Version={version}")
    shutil.copyfile(build / "launcher/mynotes.exe", runtime / "mynotes.exe")
    # Smoke test using the copied runtime, not the development venv.
    run(runtime / "mynotes.exe", "--version")
    run(runtime / "mynotes.exe", "init", "--workspace", build / "workspace")


def verify_input(path: Path, digest: str) -> None:
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != digest:
            raise ValueError(f"{path.name} checksum mismatch")


def stage_office(build: Path, stage: Path, msi: Path, extractor: Path) -> None:
    verify_input(msi, LIBREOFFICE_SHA256)
    verify_input(extractor, LESSMSI_SHA256)
    tool_dir = build / "lessmsi"
    with ZipFile(extractor) as package:
        package.extractall(tool_dir)
    tool, = tool_dir.rglob("lessmsi.exe")
    extracted = build / "LibreOffice image"
    # lessmsi uses a trailing separator to distinguish the output directory
    # from an extracted filename. It leaves Windows Installer state untouched.
    run(tool, "x", msi, str(extracted) + os.sep, timeout=180)
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


def stage_diarization(stage: Path, segmentation: Path, embedding: Path, embedding_license: Path) -> None:
    verify_input(segmentation, SEGMENTATION_ARCHIVE_SHA256)
    verify_input(embedding, EMBEDDING_SHA256)
    verify_input(embedding_license, EMBEDDING_LICENSE_SHA256)
    destination = stage / "speech" / "diarization"
    destination.mkdir(parents=True)
    with tarfile.open(segmentation, "r:bz2") as package:
        for source_name, target_name in (("model.onnx", "segmentation.onnx"), ("LICENSE", "SEGMENTATION-LICENSE.txt")):
            item = package.getmember(f"sherpa-onnx-pyannote-segmentation-3-0/{source_name}")
            if not item.isfile():
                raise ValueError(f"Missing regular file in segmentation archive: {source_name}")
            with package.extractfile(item) as source, (destination / target_name).open("xb") as target:
                shutil.copyfileobj(source, target)
    shutil.copyfile(embedding, destination / "embedding.onnx")
    shutil.copyfile(embedding_license, destination / "EMBEDDING-LICENSE.txt")


def build_candidate(args: argparse.Namespace) -> Path:
    output = args.output.resolve()
    iscc = args.iscc.resolve()
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
        print("Building the WinUI desktop and Capture worker.", flush=True)
        publish(ROOT / "windows/desktop/MyNotes.Desktop.csproj", stage / "desktop",
                "-p:Platform=x64", f"-p:Version={version}")
        publish(ROOT / "windows/capture/MyNotes.Capture.csproj", stage / "capture", f"-p:Version={version}")
        print("Staging standalone Python and locked dependencies.", flush=True)
        stage_runtime(build, stage, version)
        print("Checking the bundled office and speech applications.", flush=True)
        stage_office(build, stage, args.libreoffice_msi, args.lessmsi_zip)
        stage_whisper(stage, args.whisper_zip, args.whisper_license)
        stage_diarization(stage, args.segmentation_tar, args.embedding_model, args.embedding_license)
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
            shutil.copyfile(ROOT / name, stage / name)
        run(iscc, "--quiet-progress", f"/DStageDir={stage}", f"/DOutputFolder={output}", f"/DMyNotesVersion={version}",
            ROOT / "windows/setup/MyNotes.iss", timeout=720)
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
    parser.add_argument("--lessmsi-zip", type=Path, required=True)
    parser.add_argument("--whisper-zip", type=Path, required=True)
    parser.add_argument("--whisper-license", type=Path, required=True)
    parser.add_argument("--segmentation-tar", type=Path, required=True)
    parser.add_argument("--embedding-model", type=Path, required=True)
    parser.add_argument("--embedding-license", type=Path, required=True)
    args = parser.parse_args()
    print(build_candidate(args))


if __name__ == "__main__":
    main()
