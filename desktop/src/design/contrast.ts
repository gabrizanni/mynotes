/**
 * WCAG 2.2 contrast helpers for the design tokens (used by contrast.test.ts and by the palette
 * table in docs/DESIGN-TOKENS.md). Pure functions, no DOM.
 */

export interface Rgba {
  r: number;
  g: number;
  b: number;
  /** 0–1 */
  a: number;
}

export type TokenMap = Record<string, string>;

const HEX = /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const RGB = /^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i;

/** Parses `#rgb`, `#rrggbb`, `#rrggbbaa`, `rgb()` and `rgba()`; throws on anything else. */
export function parseColor(value: string): Rgba {
  const text = value.trim();
  const hex = HEX.exec(text);
  if (hex?.[1]) {
    const digits = hex[1].length === 3 ? [...hex[1]].map((digit) => digit + digit).join("") : hex[1];
    const channel = (index: number) => parseInt(digits.slice(index, index + 2), 16);
    return { r: channel(0), g: channel(2), b: channel(4), a: digits.length === 8 ? channel(6) / 255 : 1 };
  }
  const rgb = RGB.exec(text);
  if (rgb?.[1] && rgb[2] && rgb[3]) {
    return { r: Number(rgb[1]), g: Number(rgb[2]), b: Number(rgb[3]), a: rgb[4] === undefined ? 1 : Number(rgb[4]) };
  }
  throw new Error(`Not a color: ${value}`);
}

/** Source-over compositing of a (possibly translucent) color onto an opaque base. */
export function composite(top: Rgba, base: Rgba): Rgba {
  const mix = (front: number, back: number) => front * top.a + back * (1 - top.a);
  return { r: mix(top.r, base.r), g: mix(top.g, base.g), b: mix(top.b, base.b), a: 1 };
}

function linear(channel: number): number {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** WCAG relative luminance of an opaque color. */
export function luminance(color: Rgba): number {
  return 0.2126 * linear(color.r) + 0.7152 * linear(color.g) + 0.0722 * linear(color.b);
}

/** WCAG contrast ratio between two opaque colors (1–21). */
export function contrastRatio(a: Rgba, b: Rgba): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (light + 0.05) / (dark + 0.05);
}

/** Reads the custom properties declared directly inside the first block that `selector` opens. */
export function readBlock(css: string, selector: string): TokenMap {
  const start = css.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`Block not found: ${selector}`);
  const open = css.indexOf("{", start);
  let depth = 0;
  let end = open;
  for (let index = open; index < css.length; index += 1) {
    if (css[index] === "{") depth += 1;
    if (css[index] === "}") depth -= 1;
    if (depth === 0) {
      end = index;
      break;
    }
  }
  const body = css.slice(open + 1, end).replace(/\/\*[\s\S]*?\*\//g, "");
  const tokens: TokenMap = {};
  for (const match of body.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)) {
    if (match[1] && match[2]) tokens[match[1]] = match[2].trim();
  }
  return tokens;
}

/** Follows `var(--name)` references until a literal value is reached. */
export function resolveToken(tokens: TokenMap, name: string, seen: string[] = []): string {
  const value = tokens[name];
  if (value === undefined) throw new Error(`Unknown token: ${name}`);
  const reference = /^var\((--[a-z0-9-]+)\)$/i.exec(value);
  if (!reference?.[1]) return value;
  if (seen.includes(name)) throw new Error(`Token cycle: ${[...seen, name].join(" → ")}`);
  return resolveToken(tokens, reference[1], [...seen, name]);
}

/**
 * Contrast of token `fg` over the stack `bg` (innermost first), each layer composited onto the
 * next and the outermost onto `canvas`, the window background.
 */
export function tokenContrast(tokens: TokenMap, fg: string, bg: readonly string[]): number {
  const layers = [...bg, "--color-bg-canvas"].map((name) => parseColor(resolveToken(tokens, name)));
  let ground = layers[layers.length - 1] ?? parseColor("#000");
  for (let index = layers.length - 2; index >= 0; index -= 1) {
    const layer = layers[index];
    if (layer) ground = composite(layer, ground);
  }
  return contrastRatio(composite(parseColor(resolveToken(tokens, fg)), ground), ground);
}
