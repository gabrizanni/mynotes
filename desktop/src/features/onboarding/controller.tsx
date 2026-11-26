/**
 * Onboarding controller: the draft record, setup progress, the save queue and navigation.
 * Pure decisions live in model.ts; this file only wires them to React, the store and RPC.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { useNow } from "../../app/useNow";
import { useT } from "../../i18n";
import { setupDetailSchema, type Config, type RpcClient } from "../../rpc";
import { shallowEqual, useActions, useAppState, useRpc, type AppActions } from "../../state";
import { chosenModel } from "../../state/slices/settings";
import {
  applySaved,
  markStep,
  nextStepId,
  pendingOps,
  prevStepId,
  setupView,
  type BackendProgress,
  type OnboardingRecord,
  type OpId,
  type Provider,
  type QueueLabels,
  type SaveOp,
  type SetupDetail,
  type SetupView,
  type StepId,
} from "./model";
import { browserStorage, loadRecord, saveRecord } from "./storage";

export interface QueueState {
  running: OpId | null;
  error: string | null;
}

export interface OnboardingController {
  step: StepId;
  record: OnboardingRecord;
  update: (change: (record: OnboardingRecord) => OnboardingRecord) => void;
  setup: SetupView;
  backend: BackendProgress;
  /** A course exists in the backend (saved by this flow or an earlier install). */
  courseCode: string | null;
  courseName: string;
  providerName: string;
  pending: readonly SaveOp[];
  queue: QueueState;
  retryQueue: () => void;
  startSetup: (provider: Provider) => Promise<boolean>;
  goTo: (step: StepId) => void;
  back: () => void;
  complete: (step: StepId, change?: (record: OnboardingRecord) => OnboardingRecord) => void;
  skip: (step: StepId) => void;
  finish: () => void;
  now: Date;
}

const Context = createContext<OnboardingController | null>(null);

export function useOnboarding(): OnboardingController {
  const value = useContext(Context);
  if (!value) throw new Error("useOnboarding must be used inside <OnboardingProvider>");
  return value;
}

export function backendProgress(config: Config | null): BackendProgress {
  return {
    complete: config?.onboarding.status === "complete",
    completedGroups: config?.onboarding.completed_groups ?? [],
    agentReady: false,
  };
}

function toDetail(raw: unknown): SetupDetail | null {
  const parsed = setupDetailSchema.safeParse(raw);
  if (!parsed.success) return null;
  const value = parsed.data;
  return {
    state: value.state,
    stage: value.stage ?? null,
    title: value.title ?? null,
    bytesDone: value.bytesDone ?? null,
    bytesTotal: value.bytesTotal ?? null,
    lastDataAt: value.lastDataAt ?? null,
    error: value.error ?? null,
    overallDone: value.overallDone ?? null,
    overallTotal: value.overallTotal ?? null,
    components: (value.components ?? []).map((component) => ({
      id: component.id,
      title: component.title,
      state: component.state,
      bytesDone: component.bytesDone ?? null,
      bytesTotal: component.bytesTotal ?? null,
    })),
  };
}

/** Runs one queued save. Throws a human-safe message on failure. */
async function runOp(rpc: RpcClient, op: SaveOp, provider: Provider | null): Promise<string | null> {
  switch (op.id) {
    case "language": {
      const { data } = await rpc.call("preferences.get");
      const shared = chosenModel(data.default) ?? (provider ? { provider, model: "", effort: "" } : null);
      const { exitCode } = await rpc.call("preferences.saveModels", {
        models: {
          default: shared,
          overrides: { chat: data.overrides.chat ?? null, notes: data.overrides.notes ?? null },
          language: op.language,
        },
        expectedRevision: data.revision,
      });
      if (exitCode !== 0) throw new Error("language");
      return null;
    }
    case "profile": {
      const { exitCode } = await rpc.call("profile.save", op.background === null ? { skip: true } : { background: op.background });
      if (exitCode !== 0) throw new Error("profile");
      return null;
    }
    case "course": {
      if (op.mode === "add" || !op.code) {
        const { exitCode, data } = await rpc.call("course.add", {
          name: op.payload.name,
          notesLanguage: op.payload.notesLanguage,
          settings: { ...op.payload.settingsJson },
          ...(op.payload.contextAnswer ? { contextAnswer: op.payload.contextAnswer } : {}),
        });
        const code = data.code ?? data.course?.code ?? null;
        if (exitCode !== 0 || !code) throw new Error("course");
        return code;
      }
      // `course edit` replaces the whole course: start from the saved one, change our fields only.
      const { data } = await rpc.call("config.get");
      const current = (data.courses ?? []).find((course) => course.code === op.code) ?? { code: op.code };
      const settings = op.payload.settingsJson;
      const { exitCode } = await rpc.call("course.edit", {
        code: op.code,
        course: {
          ...current,
          name: op.payload.name,
          kind: settings.kind,
          languages: settings.languages,
          assessment: settings.assessment,
          difficult_topics: settings.difficult_topics,
          style: settings.style,
          ...(op.payload.contextAnswer ? { context: op.payload.contextAnswer } : {}),
        },
      });
      if (exitCode !== 0) throw new Error("course");
      return op.code;
    }
  }
}

