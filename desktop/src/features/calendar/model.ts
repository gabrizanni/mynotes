/**
 * Calendar view-model: pure date and layout logic, unit-tested in model.test.ts.
 *
 * Mirrors the Swift `WorkspaceCalendar` (setup/Sources/MyNotesSetup/WorkspaceModels.swift):
 * - weeks start on Monday; a month grid is always 42 days from the Monday before the 1st;
 * - days are real intervals in the display time zone, so a daylight-saving day has 23 or 25
 *   hour rows and blocks are placed by elapsed minutes since local midnight;
 * - a lesson crossing midnight is clipped to each day it touches ("continued" marks the part
 *   after midnight);
 * - horizontal space is shared only inside a connected group of overlapping blocks, never
 *   across unrelated hours of the same day; a block spans free columns to its right.
 *
 * Every function takes the IANA zone explicitly; the page passes the computer's own zone.
 */
import type { ChipKind } from "../../components/StatusChip";
import type { CaptureDashboard, Library } from "../../rpc";

const MINUTE = 60_000;
const HOUR = 3_600_000;

// ---------------------------------------------------------------------------------------------
// Zoned dates
// ---------------------------------------------------------------------------------------------

export interface ZonedParts {
  year: number;
  /** 1–12 */
  month: number;
  day: number;
  hour: number;
  minute: number;
  second: number;
  /** ISO weekday: 1 Monday … 7 Sunday. */
  weekday: number;
}

const WEEKDAYS: Record<string, number> = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6, Sun: 7 };
const partFormatters = new Map<string, Intl.DateTimeFormat>();

function partFormatter(zone: string): Intl.DateTimeFormat {
  let formatter = partFormatters.get(zone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      hourCycle: "h23",
      year: "numeric",
      month: "numeric",
      day: "numeric",
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
      weekday: "short",
    });
    partFormatters.set(zone, formatter);
  }
  return formatter;
}

export function zonedParts(date: Date, zone: string): ZonedParts {
  const values: Record<string, string> = {};
  for (const part of partFormatter(zone).formatToParts(date)) values[part.type] = part.value;
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    hour: Number(values.hour) % 24,
    minute: Number(values.minute),
    second: Number(values.second),
    weekday: WEEKDAYS[values.weekday ?? ""] ?? 1,
  };
}

/** Offset of `zone` from UTC at instant `ms`, in milliseconds (CET → +3 600 000). */
export function zoneOffset(ms: number, zone: string): number {
  const p = zonedParts(new Date(ms), zone);
  return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second) - Math.floor(ms / 1000) * 1000;
}

/** The instant whose wall clock in `zone` reads the given date and time. */
export function zonedTime(year: number, month: number, day: number, hour: number, minute: number, zone: string): Date {
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  const first = wall - zoneOffset(wall, zone);
  return new Date(wall - zoneOffset(first, zone));
}

/** Local midnight of the day that contains `date` (the first instant of that day). */
export function startOfDay(date: Date, zone: string): Date {
  const p = zonedParts(date, zone);
  let start = zonedTime(p.year, p.month, p.day, 0, 0, zone);
  // Zones that skip midnight on a transition start the day at 01:00.
  while (zonedParts(start, zone).day !== p.day) start = new Date(start.getTime() + HOUR);
  return start;
}

/** Midnight `days` calendar days after the day of `date` (DST-safe: not 24 h steps). */
export function addDays(date: Date, days: number, zone: string): Date {
  const p = zonedParts(date, zone);
  const target = new Date(Date.UTC(p.year, p.month - 1, p.day + days));
  return startOfDay(zonedTime(target.getUTCFullYear(), target.getUTCMonth() + 1, target.getUTCDate(), 12, 0, zone), zone);
}

export function sameDay(a: Date, b: Date, zone: string): boolean {
  return dayKey(a, zone) === dayKey(b, zone);
}

const pad = (n: number) => String(n).padStart(2, "0");

/** `YYYY-MM-DD` in `zone`; also the `d=` URL parameter. */
export function dayKey(date: Date, zone: string): string {
  const p = zonedParts(date, zone);
  return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
}

/** Parse the `d=` URL parameter; noon keeps the day stable across offsets. */
export function parseDayKey(value: string | null, zone: string): Date | null {
  const match = value ? /^(\d{4})-(\d{2})-(\d{2})$/.exec(value) : null;
  if (!match) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return zonedTime(year, month, day, 12, 0, zone);
}

