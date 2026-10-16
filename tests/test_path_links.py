from __future__ import annotations

import ctypes
import sys
from pathlib import Path

import pytest

from mynotes import path_links
from mynotes.path_links import PathLinkError, resolve_course_directory


def test_directory_and_relative_symlink_preserve_sources(tmp_path: Path) -> None:
    folder = tmp_path / "Course à"
    folder.mkdir()
    source = folder / "source.txt"
    source.write_text("unchanged")
    shortcut = tmp_path / "shortcut"
    shortcut.symlink_to(folder.name, target_is_directory=True)
    assert resolve_course_directory(folder) == folder
    assert resolve_course_directory(shortcut) == folder
    assert shortcut.is_symlink() and source.read_text() == "unchanged"


def test_symlink_cycle_and_broken_target_are_actionable(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.symlink_to(second.name)
    second.symlink_to(first.name)
    with pytest.raises(PathLinkError, match="cycle"):
        resolve_course_directory(first)
    with pytest.raises(PathLinkError, match="Restore its target"):
        resolve_course_directory(tmp_path / "missing")


def test_bounded_chain(tmp_path: Path, monkeypatch) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    previous = folder
    for number in range(4):
        link = tmp_path / f"link-{number}"
        link.symlink_to(previous.name)
        previous = link
    monkeypatch.setattr(path_links, "_MAX_LINKS", 3)
    with pytest.raises(PathLinkError, match="chain is too long"):
        resolve_course_directory(previous)


def test_alias_cycle_and_mixed_link_chain(tmp_path: Path, monkeypatch) -> None:
    first, second = tmp_path / "one.alias", tmp_path / "two.alias"
    first.write_text("fake metadata fixture")
    second.write_text("fake metadata fixture")
    monkeypatch.setattr(path_links, "_finder_alias_target", lambda p: second if p == first else first)
    with pytest.raises(PathLinkError, match="cycle"):
        resolve_course_directory(first)
    folder = tmp_path / "folder"
    folder.mkdir()
    symlink = tmp_path / "symbolic"
    symlink.symlink_to(folder.name)
    monkeypatch.setattr(path_links, "_finder_alias_target", lambda p: symlink)
    assert resolve_course_directory(first) == folder


def test_non_macos_alias_reports_alternative(tmp_path: Path, monkeypatch) -> None:
    alias = tmp_path / "course.alias"
    alias.write_bytes(b"fixture")
    monkeypatch.setattr(path_links.sys, "platform", "linux")
    with pytest.raises(PathLinkError, match="Finder aliases require macOS"):
        resolve_course_directory(alias)


def _create_alias(target: Path, alias: Path) -> None:
    """Create a real Finder alias with public CF calls, only in a test fixture."""
    cf = path_links._core_foundation()
    create = cf.library.CFURLCreateBookmarkData
    create.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                       ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_void_p
    write = cf.library.CFURLWriteBookmarkDataToFile
    write.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong,
                      ctypes.POINTER(ctypes.c_void_p)]
    write.restype = ctypes.c_ubyte
    refs: list[int] = []
    try:
        target_url = cf.file_url(target, directory=target.is_dir())
        refs.append(target_url)
        alias_url = cf.file_url(alias)
        refs.append(alias_url)
        error = ctypes.c_void_p()
        data = create(None, target_url, 1 << 10, None, None, ctypes.byref(error))
        refs.extend(ref for ref in (data, error.value) if ref)
        assert data, "Failed to create fixture bookmark"
        error = ctypes.c_void_p()
        saved = write(data, alias_url, 0, ctypes.byref(error))
        if error.value:
            refs.append(error.value)
        assert saved, "Failed to create fixture Finder alias"
    finally:
        for ref in reversed(refs):
            cf.CFRelease(ref)


