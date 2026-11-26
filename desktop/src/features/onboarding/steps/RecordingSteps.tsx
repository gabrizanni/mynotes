import { CalendarClock, CircleCheck, Clock, FileAudio, Info, Keyboard, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Banner, Card, ChoiceCards, DropZone, Icon, IconButton, List, ListRow, StatusChip } from "../../../components";
import { useT } from "../../../i18n";
import type { Job } from "../../../rpc";
import { shallowEqual, useAppState, useRpc } from "../../../state";
import { isMac } from "../../../app/platform";
import { useOnboarding } from "../controller";
import { classifyImport, isRecordShortcut, lectureIdFor, readySummary } from "../model";
import styles from "../Onboarding.module.css";
import { droppedPath } from "../platform";
import { StepLayout } from "../StepLayout";

function shortcutKeys(): string[] {
  return isMac() ? ["⌥", "⇧", "R"] : ["Alt", "Shift", "R"];
}

/** 11 · Automatic recording: off by default; shortcut test is checked once the keys arrive. */
export function AutomaticStep() {
  const t = useT();
  const rpc = useRpc();
  const { record, complete, skip } = useOnboarding();
  const a = t.onboarding.automatic;
  const [pressed, setPressed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const keys = shortcutKeys();

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isRecordShortcut(event)) return;
      // Inside the window the press is only a test; Rust suppresses the global action meanwhile.
      event.preventDefault();
      setPressed(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const turnOn = async () => {
    setBusy(true);
    setFailed(false);
    try {
      const { exitCode } = await rpc.call("capture.resume");
      if (exitCode !== 0) throw new Error("resume");
      complete("automatic");
    } catch {
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <StepLayout
      title={t.onboarding.steps.automatic}
      lede={a.lede}
      primary={{ label: a.turnOn, onSelect: () => void turnOn(), loading: busy }}
      secondary={[{ label: a.notNow, onSelect: () => skip("automatic") }]}
    >
      <div className={styles.stack}>
        <div className={styles.offRow}>
          <span className="type-heading">{a.status}</span>
          <span className={styles.tag}>
            <Icon icon={CalendarClock} size={14} />
            {a.off}
          </span>
        </div>
        <p className={styles.note}>
          <Icon icon={Info} size={16} />
          <span>{a.awake}</span>
        </p>
        {!record.calendar && (
          <p className={styles.note}>
            <Icon icon={Info} size={16} />
            <span>{a.noSchedule}</span>
          </p>
        )}
      </div>
      <Card title={a.shortcutTitle} padding="lg">
        <div className={styles.stack}>
          <p className="type-body">{a.shortcutBody(keys.join(isMac() ? "" : "+"))}</p>
          <div className={styles.offRow}>
            <span className={styles.keys} aria-hidden="true">
              {keys.map((key) => (
                <kbd key={key} className={styles.key}>
                  {key}
                </kbd>
              ))}
            </span>
            <span className={styles.liveLine} role="status">
              {pressed ? (
                <>
                  <Icon icon={CircleCheck} size={20} className={styles.success} />
                  {a.shortcutDone}
                </>
              ) : (
                <>
                  <Icon icon={Keyboard} size={20} className={styles.checkIcon} />
                  <span className="text-muted">{a.shortcutWaiting}</span>
                </>
              )}
            </span>
          </div>
        </div>
      </Card>
      {failed && <Banner tone="danger">{a.failed}</Banner>}
    </StepLayout>
  );
}

type Choice = "have" | "later";

/** 12 · First notes: drop a recording or transcript and generate, or finish and wait for class. */
export function FirstNotesStep() {
  const t = useT();
  const rpc = useRpc();
  const { complete, skip, courseCode, providerName } = useOnboarding();
  const f = t.onboarding.firstNotes;
  const [choice, setChoice] = useState<Choice | null>(null);
  const [file, setFile] = useState<{ name: string; path: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onFiles = (files: File[]) => {
    const first = files[0];
    if (!first) return;
    // `lesson.process` takes an audio file (docs/SERVE.md); a TXT transcript has no server path yet.
    if (classifyImport(first.name) !== "audio") {
      setProblem(f.unsupported);
      return;
    }
    setProblem(null);
    setFile({ name: first.name, path: droppedPath(first) });
  };

  const generate = async () => {
    if (!file) {
      setProblem(f.pickFile);
      return;
    }
    if (!courseCode) return;
    setBusy(true);
    try {
      await rpc.call("lesson.process", { lectureId: lectureIdFor(courseCode), audio: file.path });
      complete("first-notes");
    } catch {
      setProblem(f.failed);
    } finally {
      setBusy(false);
    }
  };

  const primary = choice === "have" ? { label: f.generate, onSelect: () => void generate(), loading: busy } : { label: f.finish, onSelect: () => skip("first-notes") };

  return (
    <StepLayout title={t.onboarding.steps["first-notes"]} lede={f.lede} primary={primary}>
      <ChoiceCards<Choice>
        label={f.choiceLabel}
        hideLabel
        columns={2}
        options={[
          { value: "have", title: f.have.title, description: f.have.body },
          { value: "later", title: f.later.title, description: f.later.body },
        ]}
        value={choice}
        onChange={setChoice}
      />
      {choice === "have" && !file && <DropZone title={f.dropTitle} body={f.dropBody} accept="audio/*" multiple={false} onFiles={onFiles} />}
      {choice === "have" && file && (
        <div className={styles.folderRow}>
          <Icon icon={FileAudio} size={20} />
          <span className={styles.folderPath}>{file.name}</span>
          <span className={`${styles.tag} ${styles.tagSuccess}`}>
            <Icon icon={CircleCheck} size={14} />
            {f.chosen}
          </span>
          <IconButton label={f.remove} icon={X} size="sm" onClick={() => setFile(null)} />
        </div>
      )}
      {choice === "have" && <p className={styles.note}>{f.sends(providerName)}</p>}
      {problem && <Banner tone="danger">{problem}</Banner>}
    </StepLayout>
  );
}

/** 13 · You're ready: what's done, what's left for later (Today's Finish setup card). */
export function ReadyStep() {
  const t = useT();
  const { record, backend, finish } = useOnboarding();
  const r = t.onboarding.ready;
  const summary = readySummary(record, backend);
  const lessonJob = useAppState((state) => {
    const job = state.jobs.order.map((id) => state.jobs.byId[id]).find((item): item is Job => item?.kind === "lesson" && item.state === "running");
    return job ? { stage: job.stage ?? "GENERATING" } : null;
  }, shallowEqual);
  const later = summary.some((item) => item.state === "later");
  return (
    <StepLayout display title={t.onboarding.steps.ready} lede={r.lede} primary={{ label: r.open, onSelect: finish }}>
      <section aria-labelledby="ready-summary">
        <h2 id="ready-summary" className="visually-hidden">
          {r.summary}
        </h2>
        <List className={styles.summaryList}>
          {summary.map((item) => (
            <li key={item.step}>
              <ListRow
                title={r.items[item.step as keyof typeof r.items]}
                meta={
                  item.state === "done" ? (
                    <span className={`${styles.tag} ${styles.tagSuccess}`}>
                      <Icon icon={CircleCheck} size={14} />
                      {r.done}
                    </span>
                  ) : (
                    <span className={styles.tag}>
                      <Icon icon={Clock} size={14} />
                      {r.later}
                    </span>
                  )
                }
              />
            </li>
          ))}
        </List>
      </section>
      {lessonJob && (
        <p className={styles.liveLine}>
          {lessonJob.stage === "TRANSCRIBING" ? <StatusChip kind="transcription" status="RUNNING" /> : <StatusChip kind="notes" status={lessonJob.stage} />}
          {r.notesRunning}
        </p>
      )}
      {later && <p className={styles.note}>{r.laterHint}</p>}
    </StepLayout>
  );
}
