import { describe, expect, it } from "vitest";

import { contrastRatio, parseColor, readBlock, resolveToken, tokenContrast, type TokenMap } from "./contrast";
import { CONTRAST_PAIRS, MINIMUM } from "./contrastPairs";
import tokensCss from "./tokens.css?raw";

const light = readBlock(tokensCss, ":root");
const darkOverride = readBlock(tokensCss, ':root[data-theme="dark"]');
const darkSystem = readBlock(tokensCss, ':root:not([data-theme="light"])');
const THEMES: Array<[name: "light" | "dark", tokens: TokenMap]> = [
  ["light", light],
  ["dark", { ...light, ...darkOverride }],
];

describe("contrast helpers", () => {
  it("matches the WCAG reference values", () => {
    expect(contrastRatio(parseColor("#000"), parseColor("#fff"))).toBeCloseTo(21, 5);
    expect(contrastRatio(parseColor("#777777"), parseColor("#ffffff"))).toBeCloseTo(4.48, 2);
    expect(parseColor("rgba(27, 26, 23, 0.5)")).toEqual({ r: 27, g: 26, b: 23, a: 0.5 });
  });

  it("composites translucent layers onto the canvas", () => {
    const tokens: TokenMap = { "--color-bg-canvas": "#ffffff", "--ink": "#000000", "--veil": "rgba(0, 0, 0, 0.5)" };
    expect(tokenContrast(tokens, "--ink", [])).toBeCloseTo(21, 5);
    expect(tokenContrast(tokens, "--ink", ["--veil"])).toBeLessThan(6);
  });
});

describe.each(THEMES)("%s theme: WCAG 2.2 AA contrast", (theme, tokens) => {
  const pairs = CONTRAST_PAIRS.filter((pair) => pair.only === undefined || pair.only === theme);
  it.each(pairs.map((pair) => [pair.role, pair] as const))("%s", (_role, pair) => {
    const ratio = tokenContrast(tokens, pair.fg, pair.bg);
    const detail = `${pair.fg} ${resolveToken(tokens, pair.fg)} on ${pair.bg.join(" / ")}: ${ratio.toFixed(2)}:1`;
    expect(ratio, detail).toBeGreaterThanOrEqual(MINIMUM[pair.level]);
  });
});

describe("token file", () => {
  it("keeps the OS-follow and manual dark blocks identical", () => {
    expect(darkSystem).toEqual(darkOverride);
  });

  it("only overrides tokens that the light theme defines", () => {
    expect(Object.keys(darkOverride).filter((name) => !(name in light))).toEqual([]);
  });

  it("defines every token the source references", () => {
    const sources = import.meta.glob<string>(["/src/**/*.{css,tsx,ts}", "!/src/**/*.test.{ts,tsx}", "!/src/design/contrast*.ts"], {
      query: "?raw",
      import: "default",
      eager: true,
    });
    const missing = new Set<string>();
    for (const [path, text] of Object.entries(sources)) {
      // Complete references only: `var(--color-${name})` built at runtime is skipped.
      for (const match of text.matchAll(/var\((--[a-z0-9-]+)\s*[,)]/g)) {
        const name = match[1];
        if (name && !(name in light)) missing.add(`${name} (${path})`);
      }
    }
    expect([...missing]).toEqual([]);
  });
});
