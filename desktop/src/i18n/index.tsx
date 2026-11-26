/**
 * i18n entry point.
 *
 *   const t = useT();                 // all messages for the current language
 *   t.today.title                     // "Today" / "Oggi"
 *   t.today.needsYou.count(3)         // plurals through Intl.PluralRules
 *   const { format } = useI18n();     // format.time(date), format.relative(date, now)
 *
 * Adding strings for a feature: edit `en/<feature>.ts`, then `it/<feature>.ts`. A missing
 * Italian key is a type error. Components outside <I18nProvider> fall back to English.
 */
import { createContext, useContext, useMemo, type ReactNode } from "react";

import { en, type Messages } from "./en";
import { createFormatters, type Formatters } from "./format";
import { it } from "./it";
import { localeFor, type Lang } from "./lang";

export { detectLang, isLang, LANGS, localeFor, type Lang } from "./lang";
export type { Messages, Namespace } from "./en";
export type { Formatters } from "./format";

export const catalogs: Record<Lang, Messages> = { en, it };

interface I18nValue {
  lang: Lang;
  locale: string;
  t: Messages;
  format: Formatters;
}

function buildValue(lang: Lang, preferred: readonly string[]): I18nValue {
  const locale = localeFor(lang, preferred);
  return { lang, locale, t: catalogs[lang], format: createFormatters(locale) };
}

const fallback = buildValue("en", []);
const I18nContext = createContext<I18nValue>(fallback);

export function I18nProvider({ lang, children }: { lang: Lang; children: ReactNode }) {
  const value = useMemo(() => buildValue(lang, typeof navigator === "undefined" ? [] : navigator.languages), [lang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}

export function useT(): Messages {
  return useContext(I18nContext).t;
}
