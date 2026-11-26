import { Pause, Play, RotateCcw, RotateCw } from "lucide-react";
import { useEffect, useId, useRef, type Dispatch } from "react";

import { Button, Card, IconButton, Segmented, Slider } from "../../components";
import { useI18n } from "../../i18n";
import type { LibraryRecording } from "../../rpc";
import { isTauriRuntime } from "../../rpc/transports/tauri";
import { RATES, SKIP_SECONDS, type PlayerAction, type PlayerState, type Rate } from "./model";
import styles from "./Lesson.module.css";

/**
 * Drives real playback inside the app (an <audio> on the converted file URL). In the browser
 * preview there is no file to play, so a clock advances the position instead: the controls,
 * keys and transcript seeking behave the same.
 */
function useAudioEngine(path: string | null, state: PlayerState, dispatch: Dispatch<PlayerAction>) {
  const audio = useRef<HTMLAudioElement | null>(null);
  const tauri = isTauriRuntime();

  useEffect(() => {
    if (!tauri || !path) return undefined;
    let element: HTMLAudioElement | null = null;
    let cancelled = false;
    void import("@tauri-apps/api/core").then(({ convertFileSrc }) => {
      if (cancelled) return;
      element = new Audio(convertFileSrc(path));
      element.preload = "metadata";
      element.addEventListener("timeupdate", () => element && dispatch({ type: "tick", position: element.currentTime }));
      element.addEventListener("loadedmetadata", () => element && Number.isFinite(element.duration) && dispatch({ type: "duration", duration: element.duration }));
      element.addEventListener("ended", () => dispatch({ type: "ended" }));
      audio.current = element;
    });
    return () => {
      cancelled = true;
      element?.pause();
      audio.current = null;
    };
  }, [tauri, path, dispatch]);

  // Simulated clock for the browser preview.
  const position = useRef(state.position);
  position.current = state.position;
  useEffect(() => {
    if (tauri || !state.playing) return undefined;
    const step = 0.25;
    const timer = setInterval(() => dispatch({ type: "tick", position: position.current + step * state.rate }), step * 1000);
    return () => clearInterval(timer);
  }, [tauri, state.playing, state.rate, dispatch]);

  useEffect(() => {
    const element = audio.current;
    if (!element) return;
    element.playbackRate = state.rate;
    if (Math.abs(element.currentTime - state.position) > 0.75) element.currentTime = state.position;
    if (state.playing && element.paused) void element.play().catch(() => dispatch({ type: "pause" }));
    if (!state.playing && !element.paused) element.pause();
  }, [state.playing, state.rate, state.position, dispatch]);

  return { preview: !tauri };
}

export interface AudioPlayerProps {
  recording: LibraryRecording;
  alternatives: readonly LibraryRecording[];
  state: PlayerState;
  dispatch: Dispatch<PlayerAction>;
  onUseForTranscription?: (recording: LibraryRecording) => void;
}

export function AudioPlayer({ recording, alternatives, state, dispatch, onUseForTranscription }: AudioPlayerProps) {
  const { t, format } = useI18n();
  const p = t.lesson.player;
  const { preview } = useAudioEngine(recording.path, state, dispatch);
  const hintId = useId();
  const position = format.clock(state.position);
  const duration = format.clock(state.duration);

  return (
    <Card padding="none" className={styles.player} aria-label={p.label} as="section">
      <div className={styles.playerMain}>
        <IconButton
          variant="soft"
          label={state.playing ? p.pause : p.play}
          icon={state.playing ? Pause : Play}
          aria-describedby={hintId}
          onClick={() => dispatch({ type: "toggle" })}
        />
        <div className={styles.playerTrack}>
          <div className={styles.playerTimes}>
            <span className={styles.time}>{position}</span>
            <span className={styles.timeMuted}>{duration}</span>
          </div>
          <Slider
            label={p.position}
            value={state.position}
            max={state.duration}
            step={1}
            valueText={p.positionText(position, duration)}
            onChange={(to) => dispatch({ type: "seek", to })}
          />
        </div>
      </div>
      <div className={styles.playerControls}>
        <div className={styles.skips}>
          <IconButton label={p.back} icon={RotateCcw} onClick={() => dispatch({ type: "skip", by: -SKIP_SECONDS })} />
          <IconButton label={p.forward} icon={RotateCw} onClick={() => dispatch({ type: "skip", by: SKIP_SECONDS })} />
        </div>
        <Segmented<`${Rate}`>
          label={p.speed}
          hideLabel
          value={`${state.rate}`}
          onChange={(value) => dispatch({ type: "rate", rate: Number(value) as Rate })}
          options={RATES.map((rate) => ({ value: `${rate}` as `${Rate}`, label: `${rate}×` }))}
        />
        <p id={hintId} className={styles.playerHint}>
          {preview ? p.preview : p.keys}
        </p>
      </div>
      {alternatives.length > 0 && (
        <div className={styles.alternatives}>
          <p className={styles.alternativesTitle}>{p.alternatives}</p>
          <ul role="list" className={styles.alternativeList}>
            {alternatives.map((alternative) => (
              <li key={alternative.id} className={styles.alternative}>
                <span className={styles.alternativeText}>
                  <span className={styles.alternativeName}>{alternative.title}</span>
                  <span className={styles.timeMuted}>{format.clock(alternative.duration ?? 0)}</span>
                </span>
                {onUseForTranscription && (
                  <Button variant="ghost" size="sm" onClick={() => onUseForTranscription(alternative)}>
                    {p.useForTranscription}
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </Card>
  );
}
