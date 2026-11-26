# MyNotes Desktop: design tokens

The tokens live in `src/design/tokens.css`. Components read only these custom properties.
`src/design/contrast.test.ts` recomputes the WCAG 2.2 contrast of every pair listed in
`src/design/contrastPairs.ts`, in both themes, and fails below AA. It also checks that the two
copies of the dark block are identical and that every `var(--…)` used in `src/` exists. All
ratios below come from that computation.

## Direction

The look is light and paper-like, close to Wispr Flow. The canvas is a pale cream. The
sidebar and sunken areas use a slightly deeper cream, cards are near-white, and ink is a warm
near-black. Each screen has one lavender primary action with a dark ink label. Surfaces are flat
with hairlines, and soft warm shadows are only for overlays.

- **Brand marks.** The coral bookmark is the single brand ornament. The MyNotes blue
  `#163B66` is used only for links and in the logo tile, never as a large surface.
- **Typography.** Headings are ink, not blue. EB Garamond is used for titles of 24 px and up,
  and Figtree for everything else.
- **Times.** Times and durations use Figtree with tabular figures (`--text-time`,
  `.type-time`). JetBrains Mono (`--text-mono`) is kept for diagnostics: raw commands, logs and
  Copy details.
- **Dark theme.** Dark is a warm counterpart, not an inversion. It has a warm near-black
  canvas, cards lifted one step, cream ink, and the same lavender primary with a dark label.

## Light palette

| Token | Hex | Role | Contrast (measured) |
|---|---|---|---|
| `bg-canvas` | `#FCFBF5` | Window background (Paper) | n/a |
| `bg-sunken` | `#F4F2E7` | Sidebar, wells, gallery stages | n/a |
| `bg-card` | `#FFFFFC` | Cards, inputs, dialogs | n/a |
| `bg-emphasis` | `#EDEADC` | Deep cream: skeletons, segmented track, secondary panels | n/a |
| `bg-inverse` | `#F3F0E2` | Feature surface: Today hero, `Card variant="inverse"` | n/a |
| `text-primary` | `#1B1A17` | Body text, warm ink | 16.79 canvas · 15.50 sunken · 17.37 card · 14.42 emphasis · 15.23 feature |
| `text-brand` | `#1B1A17` | Serif headings (ink) | same as text-primary |
| `text-muted` | `#67645C` | Secondary text, warm grey | 5.70 canvas · 5.26 sunken · 5.90 card · 4.90 emphasis · 5.17 feature · 4.77 hovered sidebar row |
| `link` | `#163B66` | Links ("See all") | 10.94 canvas · 11.32 card |
| `action-primary` | `#F0D7FF` | Primary fill (lavender) | label 13.15 |
| `action-primary-hover` / `-pressed` | `#E8C6FF` / `#DFB6FB` | Primary states | label 11.56 / 10.13 |
| `action-primary-label` | `#1B1A17` | Ink label on lavender | see above |
| `action-primary-line` | `#D6B2EF` | 1 px hairline that holds the fill on cream | decorative (the label names the control) |
| `action-soft` / `-hover` | `#EDEADC` / `#E5E1D1` | Soft buttons, selected nav, tags (cream) | label 14.42 / 13.28 |
| `control-strong` | `#1B1A17` | Toggle on, progress fill, current step, checkbox | 16.79 canvas · 14.42 emphasis track |
| `on-control-strong` | `#FCFBF5` | Toggle mark | 16.79 |
| `focus` | `#2D62FF` | 2 px ring, 2 px offset | 4.71 canvas · 4.35 sunken · 4.87 card · 4.04 emphasis · 4.27 feature · 3.69 lavender fill |
| `border-input` | `#8B877B` | Input borders (WCAG 1.4.11) | 3.46 canvas · 3.20 sunken · 3.58 card |
| `border-hairline` / `-strong` | ink at 10% / 20% | Card and divider lines | n/a |
| `success` on `success-bg` | `#1C6A3B` on `#E2F1E2` | Chips, banners | 5.64 |
| `warning` on `warning-bg` | `#7C4805` on `#F8EBCC` | Chips, badges | 6.37 |
| `danger` on `danger-bg` | `#AD2A1E` on `#F9E6E0` | Errors; live recording dot and label | 5.57 (6.48 as text on canvas) |
| `on-danger` | `#FFFFFC` | Label on a destructive button | 6.70 |
| `info` on `info-bg` | `#234F87` on `#E5ECF5` | In-progress chips | 6.95 |
| `neutral` on `neutral-bg` | `#67645C` on ink 6% | Idle chips | 5.23 on card · 4.68 on sunken |
| `accent-coral` | `#F47B62` | Bookmark ornament only | 2.58: never text, never the only state cue |
| `capsule-bg` | `#1B1A17` | Recording capsule and toasts (dark in both themes) | text `#FCFBF5` 16.79 · muted `#BDB8AB` 8.79 · rec `#FF8A78` 7.59 · ring `#8FB0FF` 8.14 |

