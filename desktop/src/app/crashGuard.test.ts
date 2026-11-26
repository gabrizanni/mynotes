import { beforeEach, describe, expect, it, vi } from "vitest";

import { installCrashGuard, MAX_COLLECTED } from "./crashGuard";

beforeEach(() => {
  document.body.innerHTML = "";
  delete window.__mynotesErrors;
});

describe("crash guard", () => {
  it("shows a readable screen instead of a blank window when boot fails", () => {
    const root = document.body.appendChild(document.createElement("div"));
    const log = vi.fn();
    const guard = installCrashGuard(window, root, { log });
    guard.report("boot", new TypeError("connect() failed"));
    expect(root.querySelector("[data-crash-screen] h1")?.textContent).toBe("This window ran into a problem");
    expect(root.textContent).toContain("TypeError: connect() failed");
    expect(root.querySelector("button")?.textContent).toBe("Reload window");
    expect(log).toHaveBeenCalledWith("[Webview] boot — TypeError: connect() failed");
    expect(window.__mynotesErrors?.map((error) => error.kind)).toEqual(["boot"]);
  });

  it("collects uncaught errors and rejections, and leaves a rendered page alone", () => {
    const root = document.body.appendChild(document.createElement("div"));
    root.appendChild(document.createElement("p")).textContent = "Today";
    installCrashGuard(window, root, { log: () => undefined });
    window.dispatchEvent(new ErrorEvent("error", { error: new Error("click handler"), message: "click handler" }));
    const rejection = new Event("unhandledrejection") as PromiseRejectionEvent;
    Object.defineProperty(rejection, "reason", { value: "rpc refused" });
    window.dispatchEvent(rejection);
    expect(window.__mynotesErrors?.map((error) => [error.kind, error.message])).toEqual([
      ["error", "Error: click handler"],
      ["rejection", "rpc refused"],
    ]);
    expect(root.textContent).toBe("Today");
  });

  it("keeps the last errors only", () => {
    const guard = installCrashGuard(window, null, { log: () => undefined });
    for (let n = 0; n < MAX_COLLECTED + 5; n += 1) guard.report("error", `error ${n}`);
    expect(window.__mynotesErrors).toHaveLength(MAX_COLLECTED);
    expect(window.__mynotesErrors?.[0]?.message).toBe("error 5");
  });
});