function navigateTo(actions: AppActions, step: StepId) {
  actions.ui.navigate({ name: "welcome", step });
}

export function OnboardingProvider({ step, children }: { step: StepId; children: ReactNode }) {
  const t = useT();
  const rpc = useRpc();
  const actions = useActions();
  const now = useNow(1000);
  const { config, lang } = useAppState((state) => ({ config: state.config.config, lang: state.ui.lang }), shallowEqual);
  const [record, setRecord] = useState<OnboardingRecord>(() => loadRecord(browserStorage()));
  const [detail, setDetail] = useState<SetupDetail | null>(null);
  const [queue, setQueue] = useState<QueueState>({ running: null, error: null });
  const [pollKey, setPollKey] = useState(0);
  const recordRef = useRef(record);
  recordRef.current = record;

  const update = useCallback((change: (current: OnboardingRecord) => OnboardingRecord) => {
    setRecord((current) => {
      const next = change(current);
      if (next !== current) saveRecord(browserStorage(), next);
      return next;
    });
  }, []);

  // The chosen language drives the window right away; the backend gets it through the queue.
  useEffect(() => {
    if (record.language && record.language !== lang) actions.ui.setLang(record.language);
  }, [record.language, lang, actions]);

  // Setup progress: poll the installer every second while it runs (it answers from its progress file).
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      let next: SetupDetail | null = null;
      try {
        next = toDetail(await rpc.call("setup.status"));
      } catch {
        next = null;
      }
      if (cancelled) return;
      if (next) setDetail(next);
      const state = next?.state ?? "idle";
      if (state === "complete") return;
      timer = setTimeout(() => void tick(), state === "running" ? 1000 : 4000);
    };
    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [rpc, pollKey]);

  const setup = useMemo(() => setupView(detail, now), [detail, now]);
  const labels: QueueLabels = useMemo(() => ({ goalLabel: (goal) => t.onboarding.studies.goals[goal], goalsPrefix: t.onboarding.studies.goalsPrefix }), [t]);
  const pending = useMemo(() => pendingOps(record, labels), [record, labels]);

  // Draft queue: once the command exists, save answers in order, one at a time. A save in
  // flight always finishes and lands in the record, even if the student moves on meanwhile.
  const inFlight = useRef<string | null>(null);
  const commandReady = setup.phase === "complete";
  useEffect(() => {
    const op = pending[0];
    if (!commandReady || queue.error !== null || !op || inFlight.current !== null) return;
    inFlight.current = op.fingerprint;
    setQueue({ running: op.id, error: null });
    runOp(rpc, op, recordRef.current.provider).then(
      (code) => {
        inFlight.current = null;
        update((current) => applySaved(current, op, code));
        setQueue({ running: null, error: null });
        if (op.id === "course") void actions.config.refresh();
      },
      () => {
        inFlight.current = null;
        setQueue({ running: null, error: op.id });
      },
    );
  }, [commandReady, pending, queue.error, rpc, update, actions]);

  const backend = useMemo(() => backendProgress(config), [config]);
  const configCourses = config?.courses ?? [];
  const courseName = record.course?.name.trim() || configCourses[0]?.name || "";
  const courseCode = record.courseCode ?? configCourses.find((course) => course.name === courseName)?.code ?? (record.course ? null : (configCourses[0]?.code ?? null));
  const providerName = t.onboarding.assistant.providers[record.provider ?? "claude"].name;

  const goTo = useCallback((target: StepId) => navigateTo(actions, target), [actions]);
  const back = useCallback(() => {
    const previous = prevStepId(step);
    if (previous) navigateTo(actions, previous);
  }, [actions, step]);
  const complete = useCallback(
    (target: StepId, change?: (current: OnboardingRecord) => OnboardingRecord) => {
      update((current) => markStep(change ? change(current) : current, target, "done"));
      const next = nextStepId(target);
      if (next) navigateTo(actions, next);
    },
    [actions, update],
  );
  const skip = useCallback(
    (target: StepId) => {
      update((current) => markStep(current, target, "skipped"));
      const next = nextStepId(target);
      if (next) navigateTo(actions, next);
    },
    [actions, update],
  );
  const finish = useCallback(() => {
    update((current) => ({ ...markStep(current, "ready", "done"), finished: true }));
    actions.ui.navigate({ name: "today" });
  }, [actions, update]);

  const startSetup = useCallback(
    async (provider: Provider) => {
      try {
        await rpc.call("setup.run", { provider });
        setPollKey((key) => key + 1);
        return true;
      } catch {
        return false;
      }
    },
    [rpc],
  );

  const retryQueue = useCallback(() => setQueue({ running: null, error: null }), []);

  const value: OnboardingController = {
    step,
    record,
    update,
    setup,
    backend,
    courseCode,
    courseName,
    providerName,
    pending,
    queue,
    retryQueue,
    startSetup,
    goTo,
    back,
    complete,
    skip,
    finish,
    now,
  };
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
