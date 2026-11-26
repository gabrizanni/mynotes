# Bundled fonts

All three families are under the SIL Open Font License 1.1 (license text next to each file).
None declares a Reserved Font Name, so the subset files below may keep their names.

| File | Source (official OFL release) | Processing |
| --- | --- | --- |
| `eb-garamond/EBGaramond-Medium.woff2` | `google/fonts` → `ofl/ebgaramond/EBGaramond[wght].ttf` (EB Garamond Project Authors) | static instance at weight 500 |
| `figtree/Figtree-Variable.woff2` | `google/fonts` → `ofl/figtree/Figtree[wght].ttf` (Figtree Project Authors) | variable, weight axis limited to 400–600 |
| `jetbrains-mono/JetBrainsMono-Regular.woff2` | `google/fonts` → `ofl/jetbrainsmono/JetBrainsMono[wght].ttf` (JetBrains Mono Project Authors) | static instance at weight 400 |

Every file is subset to Latin, Latin-1 and the punctuation the UI uses (U+0000–00FF, U+0131,
U+0152–0153, U+02BB–02BC, U+02C6, U+02DA, U+02DC, U+2000–206F, U+2074, U+20AC, U+2122,
U+2190–2199, U+2212, U+2215, U+2713), with `kern`, `liga`, `calt`, `tnum`, `lnum`, `pnum`,
`case`, `ccmp`, `locl`, `mark` and `mkmk` kept, using fontTools 4.60.1
(`varLib.instancer`, then `subset` with WOFF2 flavor).

The public-tree audit (`scripts/check_public_tree.py --desktop-source`) pins the SHA-256 of
each file. Regenerating a font means updating its pin in the same change.
