#!/usr/bin/env python3
"""Validate the explicitly allow-listed public release tree."""
from __future__ import annotations

import base64
import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    ".github/pull_request_template.md",
    "docs/GIT_WORKFLOW.md",
    "docs/LIBRARY_COURSE_MANAGEMENT.md",
    "docs/SOFT_PROGRESS_POLICY.md",
    ".github/workflows/ci.yml",
    ".agents/skills/mynotes/SKILL.md",
    ".agents/skills/mynotes/.mynotes-integration.json",
    ".agents/plugins/mynotes/plugin.json",
    ".agents/plugins/mynotes/.mynotes-integration.json",
    ".agents/plugins/mynotes/skills/mynotes/SKILL.md",
    ".agents/plugins/mynotes/agents/mynotes.md",
    ".gitignore",
    "LICENSE",
    "README.md",
    "README.it.md",
    "pyproject.toml",
    "uv.lock",
    "docs/INSTALL.md",
    "docs/INSTALL.it.md",
    "docs/PRIVACY.md",
    "docs/UPDATES.md",
    "docs/VALIDATION.md",
    "docs/CAPTURE.md",
    "docs/CAPTURE-UI.md",
    "docs/ONBOARDING.md",
    "docs/Guida.html",
    "docs/BRAND.md",
    "docs/OPTIMIZATION.md",
    "docs/GENERATION.md",
    "docs/NOTES_DELIVERY.md",
    "docs/NOTES_FLOW.md",
    "docs/CODEX_NOTES.md",
    "docs/MODEL-CATALOG.md",
    "docs/images/open-anyway.svg",
    "docs/images/open-anyway.it.svg",
    "examples/README.md",
    "examples/demo-course.json",
    "examples/demo-transcript.txt",
    "examples/demo-materials.txt",
    "examples/demo-personal-notes.txt",
    "examples/quantitative-course.json",
    "examples/quantitative-transcript.txt",
    "examples/discursive-course.json",
    "examples/discursive-transcript.txt",
    "examples/discursive-personal-notes.txt",
    "examples/python-about-web-snapshot.txt",
    "integrations/README.md",
    "scripts/README.md",
    "scripts/build_release.py",
    "scripts/build_setup.py",
    "scripts/build_dmg.py",
    "scripts/setup_runtime.py",
    "setup/Package.swift",
    "setup/components.lock.json",
    "setup/argmax.Package.resolved",
    "setup/Resources/Info.plist",
    "setup/Resources/MyNotesLogo.png",
    "setup/Resources/MyNotesGuideLogo.png",
    "setup/Resources/MyNotes.icns",
    "setup/Sources/MyNotesSetup/App.swift",
    "setup/Sources/MyNotesSetup/ChatTerminal.swift",
    "setup/Sources/MyNotesSetup/SetupProgress.swift",
    "setup/Sources/MyNotesSetup/ModelChoices.swift",
    "setup/Sources/MyNotesSetup/ModelRoles.swift",
    "setup/Sources/MyNotesSetup/ModelRoleEditor.swift",
    "setup/Sources/MyNotesSetup/ModelCatalogCommand.swift",
    "setup/Sources/MyNotesSetup/MyNotesHomeView.swift",
    "setup/Sources/MyNotesSetup/CaptureDashboard.swift",
    "setup/Sources/MyNotesSetup/CaptureModel.swift",
    "setup/Sources/MyNotesSetup/CaptureLibraryView.swift",
    "setup/Sources/MyNotesSetup/CaptureView.swift",
    "setup/Sources/MyNotesSetup/LibraryLesson.swift",
    "setup/Sources/MyNotesSetup/LibraryManagementView.swift",
    "setup/Sources/MyNotesSetup/WorkspaceModels.swift",
    "setup/Sources/MyNotesSetup/WorkspaceActivityView.swift",
    "setup/Sources/MyNotesSetup/WorkspaceCourseProfileView.swift",
    "setup/Sources/MyNotesSetup/WorkspaceView.swift",
    "setup/Sources/MyNotesSetup/WorkspaceCalendarView.swift",
    "setup/Sources/MyNotesSetup/WorkspaceLessonDetail.swift",
    "setup/Sources/MyNotesSetup/WorkspaceAudioImportView.swift",
    "setup/Sources/MyNotesSetup/WorkspaceSettingsView.swift",
    "scripts/build_capture.py",
    "scripts/capture_install_guard.py",
    "scripts/cleanup_launch_services.py",
    "scripts/test_capture_native.py",
    "scripts/build_base_template.py",
    "scripts/check_public_tree.py",
    "scripts/install-mynotes.sh",
    "scripts/validate_word_pipeline.py",
    "THIRD_PARTY_NOTICES.md",
)
PUBLIC_DIRECTORIES = ("src", "tests")
# Generated brand assets have no private metadata; pin their reviewed bytes.
PUBLIC_BINARY_SHA256 = {
    'capture/Resources/MyNotesCaptureLogo.png': 'df672aace383a25db44e62906d27effbb5e6b1fc0f95748ee27bdf78dbc63807',
    'capture/Resources/MyNotesCapture.icns': '4096f61be98431281cc7c5bc8476565610f03822078ce7c05b1fe35fe155d7b6',
    'setup/Resources/MyNotesLogo.png': '58c5930171b6964bf5ef2da719c980cfca9ec147242bef0cf1516f3d7f5fe339',
    'setup/Resources/MyNotesGuideLogo.png': 'e55a7ee562bbd210cda912728fd15cb4d8ecebcad0dc970c1b30111c55f6944e',
    'setup/Resources/MyNotes.icns': '1437f005c317e36cc657bdb467c930f783f10c0c641ad7ce6216f7ab591e9870',
}

