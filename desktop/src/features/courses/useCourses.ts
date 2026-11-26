import { useMemo } from "react";

import { useNow } from "../../app/useNow";
import type { Job } from "../../rpc";
import { shallowEqual, useAppState, type AppState } from "../../state";
import { useRpcData } from "../lesson/useRpcData";
import type { CoursesInput } from "./model";

const selectInput = (state: AppState) => ({
  config: state.config.config,
  configLoad: state.config.load,
  dashboard: state.capture.dashboard,
  library: state.library.library,
  queue: state.queue.status,
  jobsById: state.jobs.byId,
  jobOrder: state.jobs.order,
});

/** Everything the course screens derive from: the store plus `notes.history` (all courses). */
export function useCoursesInput(): { input: CoursesInput; ready: boolean; failed: boolean; reloadHistory: () => void; historyStatus: "loading" | "ready" | "error" } {
  const state = useAppState(selectInput, shallowEqual);
  const now = useNow(30_000);
  const history = useRpcData("notes.history", {}, "all");
  const jobs = useMemo(() => state.jobOrder.map((id) => state.jobsById[id]).filter((job): job is Job => job !== undefined), [state.jobOrder, state.jobsById]);
  const backups = useMemo(() => (history.status === "ready" ? history.data.data : []), [history]);
  const input = useMemo<CoursesInput>(
    () => ({ config: state.config, dashboard: state.dashboard, library: state.library, queue: state.queue, jobs, backups, now }),
    [state.config, state.dashboard, state.library, state.queue, jobs, backups, now],
  );
  return {
    input,
    ready: state.config !== null || state.dashboard !== null,
    failed: state.config === null && state.configLoad.status === "error",
    reloadHistory: history.reload,
    historyStatus: history.status,
  };
}