export interface DayInterval {
  start: Date;
  end: Date;
}

export function dayInterval(day: Date, zone: string): DayInterval {
  return { start: startOfDay(day, zone), end: addDays(day, 1, zone) };
}

/** The seven days (Monday first) of the week that contains `date`. */
export function weekDays(date: Date, zone: string): Date[] {
  const offset = zonedParts(date, zone).weekday - 1;
  const monday = addDays(date, -offset, zone);
  return Array.from({ length: 7 }, (_, index) => addDays(monday, index, zone));
}

/** Always 42 days (6 weeks), starting on the Monday on or before the 1st of the month. */
export function monthDays(date: Date, zone: string): Date[] {
  const p = zonedParts(date, zone);
  const first = zonedTime(p.year, p.month, 1, 12, 0, zone);
  const [monday] = weekDays(first, zone);
  const start = monday ?? startOfDay(first, zone);
  return Array.from({ length: 42 }, (_, index) => addDays(start, index, zone));
}

/** Move the anchor by whole weeks or months; months land on the 1st. */
export function shiftAnchor(anchor: Date, view: "week" | "month", delta: number, zone: string): Date {
  if (view === "week") return zonedTime(...ymd(addDays(anchor, delta * 7, zone), zone), 12, 0, zone);
  const p = zonedParts(anchor, zone);
  const target = new Date(Date.UTC(p.year, p.month - 1 + delta, 1));
  return zonedTime(target.getUTCFullYear(), target.getUTCMonth() + 1, 1, 12, 0, zone);
}

function ymd(date: Date, zone: string): [number, number, number] {
  const p = zonedParts(date, zone);
  return [p.year, p.month, p.day];
}

/** The part of [start, end) inside `day`, or null when they do not meet. */
export function clipped(start: Date, end: Date, day: Date, zone: string): DayInterval | null {
  const bounds = dayInterval(day, zone);
  const from = Math.max(start.getTime(), bounds.start.getTime());
  const through = Math.min(end.getTime(), bounds.end.getTime());
  return from < through ? { start: new Date(from), end: new Date(through) } : null;
}

/** Minutes since the start of `day` (elapsed time, so DST days stay honest). */
export function timelineMinute(date: Date, day: Date, zone: string): number {
  return (date.getTime() - startOfDay(day, zone).getTime()) / MINUTE;
}

export interface HourMark {
  at: Date;
  /** Wall-clock hour, 0–23 (repeats once on the autumn transition). */
  hour: number;
  /** Minutes from the top of the column. */
  minute: number;
}

/** One mark per real hour of the day: 23, 24 or 25 of them. */
export function hourMarks(day: Date, zone: string): HourMark[] {
  const { start, end } = dayInterval(day, zone);
  const marks: HourMark[] = [];
  for (let t = start.getTime(); t < end.getTime(); t += HOUR) {
    marks.push({ at: new Date(t), hour: zonedParts(new Date(t), zone).hour, minute: (t - start.getTime()) / MINUTE });
  }
  return marks;
}

// ---------------------------------------------------------------------------------------------
// Time zone labels
// ---------------------------------------------------------------------------------------------

/** Short zone name at an instant: "CET", "CEST", "GMT+1" (depends on the locale's data). */
export function zoneAbbreviation(date: Date, zone: string, locale: string): string {
  const part = new Intl.DateTimeFormat(locale, { timeZone: zone, timeZoneName: "short" }).formatToParts(date).find((item) => item.type === "timeZoneName");
  return part?.value ?? zone;
}

/** "Europe/Rome · CET": the zone the grid uses, written under the toolbar. */
export function displayZoneLabel(date: Date, zone: string, locale: string): string {
  return `${zone} · ${zoneAbbreviation(date, zone, locale)}`;
}

/**
 * When a lesson's calendar uses another zone, its original wall time:
 * "26 Nov, 08:00 GMT · Europe/London". Null when the zones match (Swift `originalTimeLabel`).
 */
