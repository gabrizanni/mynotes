#!/usr/bin/env python3
"""Validate the explicitly allow-listed public release tree."""
from __future__ import annotations

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
    "scripts/build_base_template.py",
    "scripts/check_public_tree.py",
    "scripts/install-mynotes.sh",
    "scripts/validate_word_pipeline.py",
    "THIRD_PARTY_NOTICES.md",
)
PUBLIC_DIRECTORIES = ("src", "tests")
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
    return paths


def validate(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for path in public_paths(root):
        if not path.is_file():
            errors.append(f"missing public file: {path.relative_to(root)}")
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
        if PRIVATE_REFERENCE.search(content):
            errors.append(f"private reference in public file: {path.relative_to(root)}")
    return errors


if __name__ == "__main__":
    issues = validate()
    if issues:
        print("\n".join(issues), file=sys.stderr)
        raise SystemExit(1)
    print(f"public tree clean: {len(public_paths())} files")
