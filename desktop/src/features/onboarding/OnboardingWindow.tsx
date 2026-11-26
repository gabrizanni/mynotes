import { Check, Circle, CircleCheck, LoaderCircle } from "lucide-react";
import { Fragment, useEffect, useRef } from "react";

import logo from "../../assets/brand/mynotes-logo-256.png";
import { useHashRoute } from "../../app/useHashRoute";
import { Banner, Icon } from "../../components";
import { useT } from "../../i18n";
import type { Route } from "../../state/routes";
import { shallowEqual, useActions, useAppState } from "../../state";
import { OnboardingProvider, backendProgress, useOnboarding } from "./controller";
import { draftChecklist, megabytes, mustWait, redirectFor, resumeStep, SEGMENTS, segmentStates, shouldOpenOnboarding, NO_BACKEND, type StepId } from "./model";
import styles from "./Onboarding.module.css";
import { SetupStatus } from "./SetupStatus";
import { StepLayout } from "./StepLayout";
import { CourseStep, StudiesStep, StyleStep } from "./steps/CourseSteps";
import { FilesStep } from "./steps/FilesStep";
import { MicrophoneStep } from "./steps/MicrophoneStep";
import { AutomaticStep, FirstNotesStep, ReadyStep } from "./steps/RecordingSteps";
import { ScheduleStep } from "./steps/ScheduleStep";
import { AssistantStep, LanguageStep, PrivacyStep, SignInStep } from "./steps/SetupSteps";
import { browserStorage, loadRecord } from "./storage";

function Segments({ step }: { step: StepId }) {
  const t = useT();
  const states = segmentStates(step);
  return (
    <nav aria-label={t.onboarding.window.progress}>
      <ol className={styles.segments} role="list">
        {SEGMENTS.map((segment, index) => {
          const state = states[segment];
          return (
            <Fragment key={segment}>
              {index > 0 && <li aria-hidden="true" className={styles.segmentLine} />}
              <li className={styles.segment} data-state={state} aria-current={state === "current" ? "step" : undefined}>
                {state === "done" && <Icon icon={Check} size={14} className={styles.segmentCheck} />}
                {t.onboarding.segments[segment]}
                {state === "done" && <span className="visually-hidden">, {t.onboarding.window.segmentDone}</span>}
              </li>
            </Fragment>
          );
        })}
      </ol>
    </nav>
  );
}

/** "Almost there, finishing downloads": shown in place of a step that needs the backend. */
function AlmostThere() {
  const t = useT();
  const { setup, record, queue, retryQueue, startSetup, courseName } = useOnboarding();
  const w = t.onboarding.wait;
  const labels = { goalLabel: (goal: keyof typeof t.onboarding.studies.goals) => t.onboarding.studies.goals[goal], goalsPrefix: t.onboarding.studies.goalsPrefix };
  const downloaded = setup.phase === "complete" || (setup.overallTotal !== null && setup.overallDone !== null && setup.overallDone >= setup.overallTotal);
  const installing = !downloaded ? "waiting" : setup.phase === "complete" ? "done" : "running";
  const items = [
    {
      id: "downloads",
      title: w.downloads,
      state: downloaded ? "done" : setup.phase === "failed" ? "waiting" : "running",
      detail: setup.overallTotal !== null && setup.overallDone !== null && !downloaded ? t.onboarding.setup.total(megabytes(setup.overallDone), megabytes(setup.overallTotal)) : null,
    },
    { id: "install", title: w.install, state: installing, detail: null },
    ...draftChecklist(record, labels, queue.running).map((item) => ({
      id: item.id,
      title: item.id === "course" ? w.save.course(courseName) : w.save[item.id],
      state: item.state,
      detail: null,
    })),
  ];
  const icons = { done: CircleCheck, running: LoaderCircle, waiting: Circle } as const;
  return (
    <StepLayout title={w.title} lede={w.lede} primary={null}>
      {setup.phase === "failed" && (
        <Banner tone="danger" action={{ label: t.onboarding.setup.retry, onSelect: () => void startSetup(record.provider ?? "claude") }}>
          {setup.error ? t.onboarding.setup.failedBody(setup.error) : t.onboarding.setup.failedGeneric}
        </Banner>
      )}
      {setup.phase === "waiting" && <Banner tone="info">{t.onboarding.setup.waitingBody}</Banner>}
      {queue.error !== null && (
        <Banner tone="danger" action={{ label: t.common.retry, onSelect: retryQueue }}>
          {w.saveFailed}
        </Banner>
      )}
      <section aria-labelledby="wait-checklist" aria-live="polite">
        <h2 id="wait-checklist" className="visually-hidden">
          {w.checklist}
        </h2>
        <ul className={styles.checklist} role="list">
          {items.map((item) => {
            const state = item.state === "done" || item.state === "running" ? item.state : "waiting";
            return (
              <li key={item.id} className={styles.checkItem} data-state={state}>
                <Icon icon={icons[state]} size={20} className={state === "running" ? `${styles.checkIcon} ${styles.spin}` : styles.checkIcon} />
                <span className={styles.checkText}>
                  <span className={styles.checkTitle}>{item.title}</span>
                  {item.detail && <span className={styles.checkDetail}>{item.detail}</span>}
                </span>
                <span className={styles.checkState}>{w.state[state]}</span>
              </li>
            );
          })}
        </ul>
      </section>
      <p className={styles.note}>{w.auto}</p>
    </StepLayout>
  );
}

