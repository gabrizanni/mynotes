import { LoaderCircle, Mic, Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Banner, Button, Card, Icon } from "../../../components";
import { useI18n } from "../../../i18n";
import { runJob } from "../../../rpc/jobs";
import { shallowEqual, useActions, useAppState, useRpc } from "../../../state";
import { useOnboarding } from "../controller";
import { MIC_TEST_SECONDS, micPhase, type CaptureSessionLike } from "../model";
import styles from "../Onboarding.module.css";
import { openSystemSettings, osFamily, playableUrl } from "../platform";
import { StepLayout } from "../StepLayout";

interface Playback {
  playing: boolean;
  position: number;
  duration: number;
  played: boolean;
  toggle: () => void;
}

/**
 * In-app player for the test file. Inside Tauri it plays the real file through the asset
 * protocol; in the browser mock there is no file, so time advances without sound.
 */
function usePlayback(path: string | null): Playback {
  const audio = useRef<HTMLAudioElement | null>(null);
  const [state, setState] = useState({ playing: false, position: 0, duration: MIC_TEST_SECONDS, played: false });

  useEffect(() => {
    let cancelled = false;
    audio.current = null;
    setState({ playing: false, position: 0, duration: MIC_TEST_SECONDS, played: false });
    if (!path) return undefined;
    void playableUrl(path).then((url) => {
      if (cancelled || !url) return;
      const element = new Audio(url);
      element.addEventListener("timeupdate", () => setState((current) => ({ ...current, position: element.currentTime })));
      element.addEventListener("loadedmetadata", () => setState((current) => ({ ...current, duration: Number.isFinite(element.duration) ? element.duration : current.duration })));
      element.addEventListener("ended", () => setState((current) => ({ ...current, playing: false, position: 0 })));
      audio.current = element;
    });
    return () => {
      cancelled = true;
      audio.current?.pause();
    };
  }, [path]);

  // Mock: no audio element, so advance the clock while "playing".
  useEffect(() => {
    if (!state.playing || audio.current) return undefined;
    const timer = setInterval(() => {
      setState((current) => {
        const position = current.position + 0.25;
        return position >= current.duration ? { ...current, playing: false, position: 0 } : { ...current, position };
      });
    }, 250);
    return () => clearInterval(timer);
  }, [state.playing]);

  const toggle = () => {
    const element = audio.current;
    if (element) {
      if (element.paused) void element.play().catch(() => undefined);
      else element.pause();
    }
    setState((current) => ({ ...current, playing: !current.playing, played: true }));
  };
  return { ...state, toggle };
}

