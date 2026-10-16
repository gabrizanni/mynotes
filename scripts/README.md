# Scripts

- `check_public_tree.py` validates the explicit public release allowlist and scans for private references.
- `build_setup.py` compiles and ad-hoc signs the Apple Silicon SwiftUI onboarding app; the packaging machine needs Swift tools.
- `build_dmg.py` prepares the release inputs, bundles the setup app with the managed runtime installer and precompiled tools, and creates the Apple Silicon DMG candidate plus SHA-256 checksum. It does not publish to GitHub.
- `setup_runtime.py` runs inside the packaged app to bootstrap managed Python, locked dependencies, local transcription and Word tools; providers are installed and authenticated separately by the user.
- `build_release.py` checks the lockfile, builds wheel/sdist, validates public files and creates the versioned manual-install release ZIP.
- `install-mynotes.sh` installs a standalone release into versioned external directories and creates a guarded launcher; this advanced ZIP path requires Python and uv.
- `build_capture.py` compiles and signs the native recorder; `--native-only` builds the packaging machine's architecture.
- `test_capture_native.py` runs standalone native recorder/recovery and synthetic audio regressions.
- `capture_install_guard.py` protects updates and rollback with the same lease as recording/export.
- `validate_word_pipeline.py` verifies a synthetic Word pipeline separately from provider quality.
- `build_base_template.py` extracts only Word design definitions from an operator-supplied reference into a content-free template; the reference is excluded from distribution.

Keep scripts generic: no personal paths, credentials, calendar tokens or course-processing content. Packaging downloads must be versioned and verified, and must not read tools from a personal legacy project. [Evidence and remaining acceptance](../docs/VALIDATION.md).
