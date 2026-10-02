# Installazione e problemi comuni

Da una release MyNotes estratta esegui `zsh scripts/install-mynotes.sh`.
Opzioni: `--prefix PERCORSO`, `--app-home PERCORSO`, `--python PERCORSO`,
`--offline`, `--dry-run`. Il dry-run controlla release e destinazioni senza
installare; offline richiede pacchetti già presenti nella cache uv.

Sono richiesti macOS e Python 3.12+. L’installer non sovrascrive un launcher
non creato da MyNotes, usa wheel e `uv.lock` congelato, crea un ambiente fuori
dalla release e termina con `mynotes doctor`.

Se `mynotes` non viene trovato: `export PATH="$HOME/.local/bin:$PATH"`.
L’installer non autentica provider né scarica modelli vocali/AI: usa gli
strumenti ufficiali di Codex o Agy. Un provider non disponibile non viene mai
sostituito silenziosamente.
