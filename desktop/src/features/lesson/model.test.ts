import { describe, expect, it } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";
import type { Job } from "../../rpc";
import {
  activeSegment,
  deriveLesson,
  findMatches,
  highlightParts,
  initialPlayer,
  keyBelongsToControl,
  parseTranscript,
  playerKey,
  playerReducer,
  stepMatch,
  type LessonDetail,
} from "./model";

async function lessonFor(scenario: string, id: string): Promise<LessonDetail | null> {
  const { app } = mockApp(scenario);
  await loadAll(app);
  const state = app.store.getState();
  const jobs = state.jobs.order.map((jobId) => state.jobs.byId[jobId]).filter((job): job is Job => job !== undefined);
  return deriveLesson(id, { library: state.library.library, queue: state.queue.status, dashboard: state.capture.dashboard, config: state.config.config, jobs });
}

describe("lesson detail", () => {
  it("is ready with a transcript and shows the latest notes job", async () => {
    const lesson = await lessonFor("default", "STAT-L06");
    expect(lesson).toMatchObject({ state: "ready", courseName: "Statistics", number: 6, transcriptId: "tstat06" });
    expect(lesson?.chips).toEqual({
      recording: { kind: "recording", status: "COMPLETED" },
      transcript: { kind: "transcription", status: "COMPLETED" },
      notes: { kind: "notes", status: "COMPLETED" },
    });
  });

  it("is transcribing while the queue runs its audio", async () => {
    const lesson = await lessonFor("default", "STAT-L07");
    expect(lesson?.state).toBe("transcribing");
    expect(lesson?.transcription?.elapsed_seconds).toBe(412);
    expect(lesson?.chips.transcript.status).toBe("RUNNING");
  });

  it("asks which recording to use when there are two", async () => {
    const lesson = await lessonFor("course", "CORPFIN-L02");
    expect(lesson?.state).toBe("chooseSource");
    expect(lesson?.recordings).toHaveLength(2);
  });

  it("shows the drop zone for a lesson without audio, even one known only to the calendar", async () => {
    expect((await lessonFor("course", "MICRO-L03"))?.state).toBe("noAudio");
    const fromCalendar = await lessonFor("default", "STAT-L08");
    expect(fromCalendar).toMatchObject({ state: "noAudio", courseName: "Statistics", number: 8 });
    expect(await lessonFor("default", "NOPE-L01")).toBeNull();
  });

  it("flags a transcript that needs a closer look", async () => {
    expect((await lessonFor("default", "DIRCOM-L02"))?.state).toBe("transcriptProblem");
  });
});

describe("transcript search", () => {
  const segments = [{ text: "La società per azioni è una società di capitali." }, { text: "Nothing here." }, { text: "Società semplice" }];

  it("ignores case and accents, and maps offsets back to the original text", () => {
    const matches = findMatches(segments, "SOCIETA");
    expect(matches).toEqual([
      { segment: 0, start: 3, end: 10 },
      { segment: 0, start: 28, end: 35 },
      { segment: 2, start: 0, end: 7 },
    ]);
    expect(segments[0]?.text.slice(3, 10)).toBe("società");
  });

  it("needs two characters before it searches", () => {
    expect(findMatches(segments, " a ")).toEqual([]);
  });

  it("steps through matches with wrap-around", () => {
    expect(stepMatch(-1, 3, 1)).toBe(0);
    expect(stepMatch(-1, 3, -1)).toBe(2);
    expect(stepMatch(2, 3, 1)).toBe(0);
    expect(stepMatch(0, 3, -1)).toBe(2);
    expect(stepMatch(0, 0, 1)).toBe(-1);
  });

  it("splits a segment into plain and highlighted parts", () => {
    const matches = findMatches(segments, "società");
    expect(highlightParts(segments[0]?.text ?? "", matches, 0)).toEqual([
      { text: "La ", match: null },
      { text: "società", match: 0 },
      { text: " per azioni è una ", match: null },
      { text: "società", match: 1 },
      { text: " di capitali.", match: null },
    ]);
    expect(highlightParts("Nothing here.", matches, 1)).toEqual([{ text: "Nothing here.", match: null }]);
  });

  it("finds the segment playing at a position", () => {
    const timed = [{ start_seconds: 0 }, { start_seconds: 21 }, { start_seconds: 48 }];
    expect(activeSegment(timed, 0)).toBe(0);
    expect(activeSegment(timed, 30)).toBe(1);
    expect(activeSegment(timed, 500)).toBe(2);
    expect(activeSegment([], 10)).toBe(-1);
    // Header lines and plain text have no timestamp: they never play and never stop the scan.
    expect(activeSegment([{ start_seconds: null }, { start_seconds: 5 }, { start_seconds: 12 }], 6)).toBe(1);
  });
});

