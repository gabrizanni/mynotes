import { Check } from "lucide-react";

import { cx } from "../../design/cx";
import { useI18n } from "../../i18n";
import { Icon } from "../Icon";
import styles from "./StageStepper.module.css";

export interface Stage {
  id: string;
  label: string;
}

export interface StageTimingInput {
  startedAt: string;
  finishedAt: string | null;
}

export interface StageStepperProps {
  label: string;
  stages: readonly Stage[];
  /** Id of the running stage, or `"done"` when every stage finished. */
  current: string;
  /** Elapsed time per stage; never a percentage (UX-SPEC §3 Generate notes flow). */
  timings?: Readonly<Record<string, StageTimingInput>>;
  now: Date;
}

export function StageStepper({ label, stages, current, timings = {}, now }: StageStepperProps) {
  const { format } = useI18n();
  const currentIndex = current === "done" ? stages.length : stages.findIndex((stage) => stage.id === current);
  return (
    <ol className={styles.stepper} aria-label={label}>
      {stages.map((stage, index) => {
        const state = index < currentIndex ? "done" : index === currentIndex ? "current" : "upcoming";
        const timing = timings[stage.id];
        const end = timing?.finishedAt ? new Date(timing.finishedAt) : now;
        const seconds = timing ? (end.getTime() - new Date(timing.startedAt).getTime()) / 1000 : null;
        return (
          <li key={stage.id} className={cx(styles.stage, styles[state])} aria-current={state === "current" ? "step" : undefined}>
            <span className={styles.marker}>{state === "done" && <Icon icon={Check} size={14} />}</span>
            <span className={styles.label}>{stage.label}</span>
            {seconds !== null && state !== "upcoming" && <span className={styles.time}>{format.clock(seconds)}</span>}
          </li>
        );
      })}
    </ol>
  );
}
