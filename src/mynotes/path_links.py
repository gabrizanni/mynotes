"""Resolve a user-selected folder, symlink or Finder alias without UI or writes.

Uses public CoreFoundation bookmark APIs only on macOS. Ordinary files are
probed through URL metadata before reading any bookmark data.
"""
from __future__ import annotations

import ctypes
import os
import stat
import sys
from functools import lru_cache
from pathlib import Path


class PathLinkError(ValueError):
    """A selected course link needs repair or a different local directory."""


_MAX_LINKS = 32
# CFURL.h public resolution flags. Never mount or implicitly grant access.
_WITHOUT_UI = 1 << 8
_WITHOUT_MOUNTING = 1 << 9
_WITHOUT_IMPLICIT_ACCESS = 1 << 15


class _CoreFoundation:
    def __init__(self) -> None:
        self.library = ctypes.CDLL(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        pointer = ctypes.c_void_p
        pointer_out = ctypes.POINTER(pointer)
        byte = ctypes.c_ubyte  # CoreFoundation Boolean is an unsigned byte.
        index = ctypes.c_long
        signatures = {
            "CFRelease": ([pointer], None),
            "CFURLCreateFromFileSystemRepresentation": (
                [pointer, ctypes.c_char_p, index, byte], pointer
            ),
            "CFURLCopyResourcePropertyForKey": (
                [pointer, pointer, pointer_out, pointer_out], byte
            ),
            "CFBooleanGetValue": ([pointer], byte),
            "CFGetTypeID": ([pointer], ctypes.c_ulong),
            "CFBooleanGetTypeID": ([], ctypes.c_ulong),
            "CFURLCreateBookmarkDataFromFile": ([pointer, pointer, pointer_out], pointer),
            "CFURLCreateByResolvingBookmarkData": (
                [pointer, pointer, ctypes.c_ulong, pointer, pointer,
                 ctypes.POINTER(byte), pointer_out], pointer
            ),
            "CFURLGetFileSystemRepresentation": (
                [pointer, byte, pointer, index], byte
            ),
            "CFErrorGetCode": ([pointer], index),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.library, name)
            function.argtypes, function.restype = arguments, result
            setattr(self, name, function)
        # Exported resource key is borrowed, not a Create/Copy return value.
        self.alias_key = pointer.in_dll(self.library, "kCFURLIsAliasFileKey").value

    def file_url(self, path: Path, *, directory: bool = False) -> int:
        encoded = os.fsencode(path)
        if b"\0" in encoded:
            raise PathLinkError("A course path cannot contain a NUL character.")
        result = self.CFURLCreateFromFileSystemRepresentation(
            None, encoded, len(encoded), directory
        )
        if not result:
            raise PathLinkError(f"Cannot represent course path: {path}. Choose its local folder.")
        return result

    def alias_target(self, path: Path) -> Path | None:
        owned: list[int] = []
        try:
            url = self.file_url(path)
            owned.append(url)
            value, error = ctypes.c_void_p(), ctypes.c_void_p()
            copied = self.CFURLCopyResourcePropertyForKey(
                url, self.alias_key, ctypes.byref(value), ctypes.byref(error)
            )
            owned.extend(reference for reference in (value.value, error.value) if reference)
            if not copied:
                raise self.failure("Cannot inspect course link", path, error.value)
            if not value.value:
                return None
            if self.CFGetTypeID(value.value) != self.CFBooleanGetTypeID():
                raise self.failure("Unrecognized course link metadata", path, None)
            if not self.CFBooleanGetValue(value.value):
                return None
            error = ctypes.c_void_p()
            bookmark = self.CFURLCreateBookmarkDataFromFile(None, url, ctypes.byref(error))
            owned.extend(reference for reference in (bookmark, error.value) if reference)
            if not bookmark:
                raise self.failure("Cannot read Finder alias", path, error.value)
            error, stale = ctypes.c_void_p(), ctypes.c_ubyte()
            resolved = self.CFURLCreateByResolvingBookmarkData(
                None, bookmark,
                _WITHOUT_UI | _WITHOUT_MOUNTING | _WITHOUT_IMPLICIT_ACCESS,
                None, None, ctypes.byref(stale), ctypes.byref(error),
            )
            owned.extend(reference for reference in (resolved, error.value) if reference)
            if not resolved:
                raise self.failure("Cannot resolve Finder alias", path, error.value)
            # Stale but successfully resolved bookmarks are usable; do not rewrite them.
            for capacity in (4096, 16384, 65536):
                buffer = ctypes.create_string_buffer(capacity)
                if self.CFURLGetFileSystemRepresentation(resolved, True, buffer, capacity):
                    return Path(os.fsdecode(buffer.value))
            raise self.failure("Finder alias target path is too long", path, None)
        finally:
            for reference in reversed(owned):
                self.CFRelease(reference)

    def failure(self, reason: str, path: Path, error: int | None) -> PathLinkError:
        code = f" (macOS error {self.CFErrorGetCode(error)})" if error else ""
        return PathLinkError(
            f"{reason}{code}: {path}. Restore the target, connect its volume yourself, "
            "or choose the actual accessible course folder. MyNotes will not open dialogs or mount volumes."
        )


@lru_cache(maxsize=1)
def _core_foundation() -> _CoreFoundation:
    try:
        return _CoreFoundation()
    except (OSError, AttributeError, ValueError) as error:
        raise PathLinkError(
            "Finder alias resolution is unavailable. Choose the actual course folder or a symbolic link."
        ) from error


def _finder_alias_target(path: Path) -> Path | None:
    if sys.platform != "darwin":
        raise PathLinkError(
            f"Course path is not a directory: {path}. Finder aliases require macOS; "
            "choose the actual course folder or a symbolic link."
        )
    return _core_foundation().alias_target(path)


def resolve_course_directory(path: Path) -> Path:
    """Resolve only the chosen link chain; require an existing directory target.

    Does not discover files, modify the link, request permissions or mount a disk.
    Mixed alias/symlink chains are bounded and tracked by path and inode.
    """
    try:
        current = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    except (OSError, TypeError, ValueError, RuntimeError) as error:
        raise PathLinkError("Invalid course path. Choose an accessible local folder or its shortcut.") from error
    if "\0" in str(current):
        raise PathLinkError("A course path cannot contain a NUL character. Choose its local folder.")
    seen_paths: set[Path] = set()
    seen_objects: set[tuple[int, int]] = set()
    try:
        for _ in range(_MAX_LINKS):
            info = current.lstat()
            identity = (info.st_dev, info.st_ino)
            if current in seen_paths or identity in seen_objects:
                raise PathLinkError(f"Course link cycle: {current}. Choose the actual course folder.")
            seen_paths.add(current)
            seen_objects.add(identity)
            if stat.S_ISLNK(info.st_mode):
                target = current.readlink()
                current = Path(os.path.abspath(target if target.is_absolute() else current.parent / target))
                continue
            if stat.S_ISDIR(info.st_mode):
                return current.resolve(strict=True)
            if stat.S_ISREG(info.st_mode):
                target = _finder_alias_target(current)
                if target is not None:
                    current = Path(os.path.abspath(target))
                    continue
            raise PathLinkError(f"Course path is not a directory: {current}. Choose a folder or its alias.")
    except (OSError, RuntimeError) as error:
        raise PathLinkError(
            f"Course link is missing, inaccessible or broken: {current}. "
            "Restore its target or choose the actual accessible course folder."
        ) from error
    raise PathLinkError(f"Course link chain is too long: {path}. Choose the actual course folder.")
