# Scripts

- `check_public_tree.py` validates the explicit public release allow-list.
- `build_release.py` checks the lockfile, builds the wheel/sdist, validates the
  public files, and creates a versioned macOS release ZIP.
- `install-mynotes.sh` installs a release wheel into a versioned external
  application directory and creates a guarded user launcher.

These scripts must remain generic: no user-specific paths, credentials, or
temporary lesson-processing content belong here.
