#!/usr/bin/env zsh
set -euo pipefail
setopt null_glob
original_args=("$@")

release_root="$(cd "$(dirname "$0")/.." && pwd -P)"
# A DMG carries its own uv; source/ZIP installs can continue using PATH.
if [[ -x "$release_root/bin/uv" ]]; then export PATH="$release_root/bin:$PATH"; fi
prefix="${MYNOTES_PREFIX:-$HOME/.local}"
app_home="${MYNOTES_APP_HOME:-$HOME/Library/Application Support/MyNotes/app}"
python_bin="${MYNOTES_PYTHON:-python3}"
offline=0
dry_run=0
rollback_version=""
list_versions=0

usage() {
  print "Usage: zsh scripts/install-mynotes.sh [--prefix PATH] [--app-home PATH] [--python PATH] [--offline] [--dry-run] [--rollback VERSION] [--list-versions]"
}

while (( $# )); do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --app-home) app_home="$2"; shift 2 ;;
    --python) python_bin="$2"; shift 2 ;;
    --offline) offline=1; shift ;;
    --dry-run) dry_run=1; shift ;;
    --rollback) rollback_version="$2"; shift 2 ;;
    --list-versions) list_versions=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) print -u2 "Unknown option: $1"; usage; exit 2 ;;
  esac
done

