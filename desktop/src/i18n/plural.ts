import type { Lang } from "./lang";

const rules = new Map<Lang, Intl.PluralRules>();

/** ICU-style plural pick through Intl.PluralRules (English and Italian need `one` and `other`). */
export function plural(lang: Lang, count: number, forms: { one: string; other: string }): string {
  let rule = rules.get(lang);
  if (!rule) {
    rule = new Intl.PluralRules(lang);
    rules.set(lang, rule);
  }
  return rule.select(count) === "one" ? forms.one : forms.other;
}