describe("transcript text", () => {
  it("parses the lines MyNotes writes (asr.write_transcript_text)", () => {
    const text = [
      "Timestamp indicativi. Trascrizione automatica.",
      "[00:00:05–00:00:20] professor: Good morning: today, hypothesis tests.",
      "[01:02:03–01:02:09] UNKNOWN [passaggio incerto]: maybe a question",
      "",
    ].join("\r\n");
    expect(parseTranscript(text)).toEqual([
      { start_seconds: null, end_seconds: null, speaker: null, text: "Timestamp indicativi. Trascrizione automatica.", uncertain: false },
      { start_seconds: 5, end_seconds: 20, speaker: "professor", text: "Good morning: today, hypothesis tests.", uncertain: false },
      { start_seconds: 3723, end_seconds: 3729, speaker: null, text: "maybe a question", uncertain: true },
    ]);
  });

  it("keeps a plain TXT transcript as untimed lines that search still finds", () => {
    const segments = parseTranscript("Prima riga della lezione.\n\n  Seconda riga: la società.  \n");
    expect(segments.map((segment) => [segment.start_seconds, segment.text])).toEqual([
      [null, "Prima riga della lezione."],
      [null, "Seconda riga: la società."],
    ]);
    expect(findMatches(segments, "societa")).toEqual([{ segment: 1, start: 17, end: 24 }]);
    expect(parseTranscript("")).toEqual([]);
  });
});

describe("audio player", () => {
  it("plays, skips 15 s within bounds and changes speed", () => {
    let state = initialPlayer(100);
    state = playerReducer(state, { type: "toggle" });
    expect(state.playing).toBe(true);
    state = playerReducer(state, { type: "skip", by: -15 });
    expect(state.position).toBe(0);
    state = playerReducer(state, { type: "seek", to: 95 });
    state = playerReducer(state, { type: "skip", by: 15 });
    expect(state.position).toBe(100);
    state = playerReducer(state, { type: "rate", rate: 1.5 });
    expect(state.rate).toBe(1.5);
  });

  it("stops at the end and restarts from the top", () => {
    let state = playerReducer(initialPlayer(60), { type: "toggle" });
    state = playerReducer(state, { type: "tick", position: 61 });
    expect(state).toMatchObject({ position: 60, playing: false });
    state = playerReducer(state, { type: "toggle" });
    expect(state).toMatchObject({ position: 0, playing: true });
  });

  it("maps Space and the arrow keys, and leaves fields and buttons alone", () => {
    expect(playerKey(" ")).toEqual({ type: "toggle" });
    expect(playerKey("ArrowLeft")).toEqual({ type: "skip", by: -15 });
    expect(playerKey("ArrowRight")).toEqual({ type: "skip", by: 15 });
    expect(playerKey("a")).toBeNull();
    const input = document.createElement("input");
    const button = document.createElement("button");
    expect(keyBelongsToControl(input, "ArrowLeft")).toBe(true);
    expect(keyBelongsToControl(button, " ")).toBe(true);
    expect(keyBelongsToControl(button, "ArrowLeft")).toBe(false);
    expect(keyBelongsToControl(document.body, " ")).toBe(false);
  });
});
