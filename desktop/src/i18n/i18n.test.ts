import { describe, expect, it } from "vitest";

import { en } from "./en";
import { it as italian } from "./it";
import { detectLang, localeFor } from "./lang";
import { plural } from "./plural";
import { createFormatters } from "./format";

type Tree = { [key: string]: unknown };

function shape(value: unknown, path = ""): string[] {
  if (typeof value === "function") return [`${path}:fn${value.length}`];
  if (Array.isArray(value)) return [`${path}:array${value.length}`];
  if (typeof value === "object" && value !== null) {
    return Object.keys(value)
      .sort()
      .flatMap((key) => shape((value as Tree)[key], path ? `${path}.${key}` : key));
  }
  return [`${path}:${typeof value}`];
}

describe("i18n catalogs", () => {
  it("Italian has exactly the English keys, arities and array lengths", () => {
    expect(shape(italian)).toEqual(shape(en));
  });

  it("no Italian string is empty", () => {
    const empty = shape(italian).filter((entry) => entry.endsWith(":string")).filter((entry) => {
      const value = entry
        .split(":")[0]!
        .split(".")
        .reduce<unknown>((node, key) => (node as Tree)[key], italian);
      return typeof value === "string" && value.trim() === "";
    });
    expect(empty).toEqual([]);
  });

  it("plurals go through Intl.PluralRules", () => {
    expect(plural("en", 1, { one: "1 lesson", other: "n lessons" })).toBe("1 lesson");
    expect(en.today.timeline.count(3)).toBe("3 lessons");
    expect(italian.today.timeline.count(1)).toBe("1 lezione");
    expect(italian.today.needsYou.count(2)).toBe("2 elementi");
  });

  it("picks the OS language and a locale per language", () => {
    expect(detectLang(["it-IT", "en-US"])).toBe("it");
    expect(detectLang(["fr-FR"])).toBe("en");
    expect(localeFor("en", ["en-US"])).toBe("en-US");
    expect(localeFor("it")).toBe("it-IT");
  });
});

describe("formatters", () => {
  const format = createFormatters("en-GB");
  it("formats clocks without fake precision", () => {
    expect(format.clock(65)).toBe("01:05");
    expect(format.clock(3860)).toBe("1:04:20");
    expect(format.minutes(3900)).toBe("1 h 05 min");
  });
  it("formats relative times in minutes, hours and days", () => {
    const now = new Date("2026-11-26T09:42:00Z");
    expect(format.relative(new Date("2026-11-26T10:00:00Z"), now)).toBe("in 18 min");
    expect(createFormatters("it-IT").relative(new Date("2026-11-26T10:00:00Z"), now)).toBe("tra 18 min");
  });
});