/** 10 · Microphone: explain before the OS prompt, record, play back, confirm. */
export function MicrophoneStep() {
  const { t, format } = useI18n();
  const rpc = useRpc();
  const { ui, capture } = useActions();
  const { record, update, complete, skip, now } = useOnboarding();
  const m = t.onboarding.microphone;
  const runtime = useAppState((state) => state.capture.dashboard?.runtime ?? null, shallowEqual);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);

  const sessions: readonly CaptureSessionLike[] = runtime?.sessions ?? [];
  const { phase, audio, startedAt } = micPhase({
    permission: runtime?.microphonePermission,
    requested: record.micTestAt !== null,
    current: runtime?.currentSession ?? null,
    exporting: runtime?.exporting === true,
    sessions,
    requestedAt: record.micTestAt,
  });
  const playback = usePlayback(phase === "recorded" ? audio : null);

  const start = async () => {
    setError(null);
    setStarting(true);
    try {
      // Recorder on with the schedule paused, then a fixed 60 s test (UX-SPEC §2 step 10).
      await runJob(rpc, "capture.enable", undefined);
      await rpc.call("capture.pause");
      const { exitCode } = await rpc.call("capture.test");
      if (exitCode !== 0) throw new Error("test");
      update((current) => ({ ...current, micTestAt: Date.now() }));
    } catch {
      setError(m.failed);
    } finally {
      setStarting(false);
      void capture.refresh();
    }
  };

  const confirm = async () => {
    if (!audio) return;
    if (!playback.played) {
      setError(m.listenFirst);
      return;
    }
    setConfirming(true);
    try {
      const { exitCode } = await rpc.call("capture.confirmTest", { audio, audibleConfirmed: true });
      if (exitCode !== 0) throw new Error("confirm");
      complete("microphone");
    } catch {
      setError(m.confirmFailed);
    } finally {
      setConfirming(false);
    }
  };

  const openSettings = async (pane: "privacy" | "sound") => {
    const opened = await openSystemSettings(pane);
    if (!opened) ui.toast({ message: m.settingsOutsideApp, tone: "neutral" });
  };

  const elapsed = startedAt ? Math.max(0, (now.getTime() - Date.parse(startedAt)) / 1000) : 0;
  const remaining = Math.max(0, MIC_TEST_SECONDS - elapsed);
  const skipAction = { label: t.onboarding.window.skip, onSelect: () => skip("microphone") };

  let primary: { label: string; onSelect: () => void; loading?: boolean } | null = { label: m.test, onSelect: () => void start(), loading: starting };
  let secondary = [skipAction];
  if (phase === "recording" || phase === "saving" || phase === "starting") {
    primary = null;
  } else if (phase === "recorded") {
    primary = { label: m.yes, onSelect: () => void confirm(), loading: confirming };
    secondary = [
      { label: m.change, onSelect: () => void openSettings("sound") },
      { label: m.tryAgain, onSelect: () => void start() },
    ];
  } else if (phase === "denied") {
    primary = { label: m.tryAgain, onSelect: () => void start(), loading: starting };
  }

  const prompt = m.prompt[osFamily()];

  return (
    <StepLayout title={t.onboarding.steps.microphone} lede={m.lede} primary={primary} secondary={secondary} escapeBack={phase !== "recording" && phase !== "starting"}>
      {phase === "denied" && (
        <Banner tone="danger" action={{ label: m.openPrivacy, onSelect: () => void openSettings("privacy") }}>
          {m.denied}
        </Banner>
      )}
      <Card padding="lg">
        <div className={styles.micCard} aria-live="polite">
          <span className={styles.micIcon}>
            <Icon icon={Mic} size={24} />
          </span>
          <h2 className="type-heading">{m.cardTitle}</h2>
          {(phase === "intro" || phase === "denied") && (
            <>
              <p className="type-body">{prompt}</p>
              <p className={styles.note}>{m.testBody}</p>
            </>
          )}
          {phase === "starting" && (
            <p className={styles.liveLine}>
              <Icon icon={LoaderCircle} size={20} className={styles.spin} />
              {m.starting}
            </p>
          )}
          {phase === "recording" && (
            <>
              <span className={styles.live}>
                <span className={styles.liveDot} aria-hidden="true" />
                {m.recording}
              </span>
              <p className={styles.timer}>
                <span className={styles.timerValue} role="timer" aria-label={m.remaining(format.clock(remaining))}>
                  {format.clock(remaining)}
                </span>
                <span className="text-muted" aria-hidden="true">
                  {m.left}
                </span>
              </p>
              <p className={styles.note}>{m.testBody}</p>
            </>
          )}
          {phase === "saving" && (
            <p className={styles.liveLine}>
              <Icon icon={LoaderCircle} size={20} className={styles.spin} />
              {m.saving}
            </p>
          )}
          {phase === "recorded" && (
            <>
              <div className={styles.player} role="group" aria-label={m.player}>
                <Button variant="soft" icon={playback.playing ? Pause : Play} onClick={playback.toggle}>
                  {playback.playing ? m.pause : m.play}
                </Button>
                <span className={styles.playerTime}>
                  {format.clock(playback.position)} / {format.clock(playback.duration)}
                </span>
              </div>
              <p className={styles.question}>{m.question}</p>
            </>
          )}
        </div>
      </Card>
      {error && <Banner tone="danger">{error}</Banner>}
    </StepLayout>
  );
}
