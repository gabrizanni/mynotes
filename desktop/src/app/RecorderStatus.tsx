import { Mic, MicOff } from "lucide-react";

import { Icon } from "../components";
import { cx } from "../design/cx";
import { useI18n } from "../i18n";
import { routeToHash, useAppState } from "../state";
import styles from "./Shell.module.css";
import { useNow } from "./useNow";

/** Sidebar recorder row: "Next: Statistics 10:15", the live clock, or the problem. */
export function RecorderStatus() {
  const { t, format } = useI18n();
  const dashboard = useAppState((state) => state.capture.dashboard);
  const now = useNow(1000);
  const href = routeToHash({ name: "settings", section: "recording" });
  if (!dashboard) return null;

  const runtime = dashboard.runtime;
  const live = dashboard.running === true && runtime.recording === true ? runtime.currentSession : null;
  const blocked = runtime.microphonePermission === "DENIED" || runtime.microphonePermission === "RESTRICTED" || (dashboard.enabled && dashboard.running === false);
  let content;
  if (live?.actualStart) {
    const elapsed = format.clock((now.getTime() - Date.parse(live.actualStart)) / 1000);
    content = (
      <>
        <span className={styles.liveDot} aria-hidden="true" />
        <span className={cx(styles.statusText, styles.statusLive)}>{t.nav.recorderLive(elapsed)}</span>
      </>
    );
  } else if (blocked) {
    content = (
      <>
        <Icon icon={MicOff} size={20} className={styles.statusProblem} />
        <span className={cx(styles.statusText, styles.statusProblem)}>{t.nav.recorderProblem}</span>
      </>
    );
  } else if (runtime.exporting) {
    content = (
      <>
        <Icon icon={Mic} size={20} />
        <span className={styles.statusText}>{t.nav.recorderSaving}</span>
      </>
    );
  } else {
    const next = dashboard.events.find((event) => event.eligibility === "SCHEDULED" && Date.parse(event.startsAt) > now.getTime());
    const name = next?.courseCode ? (dashboard.courses.find((course) => course.code === next.courseCode)?.name ?? next.title) : next?.title;
    const start = next ? new Date(next.startsAt) : null;
    const sameDay = start ? start.toDateString() === now.toDateString() : false;
    const label = !dashboard.enabled
      ? t.nav.recorderOff
      : next && name && start
        ? t.nav.recorderNext(name, sameDay ? format.time(start) : `${format.weekday(start)} ${format.time(start)}`)
        : t.nav.recorderIdle;
    content = (
      <>
        <Icon icon={dashboard.enabled ? Mic : MicOff} size={20} />
        <span className={styles.statusText}>{label}</span>
      </>
    );
  }
  return (
    <a className={styles.statusRow} href={href}>
      <span className="visually-hidden">{t.nav.recorderLabel}: </span>
      {content}
    </a>
  );
}
