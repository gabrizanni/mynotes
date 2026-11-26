import { describe, expect, it } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";
import type { AudioInspection, Library } from "../../rpc";
import { audioPaths, deriveLibrary, importItems, normalize, parseTarget, planImport, targetValue, type LibraryFilters } from "./model";

const none: LibraryFilters = { q: "", course: null, unassigned: false, hidden: false };

async function libraryFor(scenario: string) {
  const { app } = mockApp(scenario);
  await loadAll(app);
  const state = app.store.getState();
  return { library: state.library.library, courses: state.capture.dashboard?.courses ?? [] };
}

describe("Library view-model", () => {
  it("lists lessons newest first with their recordings and transcripts", async () => {
    const { library, courses } = await libraryFor("default");
    const model = deriveLibrary(library, courses, none);
    expect(model.items.map((item) => item.lectureId)).toEqual(["STAT-L07", "CORPFIN-L03", "MICRO-L04", "DIRCOM-L02", "STAT-L06"]);
    expect(model.items[0]).toMatchObject({ courseName: "Statistics", lessonNumber: 7, transcription: "RUNNING", duration: 5521.6 });
    expect(model.items.find((item) => item.lectureId === "STAT-L06")).toMatchObject({ transcription: "COMPLETED", imported: false });
  });

  it("filters by course, by Needs a course and by Hidden, and counts each", async () => {
    const { library, courses } = await libraryFor("workspace");
    const all = deriveLibrary(library, courses, none);
    expect(all.counts).toEqual({ unassigned: 1, hidden: 1 });
    expect(all.items.some((item) => item.title === "Microphone test")).toBe(false);
    expect(deriveLibrary(library, courses, { ...none, unassigned: true }).items.map((item) => item.title)).toEqual(["Recording 19 Nov 14:02"]);
    expect(deriveLibrary(library, courses, { ...none, hidden: true }).items.map((item) => item.title)).toEqual(["Microphone test"]);
    expect(deriveLibrary(library, courses, { ...none, course: "STAT" }).items.map((item) => item.lectureId)).toEqual(["STAT-L07", "STAT-L06", "STAT-L05"]);
    // A transcript without audio still shows as transcribed.
    expect(deriveLibrary(library, courses, { ...none, course: "STAT" }).items[2]).toMatchObject({ transcription: "COMPLETED", recordings: [] });
  });

  it("searches every word across names, codes and files, ignoring accents", async () => {
    const { library, courses } = await libraryFor("default");
    expect(deriveLibrary(library, courses, { ...none, q: "micro" }).items.map((item) => item.lectureId)).toEqual(["MICRO-L04"]);
    expect(deriveLibrary(library, courses, { ...none, q: "statistics 2026-11-24" }).items.map((item) => item.lectureId)).toEqual(["STAT-L06"]);
    expect(deriveLibrary(library, courses, { ...none, q: "dircom-l02" }).items).toHaveLength(1);
    expect(deriveLibrary(library, courses, { ...none, q: "nothing like this" }).items).toEqual([]);
    expect(normalize("Perché È")).toBe("perche e");
  });

  it("keeps loose recordings and transcripts as their own rows", () => {
    const library: Library = {
      recordings: [{ id: "r1", path: "/x/a.m4a", title: "Loose", status: "READY", created_at: "2026-11-20T10:00:00Z", origin: "Importato" }],
      transcripts: [{ id: "t1", path: "/x/b.txt", title: "b.txt", status: "COMPLETED", created_at: "2026-11-21T10:00:00Z" }],
    };
    const model = deriveLibrary(library, [], none);
    expect(model.items.map((item) => [item.id, item.unassigned, item.imported])).toEqual([
      ["transcript:t1", true, false],
      ["recording:r1", true, true],
    ]);
  });
});

describe("import plan", () => {
  const file = (name: string, recordedAt: string | null, duplicate = false): AudioInspection => ({ path: `/in/${name}`, name, size: 1, duration: 60, recorded_at: recordedAt, duplicate });

  it("joins an open lesson recorded the same day, skips duplicates, leaves the rest unassigned", async () => {
    const { library } = await libraryFor("workspace");
    const rows = planImport([file("a.m4a", "2026-11-17T09:01:12+01:00"), file("b.mp3", null), file("c.m4a", "2026-11-24T08:00:00+01:00", true)], library);
    expect(rows.map((row) => [targetValue(row.target), row.skip])).toEqual([
      ["lesson:STAT-L05", false],
      ["none", false],
      ["none", true],
    ]);
    expect(importItems(rows)).toEqual([{ path: "/in/a.m4a", target: "STAT-L05", recorded_at: "2026-11-17T09:01:12+01:00" }, { path: "/in/b.mp3" }]);
  });

  it("never offers the same open lesson twice", async () => {
    const { library } = await libraryFor("workspace");
    const rows = planImport([file("a.m4a", "2026-11-17T09:00:00+01:00"), file("b.m4a", "2026-11-17T11:00:00+01:00")], library);
    expect(rows.map((row) => targetValue(row.target))).toEqual(["lesson:STAT-L05", "none"]);
  });

  it("round-trips targets and keeps only audio from a drop", () => {
    for (const value of ["none", "lesson:STAT-L05", "course:MICRO"]) expect(targetValue(parseTarget(value))).toBe(value);
    expect(importItems([{ file: file("x.m4a", null), target: { kind: "course", courseCode: "MICRO" }, skip: false }])).toEqual([{ path: "/in/x.m4a", course: "MICRO" }]);
    expect(audioPaths(["/a/lecture.M4A", "/a/slides.pdf", "/a/notes.txt", "/a/voice.wav"])).toEqual(["/a/lecture.M4A", "/a/voice.wav"]);
  });
});
