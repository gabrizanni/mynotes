import { describe, expect, it } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";
import {
  addDays,
  calendarLessons,
  clipped,
  dayKey,
  defaultSlot,
  displayZoneLabel,
  firstVisibleHour,
  hourMarks,
  monthCells,
  monthDays,
  originalTimeLabel,
  overlapsOf,
  parseDayKey,
  placements,
  shiftAnchor,
  startOfDay,
  timelineMinute,
  tintFor,
  wallTimeToIso,
  weekColumns,
  weekDays,
  zonedParts,
  type Timed,
} from "./model";

const ROME = "Europe/Rome";
const at = (iso: string) => new Date(iso);
const keys = (days: Date[], zone = ROME) => days.map((day) => dayKey(day, zone));
const slot = (id: string, start: string, end: string): Timed => ({ id, start: at(start), end: at(end) });

describe("zoned days", () => {
  it("finds local midnight in the display zone, not in UTC", () => {
    // 00:30 in Rome on 26 Nov is still 25 Nov in UTC.
    expect(startOfDay(at("2026-11-25T23:30:00Z"), ROME).toISOString()).toBe("2026-11-25T23:00:00.000Z");
    expect(dayKey(at("2026-11-25T23:30:00Z"), ROME)).toBe("2026-11-26");
    expect(dayKey(at("2026-11-25T23:30:00Z"), "UTC")).toBe("2026-11-25");
  });

  it("adds calendar days across a clock change", () => {
    const saturday = at("2026-10-24T10:00:00Z");
    expect(addDays(saturday, 1, ROME).toISOString()).toBe("2026-10-24T22:00:00.000Z"); // Sunday 00:00 CEST
    expect(addDays(saturday, 2, ROME).toISOString()).toBe("2026-10-25T23:00:00.000Z"); // Monday 00:00 CET
  });

  it("round-trips the d= URL parameter and rejects junk", () => {
    const date = parseDayKey("2026-11-27", ROME);
    expect(date && dayKey(date, ROME)).toBe("2026-11-27");
    expect(parseDayKey("27/11/2026", ROME)).toBeNull();
    expect(parseDayKey("2026-13-01", ROME)).toBeNull();
    expect(parseDayKey(null, ROME)).toBeNull();
  });
});

describe("week and month grids", () => {
  it("starts weeks on Monday, from any day of the week", () => {
    expect(keys(weekDays(at("2026-11-26T09:42:00Z"), ROME))).toEqual([
      "2026-11-23",
      "2026-11-24",
      "2026-11-25",
      "2026-11-26",
      "2026-11-27",
      "2026-11-28",
      "2026-11-29",
    ]);
    // Sunday belongs to the week that started six days before.
    expect(keys(weekDays(at("2026-11-29T20:00:00Z"), ROME))[0]).toBe("2026-11-23");
    // A Monday starts its own week.
    expect(keys(weekDays(at("2026-11-23T00:10:00+01:00"), ROME))[0]).toBe("2026-11-23");
  });

  it("always builds 42 cells from the Monday before the 1st", () => {
    const november = monthDays(at("2026-11-26T09:42:00Z"), ROME);
    expect(november).toHaveLength(42);
    // 1 November 2026 is a Sunday, so the grid opens on Monday 26 October.
    expect(dayKey(november[0]!, ROME)).toBe("2026-10-26");
    expect(dayKey(november[41]!, ROME)).toBe("2026-12-06");
    // A month starting on Monday opens on its own 1st (June 2026).
    expect(dayKey(monthDays(at("2026-06-15T10:00:00Z"), ROME)[0]!, ROME)).toBe("2026-06-01");
    // Every cell is a distinct day even across the October clock change.
    expect(new Set(keys(monthDays(at("2026-10-10T10:00:00Z"), ROME))).size).toBe(42);
  });

  it("marks the month's days, today and weekends", () => {
    const cells = monthCells(at("2026-11-26T09:42:00Z"), ROME, [], at("2026-11-26T09:42:00Z"));
    expect(cells.filter((cell) => cell.inMonth)).toHaveLength(30);
    expect(cells.find((cell) => cell.isToday)?.key).toBe("2026-11-26");
    expect(cells.filter((cell) => cell.isWeekend)).toHaveLength(12);
  });

  it("moves by weeks and by months", () => {
    const anchor = at("2026-11-26T09:42:00Z");
    expect(dayKey(shiftAnchor(anchor, "week", 1, ROME), ROME)).toBe("2026-12-03");
    expect(dayKey(shiftAnchor(anchor, "week", -1, ROME), ROME)).toBe("2026-11-19");
    expect(dayKey(shiftAnchor(anchor, "month", 1, ROME), ROME)).toBe("2026-12-01");
    expect(dayKey(shiftAnchor(at("2026-01-31T10:00:00Z"), "month", 1, ROME), ROME)).toBe("2026-02-01");
    expect(dayKey(shiftAnchor(anchor, "month", -11, ROME), ROME)).toBe("2025-12-01");
  });
});

