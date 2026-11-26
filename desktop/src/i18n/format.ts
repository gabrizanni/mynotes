/** Dates, times and durations through Intl, in the student's own time zone. */

export interface Formatters {
  time(date: Date): string;
  timeRange(start: Date, end: Date): string;
  /** "Thursday 26 November" / "Giovedì 26 novembre" (capitalized for headings). */
  dayLong(date: Date): string;
  /** "Mon 30 Nov" / "lun 30 nov". */
  dayShort(date: Date): string;
  /** "Fri" / "ven". */
  weekday(date: Date): string;
  /** "in 18 min", "5 min ago" / "tra 18 min", "5 min fa". */
  relative(target: Date, now: Date): string;
  /** "47:30" under an hour, "1:04:20" above. */
  clock(seconds: number): string;
  /** "12 min", "1 h 05 min". */
  minutes(seconds: number): string;
}

function capitalize(text: string, locale: string): string {
  return text.charAt(0).toLocaleUpperCase(locale) + text.slice(1);
}

const pad = (value: number) => String(value).padStart(2, "0");

export function createFormatters(locale: string): Formatters {
  const time = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  const dayLong = new Intl.DateTimeFormat(locale, { weekday: "long", day: "numeric", month: "long" });
  const dayShort = new Intl.DateTimeFormat(locale, { weekday: "short", day: "numeric", month: "short" });
  const weekday = new Intl.DateTimeFormat(locale, { weekday: "short" });
  const relative = new Intl.RelativeTimeFormat(locale, { numeric: "always", style: "short" });
  return {
    time: (date) => time.format(date),
    timeRange: (start, end) => `${time.format(start)}–${time.format(end)}`,
    dayLong: (date) => capitalize(dayLong.format(date).replace(",", ""), locale),
    dayShort: (date) => dayShort.format(date).replace(",", ""),
    weekday: (date) => weekday.format(date),
    relative(target, now) {
      const minutes = Math.round((target.getTime() - now.getTime()) / 60_000);
      if (Math.abs(minutes) < 60) return relative.format(minutes, "minute");
      const hours = Math.round(minutes / 60);
      if (Math.abs(hours) < 24) return relative.format(hours, "hour");
      return relative.format(Math.round(hours / 24), "day");
    },
    clock(seconds) {
      const total = Math.max(0, Math.floor(seconds));
      const h = Math.floor(total / 3600);
      const m = Math.floor((total % 3600) / 60);
      const s = total % 60;
      return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
    },
    minutes(seconds) {
      const total = Math.max(0, Math.round(seconds / 60));
      const h = Math.floor(total / 60);
      const m = total % 60;
      return h > 0 ? `${h} h ${pad(m)} min` : `${m} min`;
    },
  };
}
