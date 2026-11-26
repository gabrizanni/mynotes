import { describe, expect, it } from "vitest";

import { en } from "../../i18n/en";
import { it as italian } from "../../i18n/it";
import { buildCommands, filterCommands, type PaletteInput } from "./model";

const base: PaletteInput = {
  t: en,
  courses: [
    { code: "STAT", name: "Statistics" },
    { code: "DIRCOM", name: "Diritto commerciale" },
  ],
  recording: false,
  resolvedTheme: "light",
  shortcut: (key) => `Ctrl+${key}`,
  mode: "all",
};

describe("command palette", () => {
  it("lists actions, sections with their shortcuts and course notes", () => {
    const commands = buildCommands(base);
    expect(commands.slice(0, 4).map((command) => command.id)).toEqual(["record", "import", "undo", "theme"]);
    expect(commands.find((command) => command.id === "go:activity")).toMatchObject({ label: "Go to Activity", shortcut: "Ctrl+5" });
    expect(commands.find((command) => command.id === "settings:assistant")?.shortcut).toBe("Ctrl+,");
    expect(commands.find((command) => command.id === "notes:STAT")?.action).toEqual({ kind: "navigate", route: { name: "course", code: "STAT", tab: "notes" } });
    expect(commands.find((command) => command.id === "theme")?.action).toEqual({ kind: "theme", theme: "dark" });
  });

  it("offers Stop instead of Record while recording, and the light theme in dark mode", () => {
    const commands = buildCommands({ ...base, recording: true, resolvedTheme: "dark" });
    expect(commands[0]?.id).toBe("stop");
    expect(commands.find((command) => command.id === "theme")?.label).toBe("Switch to light theme");
  });

  it("asks which course to undo in a second step", () => {
    expect(buildCommands({ ...base, mode: "undo" }).map((command) => command.action)).toEqual([
      { kind: "undo", course: "STAT", name: "Statistics" },
      { kind: "undo", course: "DIRCOM", name: "Diritto commerciale" },
    ]);
  });

  it("matches every word, prefers label prefixes, ignores accents and case", () => {
    const commands = buildCommands(base);
    expect(filterCommands(commands, "stat").map((command) => command.id)[0]).toBe("notes:STAT");
    expect(filterCommands(commands, "go lib").map((command) => command.id)).toEqual(["go:library"]);
    expect(filterCommands(commands, "word dircom").map((command) => command.id)).toEqual(["notes:DIRCOM"]);
    expect(filterCommands(commands, "zzz")).toEqual([]);
    expect(filterCommands(commands, "  ")).toHaveLength(commands.length);
    const it = buildCommands({ ...base, t: italian });
    expect(filterCommands(it, "attivita").map((command) => command.id)).toContain("go:activity");
  });
});