function StepView({ step }: { step: StepId }) {
  switch (step) {
    case "language":
      return <LanguageStep />;
    case "assistant":
      return <AssistantStep />;
    case "sign-in":
      return <SignInStep />;
    case "privacy":
      return <PrivacyStep />;
    case "studies":
      return <StudiesStep />;
    case "course":
      return <CourseStep />;
    case "style":
      return <StyleStep />;
    case "files":
      return <FilesStep />;
    case "schedule":
      return <ScheduleStep />;
    case "microphone":
      return <MicrophoneStep />;
    case "automatic":
      return <AutomaticStep />;
    case "first-notes":
      return <FirstNotesStep />;
    case "ready":
      return <ReadyStep />;
  }
}

function Body() {
  const { step, record, backend, setup, pending, courseCode, goTo } = useOnboarding();
  const redirect = redirectFor(step, record, backend, courseCode !== null);
  useEffect(() => {
    if (redirect && redirect !== step) goTo(redirect);
  }, [redirect, step, goTo]);
  if (redirect && redirect !== step) return null;
  if (mustWait(step, setup, pending.length) || (step === "files" && !courseCode)) return <AlmostThere />;
  return <StepView step={step} />;
}

/** The full onboarding window: no sidebar, three labeled segments, one step at a time. */
export function OnboardingWindow({ route }: { route: Extract<Route, { name: "welcome" }> }) {
  useHashRoute();
  const t = useT();
  return (
    <OnboardingProvider step={route.step}>
      <div className={styles.window}>
        <header className={styles.bar}>
          <span className={styles.brand}>
            <img src={logo} alt="" />
            {t.common.appName}
          </span>
          <Segments step={route.step} />
          <div className={styles.status}>
            <SetupStatus />
          </div>
        </header>
        <main aria-label={t.onboarding.window.label} className={styles.main}>
          <Body />
        </main>
      </div>
    </OnboardingProvider>
  );
}

/**
 * Launch rule (UX-SPEC §2 Resume): once the configuration answer arrives, open the first
 * incomplete step unless onboarding was finished here or the install is already complete.
 */
export function useOnboardingLaunch(): void {
  const { route, load, config } = useAppState((state) => ({ route: state.ui.route, load: state.config.load.status, config: state.config.config }), shallowEqual);
  const { ui } = useActions();
  const decided = useRef(false);
  useEffect(() => {
    if (decided.current || (load !== "ready" && load !== "error")) return;
    decided.current = true;
    if (route.name === "welcome" || route.name === "gallery") return;
    const record = loadRecord(browserStorage());
    const backend = load === "ready" ? backendProgress(config) : NO_BACKEND;
    if (!shouldOpenOnboarding(record, backend)) return;
    const step = resumeStep(record, backend);
    if (step) ui.navigate({ name: "welcome", step });
  }, [load, config, route.name, ui]);
}
