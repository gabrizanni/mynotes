# Scripts

- `check_public_tree.py` validates the explicit public release allow-list.
- `build_base_template.py` extracts only Word design definitions from an operator-
  supplied local reference into a content-free template; the reference is not
  included in the application or release archive.
- `build_release.py` checks the lockfile, builds the wheel/sdist, validates the
  public files, and creates a versioned macOS release ZIP.
- `install-mynotes.sh` installs a release wheel into a versioned external
  application directory and creates a guarded user launcher.

These scripts must remain generic: no user-specific paths, credentials, or
temporary lesson-processing content belong here.

## Rebuild the neutral Word template

```sh
uv run python scripts/build_base_template.py reference.docx src/mynotes/resources/base-template.docx
uv run python scripts/check_public_tree.py
```

Use a local, single-section Word design reference with native Heading 1/2/3.
The builder copies only style, numbering, theme and page geometry definitions;
body text, images, document properties and the source path are omitted.
It applies the current study design: margins 1.5 cm on all sides, Heading 1/2/3
at 16/14/12 pt in blue, and the visible orange bold character style
`Termine tecnico`. The publication renderer applies Aptos 11 body text and uses
Heading 1 for the lesson, Heading 2 for sections, Heading 3 for subsections,
and the character style for terms without direct formatting overrides.
Never add the local reference or generated handouts to the public repository.