## Dark palette

| Token | Hex | Role | Contrast (measured) |
|---|---|---|---|
| `bg-canvas` | `#171614` | Warm near-black | n/a |
| `bg-sunken` | `#12110F` | Sidebar | n/a |
| `bg-card` | `#201F1C` | Cards lifted one step | n/a |
| `bg-emphasis` | `#2A2825` | Deep panels, tracks | n/a |
| `bg-inverse` | `#201F1C` | Feature surface (hero) with hairline | n/a |
| `text-primary` / `text-brand` | `#F2EFE6` | Cream ink | 15.73 canvas · 16.41 sunken · 14.33 card · 12.78 emphasis |
| `text-muted` | `#ACA79B` | Secondary text | 7.54 canvas · 7.87 sunken · 6.87 card · 6.13 emphasis |
| `link` | `#A9C4F5` | Links | 10.25 canvas · 9.34 card |
| `action-primary` | `#E2C8FA` | Lavender fill, slightly softer | label `#1B1A17` 11.50 |
| `action-primary-hover` / `-pressed` | `#EAD6FC` / `#D5B4F4` | Primary states | 12.86 / 9.67 |
| `action-soft` / `-hover` | `#2C2A26` / `#35332E` | Soft buttons, selected nav | label `#F2EFE6` 12.46 / 10.97 |
| `control-strong` | `#E2C8FA` | Toggle on, progress, current step | 11.95 canvas · 9.71 emphasis; mark `#1B1A17` 11.50 |
| `focus` | `#7C9DFF` | Ring | 7.00 canvas · 7.30 sunken · 6.38 card · 5.69 emphasis · 5.54 soft |
| `border-input` | `#7B776D` | Input borders | 4.05 canvas · 4.23 sunken · 3.69 card |
| `success` | `#7FD6A0` on 14% tint | Chips | 6.95 on card |
| `warning` | `#F2BC5A` on 14% tint | Chips | 6.98 on card |
| `danger` | `#FF8F80` on 14% tint | Errors, live dot | 5.76 chip · 7.45 text on card |
| `on-danger` | `#1B1A17` | Destructive label | 7.87 |
| `info` | `#A9C4F5` on 14% tint | Chips | 6.87 on card |
| `neutral` | `#ACA79B` on cream 8% | Chips | 5.53 on card |
| `capsule-bg` | `#2A2825` | Capsule lifted off the dark canvas | text 14.18 · rec 6.41 · ring 6.87 |

In dark, the focus ring is not measured against the lavender fill (1.71). Its 2 px offset places
it on the surrounding surface, never on the fill, and no blue can clear 3:1 against both a
near-black surface and a pale lavender.

## New and changed tokens (for screen work)

No token was renamed or removed. What changed:

| Token | Status | Use |
|---|---|---|
| `--color-link` | new | Text links. Do not use `--color-text-brand` for links; it is ink now. |
| `--color-action-primary-line` | new | Border of the lavender primary (Button already applies it). |
| `--color-control-strong`, `--color-on-control-strong` | new | Selected or "on" controls: toggle, progress fill, current step, checkbox `accent-color`. The lavender fill is too pale for these. |
| `--color-capsule-hover-strong` | new | Hovered button inside the capsule. |
| `--text-time`, `--text-time-sm`, `.type-time` | new | Times and durations, with `font-variant-numeric: tabular-nums`. |
| `--tracking-display` | new | −0.02em for 40 px serif titles. |
| `--text-mono`, `.type-mono` | narrowed | Diagnostics only (raw commands, logs). |
| `--color-bg-inverse` and `*-on-inverse` | re-meant | The feature surface is light now: ink text, hairline, tone-colored chips. `Button variant="onInverse"` renders the lavender primary, and `StatusChip onInverse` is a no-op kept for compatibility. |
| `--shadow-e1` | flattened | Transparent: cards rely on their hairline. `e2` and `e3` are warm overlay shadows. |

## Type, space, shape, motion

These are unchanged from UX-SPEC §4. Display is 40/48, H1 32/40, H2 24/32 (EB Garamond 500).
Figtree covers Title 20/28 · 600 down to Caption 12/16 · 500. Spacing uses a 4 px base. Radii:
inputs 8, buttons 12, cards 16, dialogs 20, pills only for chips and the recording capsule.
Motion is 150/240/320 ms with `cubic-bezier(0.4, 0, 0.2, 1)`.
