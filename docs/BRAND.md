# MyNotes visual identity / Identità visiva

MyNotes uses one open notebook with a coral bookmark, on a deep blue tile.
The same image identifies the application in Finder and the Dock, the setup
header and the offline guide. Capture remains the name of the included recorder.

MyNotes usa un quaderno aperto con segnalibro corallo su fondo blu profondo.
La stessa immagine identifica app, setup e guida. Capture è il registratore incluso.

| Role / Uso | Color / Colore |
| --- | --- |
| Primary blue / Blu principale | `#163B66` |
| Paper / Carta | `#FFF8ED` |
| Decorative accent / Accento decorativo | `#F47B62` |
| Links on dark backgrounds / Link su sfondo scuro | `#8CC8FF` |

Keep system typography and familiar macOS controls. Coral is decorative,
never the sole indicator of a state or small body text on white. Error and
success states retain their native semantic colors. Both light and dark
appearance must remain readable. No new chat interface or heavy redesign.

Usare caratteri di sistema e controlli macOS familiari. Il corallo è decorativo;
non identifica da solo uno stato e non si usa per testo piccolo su bianco.
Errori e successi conservano i colori di sistema. Mantenere la leggibilità
anche in modalità scura e la chat nel Terminale.

Canonical assets: `setup/Resources/MyNotesLogo.png` (1024 px, alpha),
`MyNotesGuideLogo.png` (192 px, embedded into the self-contained HTML guide)
and `MyNotes.icns` (16–1024 px representations). Generated artwork is resized
without changing its design. PNG files have no ancillary metadata; the ICNS
contains image representations and only the standard iconutil metadata.
The public-tree checker pins the reviewed asset bytes.

## Capture

MyNotes Capture has a matching deep-blue rounded tile with an ivory microphone
and a coral waveform. The generated PNG master is
`capture/Resources/MyNotesCaptureLogo.png` (1024 px with external transparency);
`MyNotesCapture.icns` includes 16–1024 px representations. The native build copies
both assets into the app bundle, and `CFBundleIconFile` selects the ICNS.
The menu-bar microphone and REC indication retain their existing functional roles.
This source integration does not update an installed app or release artifact.
