/**
 * Every foreground/background token pair the UI actually renders, with its WCAG 2.2 floor.
 * `bg` is a stack, innermost first; translucent layers are composited onto the next one and the
 * last one onto the canvas. contrast.test.ts checks each pair in both themes.
 *
 * Adding a component that puts a new color on a new surface? Add the pair here.
 */

export type ContrastLevel = "text" | "large" | "ui";

/** WCAG 2.2: 1.4.3 body text 4.5:1, large text 3:1; 1.4.11 focus rings, borders, controls 3:1. */
export const MINIMUM: Record<ContrastLevel, number> = { text: 4.5, large: 3, ui: 3 };

export interface ContrastPair {
  role: string;
  fg: string;
  bg: readonly string[];
  level: ContrastLevel;
  /** Limit the pair to one theme when the other theme never renders it. */
  only?: "light" | "dark";
}

const SURFACES = ["--color-bg-canvas", "--color-bg-sunken", "--color-bg-card", "--color-bg-emphasis", "--color-bg-inverse"] as const;
const surfaceName = (token: string) => token.replace("--color-bg-", "");

const onSurfaces = (role: string, fg: string, level: ContrastLevel, surfaces: readonly string[] = SURFACES): ContrastPair[] =>
  surfaces.map((surface) => ({ role: `${role} on ${surfaceName(surface)}`, fg, bg: [surface], level }));

const TONES = ["success", "warning", "danger", "info", "neutral"] as const;

export const CONTRAST_PAIRS: readonly ContrastPair[] = [
  // Text on every surface.
  ...onSurfaces("Body text", "--color-text-primary", "text"),
  ...onSurfaces("Secondary text", "--color-text-muted", "text"),
  ...onSurfaces("Serif heading", "--color-text-brand", "large"),
  ...onSurfaces("Link", "--color-link", "text"),
  { role: "Body text on a hovered row (sidebar)", fg: "--color-text-primary", bg: ["--color-bg-pressed", "--color-bg-sunken"], level: "text" },
  { role: "Secondary text on a hovered row (sidebar)", fg: "--color-text-muted", bg: ["--color-bg-hover", "--color-bg-sunken"], level: "text" },
  { role: "Error text on canvas", fg: "--color-danger", bg: ["--color-bg-canvas"], level: "text" },
  { role: "Error text on card", fg: "--color-danger", bg: ["--color-bg-card"], level: "text" },

  // Feature surface (Today hero, inverse card).
  { role: "Feature text", fg: "--color-text-on-inverse", bg: ["--color-bg-inverse"], level: "text" },
  { role: "Feature secondary text", fg: "--color-text-on-inverse-muted", bg: ["--color-bg-inverse"], level: "text" },
  { role: "Feature link", fg: "--color-link-on-inverse", bg: ["--color-bg-inverse"], level: "text" },
  { role: "Feature text on a hovered control", fg: "--color-text-on-inverse", bg: ["--color-on-inverse-hover", "--color-bg-inverse"], level: "text" },

  // Actions.
  { role: "Primary label", fg: "--color-action-primary-label", bg: ["--color-action-primary"], level: "text" },
  { role: "Primary label, hover", fg: "--color-action-primary-label", bg: ["--color-action-primary-hover"], level: "text" },
  { role: "Primary label, pressed", fg: "--color-action-primary-label", bg: ["--color-action-primary-pressed"], level: "text" },
  { role: "Soft label (soft button, selected nav, tag)", fg: "--color-action-soft-label", bg: ["--color-action-soft"], level: "text" },
  { role: "Soft label, hover", fg: "--color-action-soft-label", bg: ["--color-action-soft-hover"], level: "text" },
  { role: "Destructive label", fg: "--color-on-danger", bg: ["--color-danger"], level: "text" },

  // Status chips and badges: tone text on its tint, over the surfaces they sit on.
  ...TONES.flatMap((tone) =>
    (["--color-bg-canvas", "--color-bg-card", "--color-bg-sunken"] as const).map(
      (surface): ContrastPair => ({
        role: `${tone} chip on ${surfaceName(surface)}`,
        fg: `--color-${tone}`,
        bg: [`--color-${tone}-bg`, surface],
        level: "text",
      }),
    ),
  ),

  // Recording capsule (dark in both themes).
  { role: "Capsule text", fg: "--color-capsule-text", bg: ["--color-capsule-bg"], level: "text" },
  { role: "Capsule secondary text", fg: "--color-capsule-muted", bg: ["--color-capsule-bg"], level: "text" },
  { role: "Capsule REC and problem text", fg: "--color-capsule-rec", bg: ["--color-capsule-bg"], level: "text" },
  { role: "Capsule saved text", fg: "--color-capsule-success", bg: ["--color-capsule-bg"], level: "text" },
  { role: "Capsule warning text", fg: "--color-capsule-warning", bg: ["--color-capsule-bg"], level: "text" },
  { role: "Capsule button label", fg: "--color-capsule-text", bg: ["--color-capsule-hover", "--color-capsule-bg"], level: "text" },

  // Focus ring: 2 px with a 2 px offset, so it sits on the surface around the control.
  ...onSurfaces("Focus ring", "--color-focus", "ui"),
  { role: "Focus ring on soft fill", fg: "--color-focus", bg: ["--color-action-soft"], level: "ui" },
  { role: "Focus ring on lavender fill", fg: "--color-focus", bg: ["--color-action-primary"], level: "ui", only: "light" },
  { role: "Focus ring on feature surface", fg: "--color-focus-on-inverse", bg: ["--color-bg-inverse"], level: "ui" },
  { role: "Focus ring in the capsule", fg: "--color-capsule-link", bg: ["--color-capsule-bg"], level: "ui" },

  // Input borders and strong controls.
  ...onSurfaces("Input border", "--color-border-input", "ui", ["--color-bg-canvas", "--color-bg-sunken", "--color-bg-card"]),
  ...onSurfaces("Strong control (toggle on, progress, step)", "--color-control-strong", "ui", ["--color-bg-canvas", "--color-bg-card", "--color-bg-emphasis"]),
  { role: "Toggle mark on strong control", fg: "--color-on-control-strong", bg: ["--color-control-strong"], level: "ui" },
  { role: "Live dot on canvas", fg: "--color-danger", bg: ["--color-bg-canvas"], level: "ui" },
  // Calendar lesson blocks: neutral course tints laid over the card surface.
  ...[1, 2, 3, 4, 5, 6].flatMap((n): ContrastPair[] => [
    { role: `Lesson title on course tint ${n}`, fg: "--color-text-primary", bg: [`--color-course-${n}`, "--color-bg-card"], level: "text" },
    { role: `Lesson time on course tint ${n}`, fg: "--color-text-muted", bg: [`--color-course-${n}`, "--color-bg-card"], level: "text" },
  ]),
];
