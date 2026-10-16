#!/usr/bin/env python3
"""Validate the explicitly allow-listed public release tree."""
from __future__ import annotations

import base64
import hashlib
import re
import sys
from pathlib import Path
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = (
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
    "setup/Sources/MyNotesSetup/SetupProgress.swift",
    "setup/Sources/MyNotesSetup/ModelChoices.swift",
    "setup/Sources/MyNotesSetup/ModelCatalogCommand.swift",
    "setup/Sources/MyNotesSetup/MyNotesHomeView.swift",
    "setup/Sources/MyNotesSetup/CaptureDashboard.swift",
    "setup/Sources/MyNotesSetup/CaptureModel.swift",
    "setup/Sources/MyNotesSetup/CaptureLibraryView.swift",
    "setup/Sources/MyNotesSetup/CaptureView.swift",
    "scripts/build_capture.py",
    "scripts/capture_install_guard.py",
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


if __name__ == "__main__":
    issues = validate()
    if issues:
        print("\n".join(issues), file=sys.stderr)
        raise SystemExit(1)
    print(f"public tree clean: {len(public_paths())} files")