PRIVATE_TERMS = (
    "/" + "Users/",
    "I" + "IMB",
    "P" + "GAI",
    "S" + "CM",
    "E" + "SAE",
    "P" + "EVC",
    "Ja" + "ri",
    "Bocc" + "oni",
)
PRIVATE_REFERENCE = re.compile("|".join(re.escape(term) for term in PRIVATE_TERMS), re.IGNORECASE)
TEMPLATE_PARTS = frozenset({
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
    "word/_rels/document.xml.rels",
    "word/styles.xml",
    "word/numbering.xml",
    "word/theme/theme1.xml",
})


def public_paths(root: Path = ROOT) -> list[Path]:
    paths = [root / relative for relative in PUBLIC_FILES]
    for directory in PUBLIC_DIRECTORIES:
        paths.extend(
            path
            for path in sorted((root / directory).rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )
    paths.extend(root / path for path in (
        "capture/Package.swift", "capture/Resources/Info.plist", "capture/Resources/Entitlements.plist",
        "capture/Resources/MyNotesCaptureLogo.png", "capture/Resources/MyNotesCapture.icns",
        "capture/Sources/MyNotesCapture/Models.swift", "capture/Sources/MyNotesCapture/Audio.swift",
        "capture/Sources/MyNotesCapture/App.swift", "capture/Tests/MyNotesCaptureTests/CaptureTests.swift", "capture/Tests/Regression.swift",
    ))
    return paths


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for path in public_paths(root):
        if not path.is_file():
            errors.append(f"missing public file: {path.relative_to(root)}")
            continue
        expected_binary = PUBLIC_BINARY_SHA256.get(path.relative_to(root).as_posix())
        if expected_binary is not None:
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected_binary:
                errors.append(f"changed public brand asset: {path.relative_to(root)}")
            continue
        if path.suffix == ".docx":
            if path.relative_to(root).as_posix() != "src/mynotes/resources/base-template.docx":
                errors.append(f"unexpected public document: {path.relative_to(root)}")
                continue
            try:
                with ZipFile(path) as archive:
                    members = set(archive.namelist())
                    if members != TEMPLATE_PARTS:
                        errors.append(f"unexpected template package parts: {path.relative_to(root)}")
                    for member in members & TEMPLATE_PARTS:
                        content = archive.read(member).decode("utf-8")
                        if PRIVATE_REFERENCE.search(content):
                            errors.append(f"private reference in template part: {member}")
            except (BadZipFile, UnicodeDecodeError):
                errors.append(f"invalid public document: {path.relative_to(root)}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"non-text public file: {path.relative_to(root)}")
            continue
        if path.relative_to(root).as_posix() == "docs/Guida.html":
            # Embedded image bytes can contain random matches for short terms.
            # Exclude only the exact reviewed, separately hash-checked logo.
            logo = root / "setup/Resources/MyNotesGuideLogo.png"
            if logo.is_file() and hashlib.sha256(logo.read_bytes()).hexdigest() == PUBLIC_BINARY_SHA256["setup/Resources/MyNotesGuideLogo.png"]:
                embedded_logo = "data:image/png;base64," + base64.b64encode(logo.read_bytes()).decode("ascii")
                content = content.replace(embedded_logo, "[reviewed MyNotes logo]")
        if PRIVATE_REFERENCE.search(content):
            errors.append(f"private reference in public file: {path.relative_to(root)}")
    return errors


def validate_windows_source(root: Path = ROOT) -> list[str]:
    """Audit Windows Git sources without adding them to the macOS installer."""
    tracked = subprocess.check_output(["git", "ls-files", "-z", "--", "windows/"], cwd=root)
    suffixes = {".cs", ".csproj", ".props", ".targets", ".xaml", ".resw", ".manifest",
                ".xml", ".md", ".json", ".iss", ".py", ".ps1", ".sln", ".slnx"}
    private_profile = re.compile(r"[a-z]:[\\/]users[\\/]", re.IGNORECASE)
    brand_icons = {
        "windows/desktop/MyNotes.ico": "522755e72d192044af7abd6013e45b2e440e1da1264ccf96abff9a4df204e3ea",
    }
    errors = []
    for name in filter(None, tracked.decode("utf-8").split("\0")):
        path = root / name
        if name in brand_icons and path.is_file() and not path.is_symlink():
            if hashlib.sha256(path.read_bytes()).hexdigest() != brand_icons[name]:
                errors.append(f"changed Windows public brand asset: {name}")
            continue
        if path.is_symlink() or (path.suffix.lower() not in suffixes and path.name != ".gitignore"):
            errors.append(f"unexpected Windows public artifact: {name}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append(f"unreadable Windows public source: {name}")
            continue
        if PRIVATE_REFERENCE.search(content) or private_profile.search(content):
            errors.append(f"private reference in Windows public source: {name}")
    return errors


DESKTOP_SUFFIXES = {".ts", ".tsx", ".css", ".js", ".json", ".html", ".md", ".rs", ".toml", ".lock", ".txt"}
# Bundled OFL fonts, generated app icons and the sidebar logo: binary, so their reviewed bytes
# are pinned instead of scanned (random byte runs can match short private terms).
DESKTOP_BINARY_SHA256 = {
    "desktop/src-tauri/icons/32x32.png": "424e6971fcb7ee760604f20ebb904e70830706d6e917cfbae3d87a571a53cfbb",
    "desktop/src-tauri/icons/128x128.png": "56e8e09ae6265c3bba76d88974675569cd819f6c983ed3c25997871ad8a980f9",
    "desktop/src-tauri/icons/128x128@2x.png": "993b50997e1ca05c334175ab7a4cc9491c6b82638093c663ccdc80849587ffe4",
    "desktop/src-tauri/icons/icon.png": "476d58d00cdb7c33e8d1e90fba36644d2571d978b2c97eeff93c8b63a380d81f",
    "desktop/src-tauri/icons/icon.icns": "cee1447ed5cf4982ec9faa73544fdc2014d8611b583ce50076a4bdc6f17a9785",
    "desktop/src-tauri/icons/icon.ico": "c1bb916d4ca3bf8d72a3dce8759150665307de5bcbd93e04289a5f0c5547c188",
    "desktop/src/assets/brand/mynotes-logo-256.png": "993b50997e1ca05c334175ab7a4cc9491c6b82638093c663ccdc80849587ffe4",
    "desktop/src/assets/fonts/eb-garamond/EBGaramond-Medium.woff2": "0adb3925217e1eb28effd7b651186e51a723d3447c9236a9babfd231a38febb7",
    "desktop/src/assets/fonts/figtree/Figtree-Variable.woff2": "0d516b55d6e79be18423a00cf9d94ed77c1cad4ee4aeb40628b6bef4ef567830",
    "desktop/src/assets/fonts/jetbrains-mono/JetBrainsMono-Regular.woff2": "4ebab93f27e6db455f0508dde4a4d9051eb9e1749b6fcbe9a14be1ae28c05d3d",
}
DESKTOP_IGNORED_PARTS = {"node_modules", "target", "dist", "test-results", "playwright-report"}
LOCKFILE_INTEGRITY = re.compile(r'"integrity":\s*"[^"]*"')


def desktop_files(root: Path = ROOT) -> list[str]:
    """Tracked plus not-yet-committed (but not ignored) files under desktop/."""
    listed = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "desktop/"], cwd=root
    )
    names = sorted(set(filter(None, listed.decode("utf-8").split("\0"))))
    return [
        name for name in names
        if not DESKTOP_IGNORED_PARTS.intersection(Path(name).parts) and not name.startswith("desktop/tests/shots/")
    ]


def validate_desktop_source(root: Path = ROOT) -> list[str]:
    """Audit the Tauri desktop sources (desktop/) without adding them to the macOS installer."""
    private_profile = re.compile(r"[a-z]:[\\/]users[\\/]|/home/[a-z_][a-z0-9_.-]*/", re.IGNORECASE)
    errors = []
    for name in desktop_files(root):
        path = root / name
        if not path.is_file():
            continue  # deleted in the working tree, nothing to publish
        if name in DESKTOP_BINARY_SHA256 and not path.is_symlink():
            if hashlib.sha256(path.read_bytes()).hexdigest() != DESKTOP_BINARY_SHA256[name]:
                errors.append(f"changed desktop public asset: {name}")
            continue
        if path.is_symlink() or (path.suffix.lower() not in DESKTOP_SUFFIXES and path.name != ".gitignore"):
            errors.append(f"unexpected desktop public artifact: {name}")
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append(f"unreadable desktop public source: {name}")
            continue
        if path.name == "package-lock.json":
            # Base64 integrity hashes match short private terms at random; they carry no text.
            content = LOCKFILE_INTEGRITY.sub('"integrity": ""', content)
        if PRIVATE_REFERENCE.search(content) or private_profile.search(content):
            errors.append(f"private reference in desktop public source: {name}")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows-source", action="store_true")
    parser.add_argument("--desktop-source", action="store_true")
    arguments = parser.parse_args()
    issues = validate()
    if arguments.windows_source:
        issues += validate_windows_source()
    if arguments.desktop_source:
        issues += validate_desktop_source()
    if issues:
        print("\n".join(issues), file=sys.stderr)
        raise SystemExit(1)
    print(f"public tree clean: {len(public_paths())} files")
