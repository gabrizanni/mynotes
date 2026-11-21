#!/usr/bin/env python3
"""Keep only the selected MyNotes apps discoverable; never alter app bundles."""
from __future__ import annotations
import argparse
import json
import plistlib
import re
import subprocess
from pathlib import Path

LSREGISTER = Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')
MAIN = 'com.gabrizanni.mynotes'
CAPTURE = 'com.gabrizanni.mynotes-capture'


def registered_apps(dump: str):
    path = None
    for line in dump.splitlines():
        if line.startswith('path:'):
            path = re.sub(r'\s+\(0x[0-9a-fA-F]+\)$', '', line.split(':', 1)[1].strip())
        elif line.startswith('identifier:') and path:
            yield Path(path), line.split(':', 1)[1].strip()
            path = None


def canonical_apps(app_home: Path, main_app: Path | None):
    result = {}
    for identifier, path in [(CAPTURE, app_home / 'current/MyNotes Capture.app'), (MAIN, main_app)]:
        if path is None:
            continue
        try:
            info = plistlib.loads((path / 'Contents/Info.plist').read_bytes())
        except (OSError, plistlib.InvalidFileException):
            continue
        if info.get('CFBundleIdentifier') == identifier:
            result[identifier] = path.resolve()
    return result


def cleanup(app_home: Path, main_app: Path | None, *, registry=LSREGISTER, run=subprocess.run):
    if not registry.is_file():
        return {'skipped': True, 'removed': 0}
    selected = canonical_apps(app_home, main_app)
    if not selected:
        return {"skipped": False, "removed": 0, "selected": 0}
    def snapshot():
        return list(registered_apps(run([str(registry), '-dump'], capture_output=True, text=True, check=True).stdout))
    removed = 0
    for path, identifier in snapshot():
        if identifier in selected and path != selected[identifier]:
            # Deleted paths and symlink aliases can also leave stale records.
            run([str(registry), '-u', str(path)], capture_output=True, text=True)
            removed += 1
    for path in selected.values():
        run([str(registry), '-f', str(path)], capture_output=True, text=True, check=True)
    remaining = [(p, i) for p, i in snapshot() if i in selected and p != selected[i]]
    if remaining:
        raise RuntimeError('Some obsolete MyNotes launch entries could not be removed')
    return {'skipped': False, 'removed': removed, 'selected': len(selected)}


def refresh():
    home = Path.home()
    main = home / 'Applications/MyNotes.app'
    if not main.is_dir():
        main = Path('/Applications/MyNotes.app')
    return cleanup(home / 'Library/Application Support/MyNotes/app', main)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    default = Path.home() / 'Library/Application Support/MyNotes/app'
    parser.add_argument('--app-home', type=Path, default=default)
    args = parser.parse_args()
    # Isolated/custom installations must not modify the user's launch registry.
    if args.app_home.resolve() != default.resolve():
        print(json.dumps({'skipped': True, 'reason': 'custom app home'}))
    else:
        print(json.dumps(refresh()))


if __name__ == '__main__':
    main()
