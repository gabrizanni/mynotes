import { useCallback, useMemo } from "react";

import { useNow } from "../../app/useNow";
import { Banner, PageHeader } from "../../components";
import { cx } from "../../design/cx";
import { useI18n } from "../../i18n";
import type { Job } from "../../rpc";
import { shallowEqual, useActions, useAppState, type AppState } from "../../state";
import { FinishSetup } from "./FinishSetup";
import { deriveToday, type HeroAction } from "./model";
import { NeedsYou } from "./NeedsYou";
import { TodayHero } from "./TodayHero";
import { TodayTimeline } from "./TodayTimeline";
import styles from "./Today.module.css";

const selectInput = (state: AppState) => ({
  dashboard: state.capture.dashboard,
  queue: state.queue.status,
  library: state.library.library,
  config: state.config.config,
  jobsById: state.jobs.byId,
  jobOrder: state.jobs.order,
  load: state.capture.load,
  pending: state.capture.pendingAction,
});

/** Today (UX-SPEC §3): hero with one primary action, Needs you, today's lessons, Finish setup. */
export function TodayPage() {
  const { t, format } = useI18n();
  const actions = useActions();
  const now = useNow(1000);
  const input = useAppState(selectInput, shallowEqual);

  const jobs = useMemo(
    () => input.jobOrder.map((id) => input.jobsById[id]).filter((job): job is Job => job !== undefined),
    [input.jobOrder, input.jobsById],
  );
  const model = useMemo(
    () => deriveToday({ dashboard: input.dashboard, queue: input.queue, library: input.library, config: input.config, jobs, now }),
    [input.dashboard, input.queue, input.library, input.config, jobs, now],
  );

  const onAction = useCallback(
    (action: HeroAction) => {
      switch (action.kind) {
        case "stop":
          void actions.capture.stop().then(() => actions.ui.toast({ message: t.nav.recorderSaving, tone: "neutral" }));
          return;
        case "fix":
          // TODO(settings agent): call the OS privacy pane / capture restart from Settings › Recording.
          actions.ui.navigate({ name: "settings", section: "recording" });
          return;
        case "review":
        case "generate":
          if (action.lesson.lectureId) actions.ui.navigate({ name: "lesson", id: action.lesson.lectureId });
          return;
        case "record": {
          const stamp = new Date();
          void actions.capture.start({
            unassigned: true,
            title: t.today.action.recordingTitle(`${format.dayShort(stamp)} ${format.time(stamp)}`),
            durationMinutes: 180,
          });
          return;
        }
      }
    },
    [actions, t, format],
  );

  const header = <PageHeader title={t.today.title} subtitle={format.dayLong(now)} />;

  if (!model.ready) {
    return (
      <>
        {header}
        {input.load.status === "error" ? (
          <Banner tone="danger" action={{ label: t.common.retry, onSelect: () => void actions.capture.refresh() }}>
            {t.today.loadError}
          </Banner>
        ) : (
          <div className={styles.skeleton} aria-busy="true" aria-label={t.common.loading}>
            <div className={styles.skeletonBlock} style={{ height: 232 }} />
            <div className={styles.skeletonBlock} style={{ height: 160 }} />
          </div>
        )}
      </>
    );
  }

  const hasAside = model.setup.some((item) => !item.done);
  return (
    <>
      {header}
      <div className={styles.page}>
        <TodayHero hero={model.hero} now={now} busy={input.pending !== null} onAction={onAction} />
        <div className={cx(styles.grid, !hasAside && styles.single)}>
          <div className={styles.column}>
            <NeedsYou items={model.needsYou} total={model.needsYouTotal} />
            <TodayTimeline
              lessons={model.lessons}
              now={now}
              onImport={() => actions.ui.navigate({ name: "library", course: null, q: "", unassigned: false })}
            />
          </div>
          {hasAside && (
            <aside aria-label={t.today.finishSetup.title}>
              <FinishSetup items={model.setup} />
            </aside>
          )}
        </div>
      </div>
    </>
  );
}
