/** Date labels in the calendar's display zone (the grid never mixes zones). */
export interface CalendarFormat {
  time(date: Date): string;
  range(start: Date, end: Date): string;
  weekday(date: Date): string;
  dayNumber(date: Date): string;
  dayLong(date: Date): string;
  /** "23–29 November 2026" / "23–29 novembre 2026". */
  week(first: Date, last: Date): string;
  /** "November 2026" / "Novembre 2026". */
  month(date: Date): string;
}

function capitalize(text: string, locale: string): string {
  return text.charAt(0).toLocaleUpperCase(locale) + text.slice(1);
}

export function calendarFormat(locale: string, zone: string): CalendarFormat {
  const time = new Intl.DateTimeFormat(locale, { timeZone: zone, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const weekday = new Intl.DateTimeFormat(locale, { timeZone: zone, weekday: "short" });
  const dayNumber = new Intl.DateTimeFormat(locale, { timeZone: zone, day: "numeric" });
  const dayLong = new Intl.DateTimeFormat(locale, { timeZone: zone, weekday: "long", day: "numeric", month: "long" });
  const span = new Intl.DateTimeFormat(locale, { timeZone: zone, day: "numeric", month: "long", year: "numeric" });
  const month = new Intl.DateTimeFormat(locale, { timeZone: zone, month: "long", year: "numeric" });
  return {
    time: (date) => time.format(date),
    range: (start, end) => `${time.format(start)}–${time.format(end)}`,
    weekday: (date) => weekday.format(date).replace(".", ""),
    dayNumber: (date) => dayNumber.format(date),
    dayLong: (date) => capitalize(dayLong.format(date).replace(",", ""), locale),
    week: (first, last) => span.formatRange(first, last),
    month: (date) => capitalize(month.format(date), locale),
  };
}

/** The zone the computer uses; the grid and every label follow it. */
export function systemZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}
