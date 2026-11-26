export const LANGS = ["en", "it"] as const;
export type Lang = (typeof LANGS)[number];

export function isLang(value: unknown): value is Lang {
  return value === "en" || value === "it";
}

/** OS language first (UX-SPEC §2 step "language"), English otherwise. */
export function detectLang(languages: readonly string[]): Lang {
  for (const tag of languages) {
    const base = tag.toLowerCase().split("-")[0];
    if (isLang(base)) return base;
  }
  return "en";
}

/** BCP 47 tag used by Intl formatters for each UI language. */
export function localeFor(lang: Lang, preferred: readonly string[] = []): string {
  const match = preferred.find((tag) => tag.toLowerCase().startsWith(`${lang}-`));
  if (match) return match;
  return lang === "it" ? "it-IT" : "en-GB";
}
