import { describe, expect, it } from "vitest";

import { parseRoute, routeToHash, sectionOf, type Route } from "./routes";

const ROUTES: Route[] = [
  { name: "today" },
  { name: "calendar", view: "week", date: null },
  { name: "calendar", view: "month", date: "2026-11-26" },
  { name: "courses" },
  { name: "course", code: "STAT", tab: "notes" },
  { name: "library", course: "MICRO", q: "elasticity", unassigned: true },
  { name: "activity" },
  { name: "lesson", id: "STAT-L07" },
  { name: "settings", section: "diagnostics" },
  { name: "welcome", step: "first-notes" },
  { name: "gallery" },
];

describe("routes", () => {
  it.each(ROUTES)("round-trips $name through the hash", (route) => {
    expect(parseRoute(routeToHash(route))).toEqual(route);
  });

  it("falls back to Today on unknown paths and to defaults on unknown parts", () => {
    expect(parseRoute("#/nowhere")).toEqual({ name: "today" });
    expect(parseRoute("#/courses/STAT/unknown")).toEqual({ name: "course", code: "STAT", tab: "lessons" });
    expect(parseRoute("#/settings/unknown")).toEqual({ name: "settings", section: "assistant" });
  });

  it("serves the gallery at /__gallery", () => {
    expect(parseRoute("", "/__gallery")).toEqual({ name: "gallery" });
  });

  it("maps nested routes to their sidebar section", () => {
    expect(sectionOf({ name: "course", code: "STAT", tab: "lessons" })).toBe("courses");
    expect(sectionOf({ name: "lesson", id: "STAT-L07" })).toBe("library");
    expect(sectionOf({ name: "settings", section: "general" })).toBeNull();
  });
});