export function originalTimeLabel(date: Date, eventZone: string, displayZone: string, locale: string): string | null {
  if (!eventZone || eventZone === displayZone) return null;
  try {
    const text = new Intl.DateTimeFormat(locale, {
      timeZone: eventZone,
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).format(date);
    return `${text} ${zoneAbbreviation(date, eventZone, locale)} · ${eventZone}`;
  } catch {
    return null;
  }
}

// ---------------------------------------------------------------------------------------------
// Placements
// ---------------------------------------------------------------------------------------------

export interface Timed {
  id: string;
  start: Date;
  end: Date;
}

export interface Placement<T extends Timed> {
  item: T;
  /** Clipped to the day. */
  start: Date;
  end: Date;
  /** Minutes from the top of the day column. */
  top: number;
  /** Height in minutes, at least `minimumMinutes`. */
  height: number;
  column: number;
  columnCount: number;
  span: number;
  /** The lesson started the day before (shown with a continuation mark). */
  continued: boolean;
}

/**
 * Lay out one day. Items are sorted by start, then longer first, then id; overlapping items
 * form a connected group whose columns are assigned greedily; each block then spans every
 * free column to its right. `minimumMinutes` keeps very short blocks tappable and counts
 * for overlaps, like the Swift version.
 */
export function placements<T extends Timed>(items: readonly T[], day: Date, zone: string, minimumMinutes = 0): Placement<T>[] {
  const dayStart = startOfDay(day, zone).getTime();
  type Entry = Placement<T> & { endMs: number; startMs: number };
  const entries: Entry[] = [];
  for (const item of items) {
    const segment = clipped(item.start, item.end, day, zone);
    if (!segment) continue;
    const startMs = segment.start.getTime();
    const endMs = Math.max(segment.end.getTime(), startMs + minimumMinutes * MINUTE);
    entries.push({
      item,
      start: segment.start,
      end: segment.end,
      startMs,
      endMs,
      top: (startMs - dayStart) / MINUTE,
      height: (endMs - startMs) / MINUTE,
      column: 0,
      columnCount: 1,
      span: 1,
      continued: item.start.getTime() < startMs,
    });
  }
  entries.sort((a, b) => a.startMs - b.startMs || b.endMs - a.endMs || a.item.id.localeCompare(b.item.id));

  const groups: Entry[][] = [];
  let groupEnd = -Infinity;
  for (const entry of entries) {
    const last = groups[groups.length - 1];
    if (!last || entry.startMs >= groupEnd) {
      groups.push([entry]);
      groupEnd = entry.endMs;
    } else {
      last.push(entry);
      groupEnd = Math.max(groupEnd, entry.endMs);
    }
  }

  const result: Placement<T>[] = [];
  for (const group of groups) {
    const ends: number[] = [];
    for (const entry of group) {
      let column = ends.findIndex((end) => end <= entry.startMs);
      if (column === -1) {
        column = ends.length;
        ends.push(entry.endMs);
      } else {
        ends[column] = entry.endMs;
      }
      entry.column = column;
    }
    for (const entry of group) {
      entry.columnCount = ends.length;
      let span = 1;
      for (let column = entry.column + 1; column < ends.length; column += 1) {
        const blocked = group.some((other) => other.column === column && other.startMs < entry.endMs && other.endMs > entry.startMs);
        if (blocked) break;
        span += 1;
      }
      entry.span = span;
      const { endMs: _endMs, startMs: _startMs, ...placement } = entry;
      result.push(placement);
    }
  }
  return result;
}

/** Pairs of lessons whose times overlap, for "Clashes with …" in the popover. */
export function overlapsOf<T extends Timed>(items: readonly T[]): Map<string, T[]> {
  const sorted = [...items].sort((a, b) => a.start.getTime() - b.start.getTime() || a.id.localeCompare(b.id));
  const map = new Map<string, T[]>();
  for (let i = 0; i < sorted.length; i += 1) {
    const a = sorted[i];
    if (!a) continue;
    for (let j = i + 1; j < sorted.length; j += 1) {
      const b = sorted[j];
      if (!b || b.start.getTime() >= a.end.getTime()) break;
      map.set(a.id, [...(map.get(a.id) ?? []), b]);
      map.set(b.id, [...(map.get(b.id) ?? []), a]);
    }
  }
  return map;
}

// ---------------------------------------------------------------------------------------------
// Lessons from the recorder dashboard
// ---------------------------------------------------------------------------------------------

export const TINT_COUNT = 6;

export interface CalendarChip {
  kind: ChipKind;
  status: string;
}

export interface CalendarLesson extends Timed {
  eventId: string;
  title: string;
  courseCode: string | null;
  courseName: string;
  lessonNumber: number | null;
  lectureId: string | null;
  /** The calendar's own zone (source timezone). */
  timezone: string;
  /** Backend says this lesson's recording window overlaps another one. */
  clash: boolean;
  needsCourse: boolean;
  /** Not recorded: cancelled in the calendar or excluded here. */
  excluded: boolean;
  recordingEnabled: boolean;
  localChanges: boolean;
  /** Added in MyNotes ("Schedule lesson"), not from a calendar feed. */
  special: boolean;
  sourceLabel: string | null;
  /** 1–6, stable per course; unassigned lessons use the stone tint. */
  tint: number;
  recording: CalendarChip;
  transcription: CalendarChip | null;
}

export interface LessonsInput {
  dashboard: CaptureDashboard | null;
  library: Library | null;
  now: Date;
}

const UNASSIGNED_TINT = 5;

export function lectureIdOf(courseCode: string | null | undefined, lessonNumber: number | null | undefined): string | null {
  return courseCode && lessonNumber ? `${courseCode}-L${pad(lessonNumber)}` : null;
}

/** Stable tint per course: order of the course list, wrapping after six. */
export function tintFor(courseCode: string | null, courseOrder: readonly string[]): number {
  if (!courseCode) return UNASSIGNED_TINT;
  const index = courseOrder.indexOf(courseCode);
  if (index >= 0) return (index % TINT_COUNT) + 1;
  let hash = 0;
  for (const char of courseCode) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return (hash % TINT_COUNT) + 1;
}

export function calendarLessons({ dashboard, library, now }: LessonsInput): CalendarLesson[] {
  if (!dashboard) return [];
  const courseOrder = dashboard.courses.map((course) => course.code);
  const names = new Map(dashboard.courses.map((course) => [course.code, course.name]));
  const sources = new Map(dashboard.sources.map((source) => [source.sourceID, source.label]));
  const runtime = dashboard.runtime;
  const live = dashboard.running === true && runtime.recording === true ? (runtime.currentSession ?? null) : null;
  const sessions = new Map((runtime.sessions ?? []).map((session) => [session.event.id, session]));
  const transcription = new Map<string, string>();
  for (const recording of library?.recordings ?? []) {
    if (recording.lecture_id && recording.transcription_status) transcription.set(recording.lecture_id, recording.transcription_status);
  }
  const recorderOff = !dashboard.enabled || dashboard.running === false;

  return dashboard.events.map((event) => {
    const start = new Date(event.startsAt);
    const end = new Date(event.endsAt);
    const lectureId = lectureIdOf(event.courseCode, event.lessonNumber);
    const excluded = event.eligibility === "CANCELLED";
    const clash = event.eligibility === "CONFLICT";
    const needsCourse = event.eligibility === "NEEDS_REVIEW" && (event.courseCode === null || event.reasonCode === "UNKNOWN_COURSE");
    const session = sessions.get(event.id);
    let recording: CalendarChip;
    if (live?.event.id === event.id) recording = { kind: "recording", status: "RECORDING" };
    else if (excluded) recording = { kind: "recording", status: "CANCELLED" };
    else if (session) recording = { kind: "recording", status: session.status };
    else if (end.getTime() <= now.getTime()) recording = { kind: "recording", status: "MISSED" };
    else if (clash) recording = { kind: "recording", status: "CONFLICT" };
    else if (needsCourse) recording = { kind: "recording", status: "NEEDS_COURSE" };
    else if (recorderOff) recording = { kind: "recording", status: "OFF" };
    else recording = { kind: "recording", status: "SCHEDULED" };
    const transcribed = lectureId ? transcription.get(lectureId) : undefined;
    return {
      id: event.id,
      eventId: event.id,
      start,
      end,
      title: event.title,
      courseCode: event.courseCode,
      courseName: (event.courseCode && names.get(event.courseCode)) || event.title,
      lessonNumber: event.lessonNumber,
      lectureId,
      timezone: event.timezone,
      clash,
      needsCourse,
      excluded,
      recordingEnabled: event.recordingEnabled,
      localChanges: event.localChanges,
      special: event.special,
      sourceLabel: sources.get(event.sourceID) ?? null,
      tint: tintFor(event.courseCode, courseOrder),
      recording,
      transcription: transcribed && transcribed !== "NOT_STARTED" ? { kind: "transcription", status: transcribed } : null,
    };
  });
}

// ---------------------------------------------------------------------------------------------
// Week and month models
// ---------------------------------------------------------------------------------------------

export interface WeekColumn {
  key: string;
  date: Date;
  isToday: boolean;
  isWeekend: boolean;
  /** Real hours of the day (23, 24 or 25). */
  hours: HourMark[];
  /** Total minutes in the day. */
  minutes: number;
  /** The day has a clock change: its rows carry their own labels. */
  shifted: boolean;
  placements: Placement<CalendarLesson>[];
  /** Minutes from the top for the now-line, on today's column only. */
  nowMinute: number | null;
}

export function weekColumns(anchor: Date, zone: string, lessons: readonly CalendarLesson[], now: Date, minimumMinutes = 30): WeekColumn[] {
  return weekDays(anchor, zone).map((date) => {
    const hours = hourMarks(date, zone);
    const isToday = sameDay(date, now, zone);
    const weekday = zonedParts(date, zone).weekday;
    return {
      key: dayKey(date, zone),
      date,
      isToday,
      isWeekend: weekday >= 6,
      hours,
      minutes: hours.length * 60,
      shifted: hours.length !== 24,
      placements: placements(lessons, date, zone, minimumMinutes),
      nowMinute: isToday ? timelineMinute(now, date, zone) : null,
    };
  });
}

export const MONTH_CELL_LIMIT = 3;

export interface MonthCell {
  key: string;
  date: Date;
  day: number;
  inMonth: boolean;
  isToday: boolean;
  isWeekend: boolean;
  /** Lessons touching the day, in start order. */
  lessons: CalendarLesson[];
  /** The first three, then "+ n more". */
  visible: CalendarLesson[];
  more: number;
}

export function monthCells(anchor: Date, zone: string, lessons: readonly CalendarLesson[], now: Date): MonthCell[] {
  const month = zonedParts(anchor, zone).month;
  return monthDays(anchor, zone).map((date) => {
    const parts = zonedParts(date, zone);
    const touching = lessons
      .filter((lesson) => clipped(lesson.start, lesson.end, date, zone) !== null)
      .sort((a, b) => a.start.getTime() - b.start.getTime() || a.id.localeCompare(b.id));
    return {
      key: dayKey(date, zone),
      date,
      day: parts.day,
      inMonth: parts.month === month,
      isToday: sameDay(date, now, zone),
      isWeekend: parts.weekday >= 6,
      lessons: touching,
      visible: touching.slice(0, MONTH_CELL_LIMIT),
      more: Math.max(0, touching.length - MONTH_CELL_LIMIT),
    };
  });
}

/** The first hour worth showing when the week opens: one hour before the earliest lesson, 07:00 at the latest. */
export function firstVisibleHour(columns: readonly WeekColumn[], fallback = 7): number {
  let earliest = Infinity;
  for (const column of columns) for (const placement of column.placements) earliest = Math.min(earliest, placement.top);
  if (!Number.isFinite(earliest)) return fallback;
  return Math.max(0, Math.min(fallback, Math.floor(earliest / 60) - 1));
}

/** Defaults for "Schedule lesson": the next full hour, 90 minutes. */
export function defaultSlot(now: Date, zone: string): { day: string; start: string; end: string } {
  const p = zonedParts(new Date(now.getTime() + HOUR), zone);
  const startHour = Math.min(p.hour, 22);
  const endMinutes = startHour * 60 + 90;
  return {
    day: `${p.year}-${pad(p.month)}-${pad(p.day)}`,
    start: `${pad(startHour)}:00`,
    end: `${pad(Math.floor(endMinutes / 60))}:${pad(endMinutes % 60)}`,
  };
}

/** Wall-clock day and time ("2026-11-27", "09:30") in `zone` → ISO instant; null if invalid. */
export function wallTimeToIso(day: string, time: string, zone: string): string | null {
  const date = parseDayKey(day, zone);
  const match = /^(\d{2}):(\d{2})$/.exec(time);
  if (!date || !match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour > 23 || minute > 59) return null;
  const [y, m, d] = ymd(date, zone);
  return zonedTime(y, m, d, hour, minute, zone).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/** "HH:mm" of an instant in `zone`, for time inputs. */
export function wallClock(date: Date, zone: string): string {
  const p = zonedParts(date, zone);
  return `${pad(p.hour)}:${pad(p.minute)}`;
}
