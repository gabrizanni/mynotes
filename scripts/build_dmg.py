#!/usr/bin/env python3
"""Build the unsigned/ad-hoc signed end-user DMG from pinned public components.

Builds Argmax from its locked upstream source, never from an installed recorder.
No private configuration or provider authentication is read or packaged.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path
from build_setup import build
ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def run(args: list[str], **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def download(url: str, path: Path, digest: str, *, offline: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and sha256(path) == digest: return path
    if offline: raise RuntimeError(f'Missing verified build cache: {path.name}')
    partial = path.with_suffix(path.suffix + '.part')
    run(['curl', '--fail', '--location', '--retry', '3', '--proto', '=https', '--proto-redir', '=https', '--output', str(partial), url])
    if sha256(partial) != digest: raise RuntimeError(f'Upstream checksum mismatch: {path.name}')
    os.replace(partial, path)
    return path


def argmax_binary(pin: dict, cache: Path, offline: bool) -> tuple[Path, list[Path], Path]:
    source = cache / ('argmax-' + pin['commit'])
    if not (source / '.git').is_dir():
        if offline: raise RuntimeError('Pinned Argmax source missing from build cache')
        run(['git', 'clone', '--depth', '1', '--branch', pin['tag'], pin['repository'], str(source)])
    actual = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if actual != pin['commit']: raise RuntimeError('Argmax source does not match pinned commit')
    shutil.copy2(ROOT / "setup/argmax.Package.resolved", source / "Package.resolved")
    # Pin transitive Swift packages as well as the upstream repository commit.
    scratch = cache / 'argmax-build'
    environment = dict(os.environ, CLANG_MODULE_CACHE_PATH=str(cache / 'clang-cache'), SWIFTPM_MODULECACHE_OVERRIDE=str(cache / 'swift-cache'))
    args = ['swift', 'build', '--package-path', str(source), '-c', 'release', '--product', 'argmax-cli', '--triple', 'arm64-apple-macosx14.0', '--scratch-path', str(scratch), '--disable-sandbox', '--disable-automatic-resolution']
    if offline: args += ['--skip-update']
    run(args, env=environment)
    directory = Path(subprocess.check_output(args + ['--show-bin-path'], env=environment, text=True).strip())
    return directory / 'argmax-cli', list(directory.glob('*.bundle')), source


def build_dmg(*, offline: bool = False, reuse_release: bool = False, cache: Path | None = None) -> Path:
    if sys.platform != 'darwin' or os.uname().machine != 'arm64': raise RuntimeError('DMG build requires an Apple Silicon Mac')
    cache = cache or Path(tempfile.gettempdir()) / 'mynotes-dmg-build'
    cache.mkdir(parents=True, exist_ok=True)
    lock = json.loads((ROOT / 'setup/components.lock.json').read_text())
    if not reuse_release:
        run([sys.executable, str(ROOT / 'scripts/build_release.py'), *(['--offline'] if offline else [])], cwd=ROOT)
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    archive = ROOT / f'dist/mynotes-{version}-macos-release.zip'
    if not archive.is_file(): raise RuntimeError('Build the release archive first')
    uv_pin = lock['build']['uv']
    uv_archive = download(uv_pin['url'], cache / 'uv.tar.gz', uv_pin['sha256'], offline=offline)
    uv_extract = cache / 'uv'
    uv_extract.mkdir(exist_ok=True)
    with tarfile.open(uv_archive) as source: source.extractall(uv_extract, filter='data')
    uv_candidates = list(uv_extract.rglob('uv'))
    uv_executable = next(path for path in uv_candidates if path.is_file())
    argmax, bundles, upstream = argmax_binary(lock['build']['argmax'], cache, offline)
    with tempfile.TemporaryDirectory(prefix='mynotes-dmg-', dir=cache) as temporary:
        work = Path(temporary)
        payload = work / 'release'; payload.mkdir()
        with zipfile.ZipFile(archive) as zipped: zipped.extractall(payload)
        # Preserve executability of the extracted native recorder.
        (payload / 'capture/output/MyNotes Capture.app/Contents/MacOS/MyNotesCapture').chmod(0o755)
        (payload / 'bin').mkdir()
        for name, source in [('uv', uv_executable), ('argmax-cli', argmax)]:
            target = payload / 'bin' / name; shutil.copy2(source, target); target.chmod(0o755)
            run(['codesign', '--force', '--sign', '-', str(target)])
        for bundle in bundles: shutil.copytree(bundle, payload / 'bin' / bundle.name)
        licenses = payload / 'third-party/argmax'; licenses.mkdir(parents=True)
        # SwiftPM dependencies and bundled tools retain their upstream licenses.
        uv_licenses = payload / 'third-party/uv'; uv_licenses.mkdir(parents=True)
        for name, digest in lock['build']['uv']['licenses'].items():
            cached_license = download('https://raw.githubusercontent.com/astral-sh/uv/0.12.9/' + name, cache / 'uv-licenses' / name, digest, offline=offline)
            shutil.copy2(cached_license, uv_licenses / name)
        checkouts = cache / 'argmax-build/checkouts'
        for checkout in checkouts.iterdir() if checkouts.is_dir() else []:
            for path in checkout.iterdir():
                if path.is_file() and path.name.upper().startswith(('LICENSE', 'NOTICE')):
                    destination = licenses / checkout.name / path.name
                    destination.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(path, destination)
        for filename in ['LICENSE', 'NOTICE', 'NOTICES', 'Package.resolved']:
            if (upstream / filename).is_file(): shutil.copy2(upstream / filename, licenses / filename)
        (payload / 'setup-components.json').write_text(json.dumps({key:value for key,value in lock.items() if key != 'build'}, indent=2) + '\n')
        manifest = json.loads((payload / 'release-manifest.json').read_text())
        # Extend integrity verification to precompiled tools, model pins and resources.
        for path in payload.rglob('*'):
            if path.is_file() and path.name != 'release-manifest.json': manifest['files'][str(path.relative_to(payload))] = sha256(path)
        (payload / 'release-manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        app = build(payload)
        staging = work / 'volume'; staging.mkdir()
        run(['ditto', str(app), str(staging / 'MyNotes.app')])
        (staging / 'Applications').symlink_to('/Applications')
        shutil.copytree(payload / 'docs', staging / 'docs')
        # The app's Guide button and the DMG readme share one offline manual.
        shutil.copy2(payload / 'docs/Guida.html', staging / 'Leggimi.html')
        result = ROOT / f'dist/MyNotes-{version}-macOS-arm64.dmg'
        result.unlink(missing_ok=True)
        run(['hdiutil', 'create', '-volname', 'MyNotes', '-srcfolder', str(staging), '-format', 'UDZO', '-ov', str(result)])
        run(['hdiutil', 'verify', str(result)])
        result.with_suffix('.dmg.sha256').write_text(f'{sha256(result)}  {result.name}\n')
        print(result)
        return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--reuse-release', action='store_true', help='reuse a freshly built release ZIP')
    parser.add_argument('--cache', type=Path)
    args = parser.parse_args()
    build_dmg(offline=args.offline, reuse_release=args.reuse_release, cache=args.cache)