if (( list_versions )); then
  versions=("$app_home"/versions/*(/N))
  for directory in $versions; do print "${directory:t}"; done
  exit 0
fi
capture_install_guard() {
  if [[ "${MYNOTES_INSTALL_CAPTURE_GUARD:-0}" != "1" ]]; then
    exec "$python_bin" "$release_root/scripts/capture_install_guard.py" zsh "$release_root/scripts/install-mynotes.sh" "${original_args[@]}"
  fi
}

refresh_capture_python() {
  "$python_bin" - "$app_home/current/venv/bin/mynotes" <<'PY_CAPTURE'
import json, os, subprocess, sys
from pathlib import Path
home = Path(os.environ.get("MYNOTES_CAPTURE_HOME", str(Path.home() / "Library/Application Support/MyNotes/Capture")))
active = home / "active.json"
if active.is_file():
    workspace = json.loads(active.read_text())["workspace"]
    subprocess.run([sys.argv[1], "capture", "configure", "--workspace", workspace], check=True)
PY_CAPTURE
}

restore_managed_tools() {
  "$python_bin" - "$app_home/current/managed-tools.json" <<'PY_TOOLS'
import json, os, sys, tempfile
from pathlib import Path
source = Path(sys.argv[1])
if source.is_file():
    tools = json.loads(source.read_text())
    root = Path(os.environ.get("MYNOTES_HOME", str(Path.home() / "Library/Application Support/MyNotes")))
    root.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=root, prefix=".managed-tools-")
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(tools, handle)
        os.replace(name, root / "tools.json")
    finally:
        Path(name).unlink(missing_ok=True)
PY_TOOLS
}


write_launcher() {
  "$python_bin" - "$prefix/bin/mynotes" "$app_home" "${MYNOTES_HOME:-$HOME/Library/Application Support/MyNotes}" "$@" <<'PY_LAUNCHER'
import os, shlex, sys, tempfile
from pathlib import Path
launcher = Path(sys.argv[1])
application = Path(sys.argv[2])
runtime = sys.argv[3]
if launcher.exists() or launcher.is_symlink():
    try:
        managed = "# MyNotes managed launcher" in launcher.read_text()
    except (OSError, UnicodeError):
        managed = False
    if not managed:
        raise SystemExit("Refusing to overwrite a launcher not created by MyNotes: " + str(launcher))
if "--check" in sys.argv[4:]:
    raise SystemExit(0)
launcher.parent.mkdir(parents=True, exist_ok=True)
python = shlex.quote(str(application / "current/venv/bin/python"))
command = shlex.quote(str(application / "current/venv/bin/mynotes"))
read_workspace = shlex.quote('import json,os;print(json.load(open(os.path.join(os.environ["MYNOTES_HOME"],"setup.json")))["workspace"])')
content = ("#!/usr/bin/env zsh\n# MyNotes managed launcher\n"
           + "if [[ -z \"${MYNOTES_HOME:-}\" ]]; then export MYNOTES_HOME=" + shlex.quote(runtime) + "; fi\n"
           + "if [[ -z \"${MYNOTES_WORKSPACE:-}\" && -f \"$MYNOTES_HOME/setup.json\" ]]; then\n"
           + "  export MYNOTES_WORKSPACE=\"$(" + python + " -c " + read_workspace + ")\"\nfi\n"
           + "exec " + command + " \"$@\"\n")
descriptor, temporary = tempfile.mkstemp(dir=launcher.parent, prefix=".mynotes-launcher-")
try:
    with os.fdopen(descriptor, "w") as handle:
        handle.write(content)
    os.chmod(temporary, 0o755)
    os.replace(temporary, launcher)
finally:
    Path(temporary).unlink(missing_ok=True)
PY_LAUNCHER
}

if [[ -n "$rollback_version" ]]; then
  if [[ "$rollback_version" == *[^A-Za-z0-9._-]* || "$rollback_version" == "." || "$rollback_version" == ".." ]]; then
    print -u2 "Invalid rollback version"; exit 2
  fi
  version_dir="$app_home/versions/$rollback_version"
  [[ -x "$version_dir/venv/bin/mynotes" ]] || { print -u2 "Installed MyNotes version not found: $rollback_version"; exit 2; }
  mkdir -p "$app_home"
  if [[ -f "${MYNOTES_CAPTURE_HOME:-$HOME/Library/Application Support/MyNotes/Capture}/active.json" && ! -x "$version_dir/MyNotes Capture.app/Contents/MacOS/MyNotesCapture" ]]; then
    print -u2 "Disable Capture before rolling back to a version without the recorder."
    exit 2
  fi
  write_launcher --check
  capture_install_guard
  previous_current="$(readlink "$app_home/current" || true)"
  ln -sfn "$version_dir" "$app_home/current"
  if ! refresh_capture_python || ! write_launcher || ! restore_managed_tools; then
    if [[ -n "$previous_current" ]]; then ln -sfn "$previous_current" "$app_home/current"; else rm -f "$app_home/current"; fi
    print -u2 "Rollback configuration failed; previous application selection restored."
    exit 2
  fi
  print "Rolled back MyNotes launcher to $rollback_version. Private workspaces were not changed."
  exit 0
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  print -u2 "MyNotes release installer supports macOS only."
  exit 2
fi
architecture="$(uname -m)"
if [[ "$architecture" != "arm64" && "$architecture" != "x86_64" ]]; then
  print -u2 "Unsupported macOS architecture: $architecture"
  exit 2
fi
if ! command -v uv >/dev/null 2>&1; then
  print -u2 "uv is required. Install uv first, then rerun this script."
  exit 2
fi
if ! command -v "$python_bin" >/dev/null 2>&1; then
  print -u2 "Python executable not found: $python_bin"
  exit 2
fi
if ! "$python_bin" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
  print -u2 "MyNotes requires Python 3.12 or newer."
  exit 2
fi

wheels=("$release_root"/dist/mynotes-*.whl)
if (( ${#wheels} != 1 )); then
  print -u2 "Expected exactly one release wheel in $release_root/dist."
  exit 2
fi
wheel="$wheels[1]"
if [[ ! -f "$release_root/uv.lock" || ! -f "$release_root/release-manifest.json" ]]; then
  print -u2 "This directory is not a standalone MyNotes release archive."
  print -u2 "For development / source checkout, run: uv sync"
  print -u2 "To install from a release, download the packaged release ZIP from GitHub Releases."
  exit 2
fi

version="$("$python_bin" - "$release_root/pyproject.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], 'rb') as source:
    print(tomllib.load(source)['project']['version'])
PY
)"
version_dir="$app_home/versions/$version"
launcher="$prefix/bin/mynotes"
cache_dir="${UV_CACHE_DIR:-$HOME/Library/Caches/MyNotes/uv}"

print "MyNotes $version for macOS $architecture"
print "release wheel: $wheel ($(du -h "$wheel" | awk '{print $1}'))"
print "application destination: $version_dir"
print "launcher destination: $launcher"
print "uv cache destination: $cache_dir"
print "AI/speech model downloads: none. Provider tools manage their own model downloads."

if (( dry_run )); then
  print "Dry run complete; no files were installed."
  exit 0
fi
capture_install_guard

# Verify every packaged file, including the native executable, before installation.
"$python_bin" - "$release_root" <<'PY_MANIFEST'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
manifest = json.loads((root / "release-manifest.json").read_text())
for relative, expected in manifest["files"].items():
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit("Release integrity check failed: " + relative)
if "capture/output/MyNotes Capture.app/Contents/MacOS/MyNotesCapture" not in manifest["files"]:
    raise SystemExit("Release manifest does not include MyNotes Capture")
PY_MANIFEST

if [[ -e "$version_dir" ]]; then
  print -u2 "Version directory already exists; preserving it: $version_dir"
  exit 2
fi
if [[ -e "$launcher" ]] && ! grep -q "MyNotes managed launcher" "$launcher"; then
  print -u2 "Refusing to overwrite a launcher not created by MyNotes: $launcher"
  exit 2
fi

verification_workspace="$(mktemp -d "${TMPDIR:-/tmp}/mynotes-install.XXXXXX")"
install_complete=0
application_selected=0
previous_current="$(readlink "$app_home/current" || true)"
if [[ -f "$launcher" ]]; then cp "$launcher" "$verification_workspace/previous-launcher"; fi
cleanup_install() {
  if (( ! install_complete )); then
    if (( application_selected )); then
      if [[ -n "$previous_current" ]]; then ln -sfn "$previous_current" "$app_home/current"; else rm -f "$app_home/current"; fi
      if [[ -f "$verification_workspace/previous-launcher" ]]; then
        cp "$verification_workspace/previous-launcher" "$launcher"
      else
        rm -f "$launcher"
      fi
    fi
    rm -rf "$version_dir"
  fi
  rm -rf "$verification_workspace"
}
trap cleanup_install EXIT
mkdir -p "$version_dir" "$prefix/bin" "$cache_dir" "$app_home/versions"
uv_flags=()
if (( offline )); then
  uv_flags=(--offline)
fi
requirements="$version_dir/requirements-runtime.txt"
UV_CACHE_DIR="$cache_dir" uv export --project "$release_root" --quiet --locked --no-dev --no-emit-project --output-file "$requirements" "${uv_flags[@]}"
UV_CACHE_DIR="$cache_dir" uv venv --quiet --python "$python_bin" "$version_dir/venv" "${uv_flags[@]}"
UV_CACHE_DIR="$cache_dir" uv pip install --quiet --python "$version_dir/venv/bin/python" --require-hashes --no-deps -r "$requirements" "${uv_flags[@]}"
UV_CACHE_DIR="$cache_dir" uv pip install --quiet --python "$version_dir/venv/bin/python" --no-deps "$wheel" "${uv_flags[@]}"

ditto "$release_root/capture/output/MyNotes Capture.app" "$version_dir/MyNotes Capture.app"
chmod 755 "$version_dir/MyNotes Capture.app/Contents/MacOS/MyNotesCapture"
codesign --verify --strict "$version_dir/MyNotes Capture.app"
mkdir -p "$version_dir/docs"
cp "$release_root/docs/CAPTURE.md" "$version_dir/docs/CAPTURE.md"
cp "$release_root/docs/GENERATION.md" "$version_dir/docs/GENERATION.md"

"$version_dir/venv/bin/mynotes" init --workspace "$verification_workspace" >/dev/null
"$version_dir/venv/bin/mynotes" doctor --workspace "$verification_workspace"

"$python_bin" - "$wheel" "$version_dir/installation.json" <<'PY_RECEIPT'
import hashlib, json, sys
from pathlib import Path
wheel = Path(sys.argv[1])
Path(sys.argv[2]).write_text(json.dumps({"schema_version": 1, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}) + "\n")
PY_RECEIPT
ln -sfn "$version_dir" "$app_home/current"
application_selected=1
write_launcher
refresh_capture_python
install_complete=1
print "Installed MyNotes $version. Ensure $prefix/bin is on PATH."
print "Provider authentication stays external: use codex login or the Agy sign-in flow."
