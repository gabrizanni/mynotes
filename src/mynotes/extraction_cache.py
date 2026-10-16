"""Verified local extraction cache; source originals are never modified."""
from __future__ import annotations

import copy
import fcntl
import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import extraction, office
from .utils import atomic_json_write, file_sha256

CACHE_VERSION = 1
DEPENDENCIES = {
    ".pdf": ("pypdfium2", "Pillow"),
    ".ppt": ("pypdfium2", "Pillow"),
    ".pptx": ("python-pptx", "lxml", "pypdfium2", "Pillow"),
    ".xlsx": ("openpyxl", "et_xmlfile"),
    ".docx": ("python-docx", "lxml"),
}


def _json_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def extractor_identity(suffix: str, *, render_visuals: bool) -> dict:
    versions = {}
    for name in DEPENDENCIES.get(suffix, ()):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    identity = {"cache_version": CACHE_VERSION, "cache_implementation_sha256": file_sha256(Path(__file__)), "extractor_sha256": file_sha256(Path(extraction.__file__)),
                "python": list(sys.version_info[:3]), "dependencies": versions}
    if suffix in {".pdf", ".ppt"} or (suffix == ".pptx" and render_visuals):
        import pypdfium2_raw
        native_root = Path(pypdfium2_raw.__file__).parent
        identity["pdfium_binaries"] = {item.name: file_sha256(item) for item in sorted(native_root.iterdir())
                                        if item.is_file() and item.suffix in {".dylib", ".so", ".dll"}}
    if suffix == ".ppt" or (suffix == ".pptx" and render_visuals):
        executable = extraction.libreoffice_executable()
        resolved = (shutil.which(executable) or executable) if executable else None
        identity["renderer"] = {"path": resolved, "sha256": file_sha256(Path(resolved)) if resolved and Path(resolved).is_file() else None}
        identity["office_launcher_sha256"] = file_sha256(Path(office.__file__))
        # A text-only result from a restricted context must not prevent fresh
        # visual rendering when preparation later runs in a normal Terminal.
        identity["renderer_launch_restriction"] = office.macos_launch_restriction()
    return identity


def _relative(root: Path, value: str) -> Path:
    if not isinstance(value, str) or Path(value).is_absolute():
        raise ValueError("Invalid cache asset path")
    target = (root / value).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Cache asset escaped its entry")
    return target


def _read(entry: Path, key: str) -> dict | None:
    try:
        envelope = json.loads((entry / "result.json").read_text())
        if not isinstance(envelope, dict) or envelope.get("key") != key:
            return None
        data = envelope["data"]
        if not isinstance(data, dict) or envelope.get("data_sha256") != _json_hash(data):
            return None
        if not isinstance(data.get("chunks"), list) or not all(isinstance(c, dict) and isinstance(c.get("content"), str) for c in data["chunks"]):
            return None
        if not isinstance(data.get("visuals"), list) or not all(isinstance(v, dict) for v in data["visuals"]):
            return None
        if not isinstance(data.get("assets"), dict):
            return None
        for relative, spec in data["assets"].items():
            if not isinstance(spec, dict) or not isinstance(spec.get("name"), str) or Path(spec["name"]).name != spec["name"] or spec["name"] in {"", ".", ".."}:
                return None
            asset = _relative(entry, relative)
            if not asset.is_file() or file_sha256(asset) != spec.get("sha256"):
                return None
        if any(v.get("image_path") is not None and v["image_path"] not in data["assets"] for v in data["visuals"]):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _copy_verified(source: Path, target: Path, expected: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and file_sha256(target) == expected:
        return
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=".mynotes-extract-")
    os.close(descriptor)
    try:
        shutil.copyfile(source, name)
        if file_sha256(Path(name)) != expected:
            raise extraction.ExtractionError("Cached visual changed during copy; retry preparation")
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)


def _materialize(data: dict, entry: Path, source: Path, visual_dir: Path | None):
    chunks, visuals = copy.deepcopy(data["chunks"]), copy.deepcopy(data["visuals"])
    for visual in visuals:
        visual["source_file"] = str(source)
        asset = visual.get("image_path")
        if asset is not None:
            if visual_dir is None:
                raise extraction.ExtractionError("Visual cache options do not match extraction")
            spec = data["assets"][asset]
            target = visual_dir / spec["name"]
            _copy_verified(_relative(entry, asset), target, spec["sha256"])
            visual["image_path"] = str(target)
    return chunks, visuals


def extract_cached(source: Path, cache_dir: Path, *, visual_dir: Path | None = None, lesson_number: int | None = None):
    """Reuse content only after metadata and all visual bytes pass validation."""
    source = Path(source)
    options = {"lesson_number": lesson_number, "render_visuals": visual_dir is not None}
    identity = extractor_identity(source.suffix.casefold(), render_visuals=visual_dir is not None)
    source_hash = file_sha256(source)
    key = _json_hash({"source_sha256": source_hash, "suffix": source.suffix.casefold(), "identity": identity, "options": options})
    cache_dir.mkdir(parents=True, exist_ok=True)
    entry = cache_dir / key
    with (cache_dir / (key + ".lock")).open("a+") as lease:
        fcntl.flock(lease.fileno(), fcntl.LOCK_EX)
        if file_sha256(source) != source_hash or extractor_identity(source.suffix.casefold(), render_visuals=visual_dir is not None) != identity:
            raise extraction.ExtractionError("Source or extractor changed while waiting for extraction; retry preparation")
        cached = _read(entry, key)
        if cached is not None:
            if file_sha256(source) != source_hash:
                raise extraction.ExtractionError("Source changed during cache verification; retry preparation")
            result = _materialize(cached, entry, source, visual_dir)
            if file_sha256(source) != source_hash:
                raise extraction.ExtractionError("Source changed during cache materialization; retry preparation")
            return result
        with tempfile.TemporaryDirectory(dir=cache_dir, prefix=".extract-") as temporary:
            staging = Path(temporary)
            chunks, visuals = extraction.extract_material(source, visual_dir=staging / "visuals" if visual_dir is not None else None, lesson_number=lesson_number)
            if file_sha256(source) != source_hash or extractor_identity(source.suffix.casefold(), render_visuals=visual_dir is not None) != identity:
                raise extraction.ExtractionError("Source or extractor changed during extraction; retry preparation")
            data = {"chunks": chunks, "visuals": copy.deepcopy(visuals), "assets": {}}
            entry.mkdir(parents=True, exist_ok=True)
            for visual in data["visuals"]:
                image = visual.get("image_path")
                if image is not None:
                    asset = _relative(staging, str(Path(image).relative_to(staging)))
                    checksum = file_sha256(asset)
                    relative = "assets/" + checksum + "-" + asset.name
                    _copy_verified(asset, entry / relative, checksum)
                    data["assets"][relative] = {"sha256": checksum, "name": asset.name}
                    visual["image_path"] = relative
            # Do not make a temporary renderer failure a permanent cache hit.
            transient = visual_dir is not None and any(v.get("render_error") for v in visuals)
            if not transient:
                atomic_json_write(entry / "result.json", {"key": key, "data": data, "data_sha256": _json_hash(data)})
            result = _materialize(data, entry, source, visual_dir)
            if file_sha256(source) != source_hash:
                raise extraction.ExtractionError("Source changed during extraction materialization; retry preparation")
            return result
