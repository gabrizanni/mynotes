#!/usr/bin/env zsh
set -euo pipefail
setopt null_glob

release_root="$(cd "$(dirname "$0")/.." && pwd -P)"
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
if [[ -n "$rollback_version" ]]; then
  version_dir="$app_home/versions/$rollback_version"
  [[ -x "$version_dir/venv/bin/mynotes" ]] || { print -u2 "Installed MyNotes version not found: $rollback_version"; exit 2; }
  mkdir -p "$app_home"
  ln -sfn "$version_dir" "$app_home/current"
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
if [[ -e "$version_dir" ]]; then
  print -u2 "Version directory already exists; preserving it: $version_dir"
  exit 2
fi
if [[ -e "$launcher" ]] && ! grep -q "MyNotes managed launcher" "$launcher"; then
  print -u2 "Refusing to overwrite a launcher not created by MyNotes: $launcher"
  exit 2
fi

mkdir -p "$version_dir" "$prefix/bin" "$cache_dir" "$app_home/versions"
uv_flags=()
if (( offline )); then
  uv_flags=(--offline)
fi
requirements="$version_dir/requirements-runtime.txt"
UV_CACHE_DIR="$cache_dir" uv export --quiet --locked --no-dev --no-emit-project --output-file "$requirements" "${uv_flags[@]}"
UV_CACHE_DIR="$cache_dir" uv venv --quiet --python "$python_bin" "$version_dir/venv" "${uv_flags[@]}"
UV_CACHE_DIR="$cache_dir" uv pip install --quiet --python "$version_dir/venv/bin/python" --require-hashes --no-deps -r "$requirements" "${uv_flags[@]}"
UV_CACHE_DIR="$cache_dir" uv pip install --quiet --python "$version_dir/venv/bin/python" --no-deps "$wheel" "${uv_flags[@]}"

verification_workspace="$(mktemp -d "${TMPDIR:-/tmp}/mynotes-install.XXXXXX")"
install_complete=0
trap 'rm -rf "$verification_workspace"; if (( ! install_complete )); then rm -rf "$version_dir"; fi' EXIT
"$version_dir/venv/bin/mynotes" init --workspace "$verification_workspace" >/dev/null
"$version_dir/venv/bin/mynotes" doctor --workspace "$verification_workspace"

ln -sfn "$version_dir" "$app_home/current"
cat > "$launcher" <<EOF
#!/usr/bin/env zsh
# MyNotes managed launcher
exec "$app_home/current/venv/bin/mynotes" "\$@"
EOF
chmod 755 "$launcher"
install_complete=1
print "Installed MyNotes $version. Ensure $prefix/bin is on PATH."
print "Provider authentication stays external: use codex login or the Agy sign-in flow."
