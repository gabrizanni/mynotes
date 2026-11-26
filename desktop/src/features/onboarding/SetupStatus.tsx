import { CircleAlert, CircleCheck, Download, WifiOff } from "lucide-react";

import { Banner, Icon, Popover, ProgressBar } from "../../components";
import { useT, type Messages } from "../../i18n";
import { useOnboarding } from "./controller";
import { megabytes, type SetupView } from "./model";
import styles from "./Onboarding.module.css";

export function stageLabel(t: Messages, view: SetupView): string {
  const stage = view.stage;
  const component = view.components.find((item) => item.title === view.stageTitle);
  if (component) return t.onboarding.setup.components[component.id] ?? component.title;
  if (stage && t.onboarding.setup.stages[stage]) return t.onboarding.setup.stages[stage] ?? stage;
  return view.stageTitle ?? t.onboarding.setup.running;
}

export function triggerLabel(t: Messages, view: SetupView): string {
  switch (view.phase) {
    case "waiting":
      return t.onboarding.setup.waiting;
    case "failed":
      return t.onboarding.setup.failed;
    case "complete":
      return t.onboarding.setup.complete;
    case "idle":
    case "running":
      return view.percent === null ? t.onboarding.setup.running : t.onboarding.settingUp(view.percent);
  }
}

/** Setup details: stage, measured MB, data age, and the one fix when it stops. */
export function SetupDetails() {
  const t = useT();
  const { setup, record, startSetup } = useOnboarding();
  const s = t.onboarding.setup;
  const hasBytes = setup.bytesTotal !== null && setup.bytesTotal > 0 && setup.bytesDone !== null;
  return (
    <div className={styles.stack}>
      {setup.phase === "failed" && (
        <Banner tone="danger" action={record.provider ? { label: s.retry, onSelect: () => void startSetup(record.provider ?? "claude") } : undefined}>
          {setup.error ? s.failedBody(setup.error) : s.failedGeneric}
        </Banner>
      )}
      {setup.phase === "waiting" && <Banner tone="info">{s.waitingBody}</Banner>}
      {setup.phase !== "complete" && hasBytes && (
        <ProgressBar
          label={stageLabel(t, setup)}
          value={setup.bytesDone ?? 0}
          max={setup.bytesTotal ?? 1}
          valueText={s.bytes(megabytes(setup.bytesDone ?? 0), megabytes(setup.bytesTotal ?? 0))}
        />
      )}
      {setup.phase !== "complete" && !hasBytes && setup.phase !== "failed" && <p className="type-body">{stageLabel(t, setup)}</p>}
      <div className={styles.statusMeta}>
        {setup.overallTotal !== null && setup.overallDone !== null && <span>{s.total(megabytes(setup.overallDone), megabytes(setup.overallTotal))}</span>}
        {setup.secondsSinceData !== null && setup.phase !== "complete" && <span>{s.lastData(setup.secondsSinceData)}</span>}
      </div>
    </div>
  );
}

/** Header status: "Setting up · 62%", expandable for the details (UX-SPEC §2 Install in the background). */
export function SetupStatus({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const t = useT();
  const { setup } = useOnboarding();
  if (setup.phase === "idle") return null;
  const icon = setup.phase === "failed" ? CircleAlert : setup.phase === "waiting" ? WifiOff : setup.phase === "complete" ? CircleCheck : Download;
  const tone = setup.phase === "failed" ? styles.statusProblem : setup.phase === "complete" ? styles.statusDone : undefined;
  return (
    <Popover
      label={
        <span className={styles.statusTrigger}>
          <span className="visually-hidden">{t.onboarding.setup.panelTitle}: </span>
          {triggerLabel(t, setup)}
        </span>
      }
      triggerProps={{ variant: "ghost", size: "sm", icon, className: tone }}
      title={t.onboarding.setup.panelTitle}
      align="end"
      defaultOpen={defaultOpen}
    >
      <div className={styles.statusPanel}>
        <p className={styles.statusTitle}>
          {setup.phase === "complete" && <Icon icon={CircleCheck} size={16} className={styles.success} />} {t.onboarding.setup.panelTitle}
        </p>
        <SetupDetails />
        {setup.phase !== "complete" && <p className={styles.previewCaption}>{t.onboarding.setup.keepGoing}</p>}
      </div>
    </Popover>
  );
}