describe("daylight saving days", () => {
  it("gives the autumn day 25 hour rows and the spring day 23", () => {
    const autumn = hourMarks(at("2026-10-25T12:00:00Z"), ROME);
    expect(autumn).toHaveLength(25);
    expect(autumn.map((mark) => mark.hour).slice(0, 5)).toEqual([0, 1, 2, 2, 3]);
    const spring = hourMarks(at("2026-03-29T12:00:00Z"), ROME);
    expect(spring).toHaveLength(23);
    expect(spring.map((mark) => mark.hour).slice(0, 4)).toEqual([0, 1, 3, 4]);
    expect(hourMarks(at("2026-11-26T12:00:00Z"), ROME)).toHaveLength(24);
  });

  it("places by elapsed minutes, so 09:00 sits one row lower after the extra hour", () => {
    const day = at("2026-10-25T12:00:00Z");
    expect(timelineMinute(at("2026-10-25T08:00:00Z"), day, ROME)).toBe(10 * 60); // 09:00 CET
    const columns = weekColumns(day, ROME, [], at("2026-10-25T08:00:00Z"));
    expect(columns.find((column) => column.shifted)?.key).toBe("2026-10-25");
    expect(columns.find((column) => column.key === "2026-10-25")?.minutes).toBe(25 * 60);
  });
});

describe("clipping and placements", () => {
  it("clips a lesson that crosses midnight and flags the continuation", () => {
    const late = slot("late", "2026-11-26T21:30:00Z", "2026-11-26T23:30:00Z"); // 22:30–00:30 Rome
    expect(clipped(late.start, late.end, at("2026-11-26T12:00:00Z"), ROME)?.end.toISOString()).toBe("2026-11-26T23:00:00.000Z");
    const next = placements([late], at("2026-11-27T12:00:00Z"), ROME);
    expect(next).toHaveLength(1);
    expect(next[0]).toMatchObject({ top: 0, height: 30, continued: true });
    expect(placements([late], at("2026-11-26T12:00:00Z"), ROME)[0]).toMatchObject({ top: 22 * 60 + 30, height: 90, continued: false });
    expect(clipped(late.start, late.end, at("2026-11-28T12:00:00Z"), ROME)).toBeNull();
  });

  it("shares columns only inside a connected group of overlaps", () => {
    const day = at("2026-11-27T12:00:00Z");
    const result = placements(
      [
        slot("a", "2026-11-27T08:00:00Z", "2026-11-27T09:30:00Z"),
        slot("b", "2026-11-27T08:30:00Z", "2026-11-27T10:00:00Z"),
        slot("c", "2026-11-27T13:00:00Z", "2026-11-27T14:30:00Z"),
      ],
      day,
      ROME,
    );
    const byId = Object.fromEntries(result.map((placement) => [placement.item.id, placement]));
    expect(byId.a).toMatchObject({ column: 0, columnCount: 2, span: 1 });
    expect(byId.b).toMatchObject({ column: 1, columnCount: 2, span: 1 });
    // The afternoon lesson does not inherit the morning clash's narrow width.
    expect(byId.c).toMatchObject({ column: 0, columnCount: 1, span: 1 });
  });

  it("reuses a freed column and lets a block span free columns to its right", () => {
    const day = at("2026-11-27T12:00:00Z");
    const result = placements(
      [
        slot("long", "2026-11-27T07:00:00Z", "2026-11-27T11:00:00Z"),
        slot("first", "2026-11-27T07:30:00Z", "2026-11-27T08:30:00Z"),
        slot("third", "2026-11-27T07:45:00Z", "2026-11-27T08:15:00Z"),
        slot("after", "2026-11-27T09:00:00Z", "2026-11-27T10:00:00Z"),
      ],
      day,
      ROME,
    );
    const byId = Object.fromEntries(result.map((placement) => [placement.item.id, placement]));
    expect(byId.long).toMatchObject({ column: 0, columnCount: 3, span: 1 });
    expect(byId.first).toMatchObject({ column: 1, span: 1 });
    expect(byId.third).toMatchObject({ column: 2, span: 1 });
    // Column 1 is free again at 10:00 Rome, and nothing sits in column 2 then: span 2.
    expect(byId.after).toMatchObject({ column: 1, span: 2, columnCount: 3 });
  });

  it("orders equal starts longest first, then by id", () => {
    const result = placements(
      [slot("b", "2026-11-27T08:00:00Z", "2026-11-27T09:00:00Z"), slot("a", "2026-11-27T08:00:00Z", "2026-11-27T09:00:00Z"), slot("z", "2026-11-27T08:00:00Z", "2026-11-27T10:00:00Z")],
      at("2026-11-27T12:00:00Z"),
      ROME,
    );
    expect(result.map((placement) => [placement.item.id, placement.column])).toEqual([
      ["z", 0],
      ["a", 1],
      ["b", 2],
    ]);
  });

  it("stretches very short lessons to the minimum height, which also counts for overlaps", () => {
    const result = placements(
      [slot("short", "2026-11-27T08:00:00Z", "2026-11-27T08:10:00Z"), slot("next", "2026-11-27T08:20:00Z", "2026-11-27T09:00:00Z")],
      at("2026-11-27T12:00:00Z"),
      ROME,
      30,
    );
    expect(result[0]).toMatchObject({ height: 30, columnCount: 2 });
  });

  it("lists who overlaps whom", () => {
    const overlaps = overlapsOf([
      slot("a", "2026-11-27T08:00:00Z", "2026-11-27T09:30:00Z"),
      slot("b", "2026-11-27T08:30:00Z", "2026-11-27T10:00:00Z"),
      slot("c", "2026-11-27T10:00:00Z", "2026-11-27T11:00:00Z"),
    ]);
    expect(overlaps.get("a")?.map((item) => item.id)).toEqual(["b"]);
    expect(overlaps.get("b")?.map((item) => item.id)).toEqual(["a"]);
    expect(overlaps.has("c")).toBe(false);
  });
});

