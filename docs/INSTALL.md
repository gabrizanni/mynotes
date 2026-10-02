# Installation and troubleshooting

Run the installer from an unpacked, versioned MyNotes release:

```sh
zsh scripts/install-mynotes.sh
```

Options are `--prefix PATH`, `--app-home PATH`, `--python PATH`, `--offline`,
and `--dry-run`. `--dry-run` validates the release and prints destinations
without installing. `--offline` requires all locked packages to be available in
the configured uv cache.

The installer requires macOS and Python 3.12+. It refuses to overwrite a
launcher it did not create. It uses the release wheel plus the frozen `uv.lock`
to create an isolated environment outside the release directory, then runs
`mynotes doctor`.

If `mynotes` is not found, add the selected prefix's `bin` directory to `PATH`:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer neither authenticates providers nor downloads speech/AI models.
Authenticate Codex or Agy with their own official application/CLI. If a
provider is unavailable, MyNotes records that fact rather than switching to a
different provider.