@pytest.mark.skipif(sys.platform != "darwin", reason="Native Finder alias requires CoreFoundation")
def test_native_finder_alias_unicode_renamed_target(tmp_path: Path) -> None:
    folder = tmp_path / "Course à & math"
    folder.mkdir()
    alias = tmp_path / "course link"
    _create_alias(folder, alias)
    original_alias = alias.read_bytes()
    assert resolve_course_directory(alias).samefile(folder)
    renamed = tmp_path / "renamed course"
    folder.rename(renamed)
    assert resolve_course_directory(alias) == renamed
    assert alias.read_bytes() == original_alias


@pytest.mark.skipif(sys.platform != "darwin", reason="Native Finder alias requires CoreFoundation")
def test_native_alias_to_file_or_missing_folder(tmp_path: Path) -> None:
    ordinary = tmp_path / "lecture.pdf"
    ordinary.write_bytes(b"test input")
    alias_file = tmp_path / "lecture alias"
    _create_alias(ordinary, alias_file)
    with pytest.raises(PathLinkError, match="not a directory"):
        resolve_course_directory(alias_file)
    folder = tmp_path / "to remove"
    folder.mkdir()
    alias_folder = tmp_path / "broken course"
    _create_alias(folder, alias_folder)
    folder.rmdir()
    with pytest.raises(PathLinkError, match="Restore the target|Restore its target"):
        resolve_course_directory(alias_folder)


@pytest.mark.skipif(sys.platform != "darwin", reason="Native metadata probe requires CoreFoundation")
def test_ordinary_sparse_pdf_never_reads_bookmark_data(tmp_path: Path, monkeypatch) -> None:
    pdf = tmp_path / "large.pdf"
    with pdf.open("wb") as output:
        output.truncate(1 << 30)
    cf = path_links._core_foundation()
    def forbidden(*args, **kwargs):
        raise AssertionError("ordinary source file content must not be read")
    monkeypatch.setattr(cf, "CFURLCreateBookmarkDataFromFile", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    with pytest.raises(PathLinkError, match="not a directory"):
        resolve_course_directory(pdf)
    assert pdf.stat().st_size == 1 << 30


@pytest.mark.skipif(sys.platform != "darwin", reason="Native CF ownership check requires macOS")
def test_native_no_ui_no_mount_and_owned_refs_released_on_success_and_failure(tmp_path, monkeypatch):
    from collections import Counter
    folder, alias = tmp_path / "folder", tmp_path / "alias"
    folder.mkdir()
    _create_alias(folder, alias)
    cf = path_links._core_foundation()
    acquired, released, flags = [], [], []
    pointer = ctypes.POINTER(ctypes.c_void_p)
    for name, error_position in (
        ("CFURLCreateFromFileSystemRepresentation", None),
        ("CFURLCreateBookmarkDataFromFile", 2),
        ("CFURLCreateByResolvingBookmarkData", 6),
    ):
        original = getattr(cf, name)
        def observe(*args, original=original, name=name, error_position=error_position):
            result = original(*args)
            if result:
                acquired.append(result)
            if error_position is not None:
                error = ctypes.cast(args[error_position], pointer).contents.value
                if error:
                    acquired.append(error)
            if name == "CFURLCreateByResolvingBookmarkData":
                flags.append(args[2])
            return result
        monkeypatch.setattr(cf, name, observe)
    original_property, original_release = cf.CFURLCopyResourcePropertyForKey, cf.CFRelease
    def observe_property(*args):
        result = original_property(*args)
        for index in (2, 3):
            value = ctypes.cast(args[index], pointer).contents.value
            if value:
                acquired.append(value)
        return result
    def observe_release(ref):
        released.append(ref)
        original_release(ref)
    monkeypatch.setattr(cf, "CFURLCopyResourcePropertyForKey", observe_property)
    monkeypatch.setattr(cf, "CFRelease", observe_release)
    assert resolve_course_directory(alias) == folder
    folder.rmdir()
    with pytest.raises(PathLinkError):
        resolve_course_directory(alias)
    assert Counter(acquired) == Counter(released)
    assert flags and all(value & 256 and value & 512 and value & 32768 for value in flags)


def test_invalid_path_is_actionable():
    with pytest.raises(PathLinkError, match="NUL character"):
        resolve_course_directory(Path("bad\0path"))