describe("time zone labels", () => {
  it("names the grid's zone", () => {
    expect(displayZoneLabel(at("2026-11-26T09:42:00Z"), ROME, "en-GB")).toMatch(/^Europe\/Rome · (CET|GMT\+1)$/);
  });

  it("shows a lesson's own calendar time only when its zone differs", () => {
    expect(originalTimeLabel(at("2026-11-26T08:00:00Z"), ROME, ROME, "en-GB")).toBeNull();
    expect(originalTimeLabel(at("2026-11-26T08:00:00Z"), "Europe/London", ROME, "en-GB")).toBe("26 Nov, 08:00 GMT · Europe/London");
    expect(originalTimeLabel(at("2026-11-26T08:00:00Z"), "Not/AZone", ROME, "en-GB")).toBeNull();
  });
});

describe("lessons from the dashboard", () => {
  async function lessonsFor(scenario: string) {
    const now = at("2026-11-26T09:42:00Z");
    const { app } = mockApp(scenario, { now });
    await loadAll(app);
    const state = app.store.getState();
    return calendarLessons({ dashboard: state.capture.dashboard, library: state.library.library, now });
  }

  it("maps statuses and keeps course tints stable", async () => {
    const lessons = await lessonsFor("default");
    const stat7 = lessons.find((lesson) => lesson.id === "uni-timetable:STAT-L07");
    expect(stat7).toMatchObject({ courseName: "Statistics", lectureId: "STAT-L07", tint: 1, recording: { status: "COMPLETED" }, transcription: { status: "RUNNING" } });
    expect(lessons.find((lesson) => lesson.id === "uni-timetable:MICRO-L05")?.recording.status).toBe("SCHEDULED");
    expect(tintFor("MICRO", ["STAT", "MICRO"])).toBe(2);
    expect(tintFor(null, ["STAT"])).toBe(5);
    expect(tintFor("NEW", [])).toBe(tintFor("NEW", []));
  });

  it("flags clashes, a course to pick and local edits", async () => {
    const lessons = await lessonsFor("workspace");
    const clash = lessons.filter((lesson) => lesson.clash).map((lesson) => lesson.id);
    expect(clash).toEqual(["uni-timetable:DIRCOM-L03", "special:corpfin-guest"]);
    expect(lessons.find((lesson) => lesson.id === "uni-timetable:seminar-1127")).toMatchObject({ needsCourse: true, tint: 5, recording: { status: "NEEDS_COURSE" } });
    expect(lessons.find((lesson) => lesson.localChanges)?.id).toBe("uni-timetable:CORPFIN-L04");
    const columns = weekColumns(at("2026-11-26T09:42:00Z"), ROME, lessons, at("2026-11-26T09:42:00Z"));
    const friday = columns.find((column) => column.key === "2026-11-27");
    expect(friday?.placements.filter((placement) => placement.columnCount === 2)).toHaveLength(2);
    expect(columns.find((column) => column.isToday)?.nowMinute).toBe(10 * 60 + 42);
    expect(firstVisibleHour(columns)).toBe(7);
  });

  it("caps month cells at three lessons with a remainder", async () => {
    const lessons = await lessonsFor("workspace");
    const many = [...lessons, ...lessons.map((lesson) => ({ ...lesson, id: `${lesson.id}:copy` }))];
    const friday = monthCells(at("2026-11-26T09:42:00Z"), ROME, many, at("2026-11-26T09:42:00Z")).find((cell) => cell.key === "2026-11-27");
    expect(friday?.visible).toHaveLength(3);
    expect(friday?.more).toBe(3);
  });
});

describe("schedule lesson helpers", () => {
  it("suggests the next full hour for 90 minutes", () => {
    expect(defaultSlot(at("2026-11-26T09:42:00Z"), ROME)).toEqual({ day: "2026-11-26", start: "11:00", end: "12:30" });
  });

  it("turns a wall time into an instant in the zone", () => {
    expect(wallTimeToIso("2026-11-27", "09:30", ROME)).toBe("2026-11-27T08:30:00Z");
    expect(wallTimeToIso("2026-07-01", "09:30", ROME)).toBe("2026-07-01T07:30:00Z");
    expect(wallTimeToIso("2026-11-27", "25:00", ROME)).toBeNull();
    expect(zonedParts(at("2026-11-27T08:30:00Z"), ROME).hour).toBe(9);
  });
});
