import { beforeEach, describe, expect, it, vi } from "vitest";

const invoke = vi.fn<(command: string, args?: unknown) => Promise<unknown>>();
const convertFileSrc = vi.fn((path: string) => `asset://localhost/${encodeURIComponent(path)}`);

vi.mock("@tauri-apps/api/core", () => ({
  invoke: (command: string, args?: unknown) => invoke(command, args),
  convertFileSrc: (path: string) => convertFileSrc(path),
}));
vi.mock("@tauri-apps/api/event", () => ({ listen: () => Promise.resolve(() => undefined) }));

const native = await import("./native");

beforeEach(() => {
  invoke.mockReset();
  convertFileSrc.mockClear();
});

describe("native commands", () => {
  it("asks Rust to allow a recording before building its asset URL", async () => {
    invoke.mockResolvedValue("/srv/rec/STAT-L01.m4a");
    const url = await native.playableUrl("/srv/rec/../rec/STAT-L01.m4a");
    expect(invoke).toHaveBeenCalledWith("audio_source", { path: "/srv/rec/../rec/STAT-L01.m4a" });
    expect(convertFileSrc).toHaveBeenCalledWith("/srv/rec/STAT-L01.m4a"); // The canonical path the scope allowed.
    expect(url).toBe("asset://localhost/%2Fsrv%2Frec%2FSTAT-L01.m4a");
  });

  it("builds no URL for a recording Rust refuses", async () => {
    invoke.mockRejectedValue({ code: -32602, message: "the path is outside your MyNotes folders" });
    await expect(native.playableUrl("/etc/passwd")).rejects.toThrow(/outside/);
    expect(convertFileSrc).not.toHaveBeenCalled();
  });

  it("opens links and settings through their Rust commands", async () => {
    invoke.mockResolvedValue(null);
    await native.openExternal("https://github.com/openai/codex");
    expect(invoke).toHaveBeenLastCalledWith("open_external", { url: "https://github.com/openai/codex" });
    await native.openSystemSettings("microphone");
    expect(invoke).toHaveBeenLastCalledWith("open_system_settings", { pane: "microphone" });
  });
});
