#!/usr/bin/env python3
"""Build the Apple Silicon setup application with a standalone release payload."""
from __future__ import annotations
import argparse
import os
import plistlib
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def build(payload: Path) -> Path:
    scratch = Path(tempfile.gettempdir()) / 'mynotes-setup-build'
    env = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(scratch / 'clang-cache'), SWIFTPM_MODULECACHE_OVERRIDE=str(scratch / 'swift-cache'))
    args = ['swift', 'build', '--package-path', str(ROOT / 'setup'), '-c', 'release', '--product', 'MyNotesSetup', '--triple', 'arm64-apple-macosx14.0', '--scratch-path', str(scratch / 'build'), '--disable-sandbox']
    subprocess.run(args, env=env, check=True)
    binary_root = Path(subprocess.check_output(args + ['--show-bin-path'], env=env, text=True).strip())
    destination = ROOT / 'setup/output/MyNotes.app'
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='mynotes-app-', dir=destination.parent))
    app = temp / 'MyNotes.app'
    try:
        (app / 'Contents/MacOS').mkdir(parents=True)
        shutil.copy2(binary_root / 'MyNotesSetup', app / 'Contents/MacOS/MyNotesSetup')
        info = plistlib.loads((ROOT / 'setup/Resources/Info.plist').read_bytes())
        info['CFBundleShortVersionString'] = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
        info['CFBundleVersion'] = info['CFBundleShortVersionString']
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
        subprocess.run(['ditto', str(payload), str(app / 'Contents/Resources/release')], check=True)
        for name in ('MyNotes.icns', 'MyNotesLogo.png'):
            shutil.copy2(ROOT / 'setup/Resources' / name, app / 'Contents/Resources' / name)
        shutil.copy2(ROOT / 'capture/Resources/MyNotesCaptureLogo.png', app / 'Contents/Resources/MyNotesCaptureLogo.png')
        subprocess.run(['codesign', '--force', '--sign', '-', '--identifier', info['CFBundleIdentifier'], str(app)], check=True)
        subprocess.run(['codesign', '--verify', '--strict', str(app)], check=True)
        if destination.exists(): shutil.rmtree(destination)
        shutil.move(str(app), destination)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return destination

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    print(build(parser.parse_args().payload))
