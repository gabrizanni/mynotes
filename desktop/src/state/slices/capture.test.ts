import { describe, expect, it } from "vitest";

import { loadAll, mockApp } from "../../../tests/unit/helpers";

const SOURCE = { url: "https://timetable.example.edu/statistics.ics", timezone: "Europe/Rome", periodStart: "2026-09-01", periodEnd: "2027-01-31" };

describe("capture actions on the job lane", () => {
  it("connects a calendar once its job ends CONNECTED", async () => {
    const { app } = mockApp();
    await loadAll(app);
    const result = await app.actions.capture.connectCalendar(SOURCE);
    expect(result).toMatchObject({ ok: true, value: { status: "CONNECTED" } });
    app.rpc.close();
  });

  it("reports a calendar the server could not read as a failure, not as connected", async () => {
    const { app } = mockApp("schedule-error");
    await loadAll(app);
    const result = await app.actions.capture.connectCalendar(SOURCE);
    expect(result).toEqual({ ok: false, error: "GOOGLE_FEED_UNAVAILABLE" });
    app.rpc.close();
  });

  it("turns the recorder on and off and syncs through jobs", async () => {
    const { app } = mockApp();
    await loadAll(app);
    expect(await app.actions.capture.setEnabled(false)).toMatchObject({ ok: true, value: { status: "DISABLED" } });
    expect(app.store.getState().capture.dashboard?.enabled).toBe(false);
    expect(await app.actions.capture.setEnabled(true)).toMatchObject({ ok: true, value: { status: "ENABLED" } });
    expect(await app.actions.capture.syncCalendars("timetable")).toMatchObject({ ok: true, value: { status: "SYNCED" } });
    const methods = (await app.rpc.call("job.list")).jobs.map((job) => job.method);
    expect(methods).toEqual(expect.arrayContaining(["capture.disable", "capture.enable", "capture.sync"]));
    app.rpc.close();
  });
});
