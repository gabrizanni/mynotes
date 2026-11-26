import { CalendarPlus, FolderOpen, LifeBuoy, Mic, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";

import { useNow } from "../../app/useNow";
import { Banner, Button, Card, Dialog, Segmented, Select, StatusChip, Stepper, TextField, Toggle } from "../../components";
import { useI18n } from "../../i18n";
import type { CaptureSource } from "../../rpc";
import { shallowEqual, useActions, useAppState, type AppState } from "../../state";
import type { ActionResult } from "../../state/result";
import { systemZone } from "../calendar/format";
import { canReveal, isWindows, recordShortcut, revealPath } from "./platform";
import styles from "./Settings.module.css";

const PARALLEL = ["1", "2", "3", "4"] as const;
const REFRESH_HOURS = [1, 3, 6, 12, 24];

function useRun() {
  const { t } = useI18n();
  const actions = useActions();
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (key: string, task: () => Promise<ActionResult<unknown>>, success: string, failure = t.settings.saveFailed) => {
    setBusy(key);
    const result = await task();
    setBusy(null);
    actions.ui.toast(result.ok ? { message: success, tone: "success" } : { message: failure, tone: "danger" });
    return result.ok;
  };
  return { busy, run };
}

// ---------------------------------------------------------------------------------------------
// Transcription
// ---------------------------------------------------------------------------------------------

export function TranscriptionSection() {
  const { t, lang } = useI18n();
  const s = t.settings.transcription;
  const actions = useActions();
  const queue = useAppState((state) => state.queue.status);
  const doctor = useAppState((state) => state.settings.doctor.value);
  const { busy, run } = useRun();

  useEffect(() => {
    void actions.settings.loadDoctor();
  }, [actions]);

  const engine = doctor?.checks.transcription?.status;
  const parallel = String(Math.min(4, Math.max(1, queue?.parallel ?? 1))) as (typeof PARALLEL)[number];

  return (
    <div className={styles.stack}>
      <Card title={s.engine} headingLevel={3} actions={engine ? <StatusChip kind="assistant" status={engine === "ready" ? "ready" : "missing"} /> : undefined}>
        <p className={styles.muted}>{engine === undefined ? s.intro : engine === "ready" ? `${s.intro} ${s.engineReady}` : s.engineMissing}</p>
      </Card>
      <Card>
        <div className={styles.cardStack}>
          <Segmented
            label={s.parallel}
            hint={s.parallelHint}
            value={parallel}
            options={PARALLEL.map((value) => ({ value, label: value }))}
            onChange={(value) => void run("parallel", () => actions.queue.setParallel(Number(value)), t.activity.done.parallel(Number(value)))}
          />
          <div className={styles.divider} />
          <Toggle
            label={s.background}
            description={s.backgroundBody}
            checked={queue ? !queue.paused : true}
            disabled={!queue || busy === "pause"}
            onChange={(on) => void run("pause", () => actions.queue.setPaused(!on), on ? t.activity.done.resumed : t.activity.done.paused)}
          />
        </div>
      </Card>
      {isWindows() && (
        <Card>
          <div className={styles.cardStack}>
            <div className={styles.actionRow}>
              <div>
                <p className={styles.rowTitle}>{s.windowsModel}</p>
                <p className={styles.muted}>{s.windowsModelBody}</p>
              </div>
              <Button variant="outline" loading={busy === "speech"} onClick={() => void run("speech", () => actions.settings.installSpeechModel(), s.started)}>
                {s.download}
              </Button>
            </div>
            <div className={styles.divider} />
            <div className={styles.actionRow}>
              <div>
                <p className={styles.rowTitle}>{s.verify}</p>
                <p className={styles.muted}>{s.verifyBody}</p>
              </div>
              <Button variant="outline" loading={busy === "verify"} onClick={() => void run("verify", () => actions.settings.verifySpeech(lang), s.started)}>
                {s.verify}
              </Button>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Recording
// ---------------------------------------------------------------------------------------------

export function RecordingSection() {
  const { t } = useI18n();
  const r = t.settings.recording;
  const actions = useActions();
  const dashboard = useAppState((state) => state.capture.dashboard);
  const { busy, run } = useRun();
  if (!dashboard) return <div className={styles.skeleton} aria-busy="true" aria-label={t.common.loading} />;

  const before = Math.round(dashboard.recordBeforeSeconds / 60);
  const after = Math.round(dashboard.recordAfterSeconds / 60);
  const mic = dashboard.runtime.microphonePermission;
  const micDenied = mic === "DENIED" || mic === "RESTRICTED";
  const folder = dashboard.outputDirectory ?? null;

  return (
    <div className={styles.stack}>
      <Card>
        <div className={styles.cardStack}>
          <Toggle label={r.recorder} description={r.recorderBody} checked={dashboard.enabled} disabled={busy === "enabled"} onChange={(on) => void run("enabled", () => actions.capture.setEnabled(on), t.settings.saved)} />
          <div className={styles.divider} />
          <Toggle
            label={r.automatic}
            description={r.automaticBody}
            checked={dashboard.enabled && !dashboard.paused}
            disabled={!dashboard.enabled || busy === "automatic"}
            onChange={(on) => void run("automatic", () => actions.capture.setAutomatic(on), t.settings.saved)}
          />
        </div>
      </Card>

      <Card title={r.margins} headingLevel={3}>
        <div className={styles.fieldRow}>
          <Stepper label={r.before} value={before} min={0} max={30} valueText={r.minutes} onChange={(value) => void run("margins", () => actions.capture.configure({ recordBeforeSeconds: value * 60 }), t.settings.saved)} />
          <Stepper label={r.after} value={after} min={0} max={30} valueText={r.minutes} onChange={(value) => void run("margins", () => actions.capture.configure({ recordAfterSeconds: value * 60 }), t.settings.saved)} />
          <Select
            label={r.refresh}
            value={String(dashboard.refreshHours)}
            options={[...new Set([...REFRESH_HOURS, dashboard.refreshHours])].sort((a, b) => a - b).map((hours) => ({ value: String(hours), label: r.hours(hours) }))}
            onChange={(event) => void run("refresh", () => actions.capture.configure({ refreshHours: Number(event.target.value) }), t.settings.saved)}
          />
        </div>
      </Card>

      <Card title={r.microphone} headingLevel={3} actions={micDenied ? <StatusChip kind="recording" status="MIC_BLOCKED" /> : undefined}>
        <div className={styles.cardStack}>
          <p className={styles.muted}>{micDenied ? r.micDenied : mic === "AUTHORIZED" ? r.micAllowed : r.micUnknown}</p>
          <div className={styles.buttonRow}>
            <Button variant="outline" icon={Mic} loading={busy === "test"} onClick={() => void run("test", () => actions.capture.testMicrophone(), r.testStarted)}>
              {r.testMic}
            </Button>
          </div>
          <div className={styles.divider} />
          <div className={styles.actionRow}>
            <div>
              <p className={styles.rowTitle}>{r.recover}</p>
              <p className={styles.muted}>{r.recoverBody}</p>
            </div>
            <Button variant="ghost" icon={LifeBuoy} loading={busy === "recover"} onClick={() => void run("recover", () => actions.capture.recoverAudio(), r.recoverStarted)}>
              {r.recover}
            </Button>
          </div>
        </div>
      </Card>

      <Card>
        <div className={styles.cardStack}>
          <div className={styles.actionRow}>
            <div>
              <p className={styles.rowTitle}>{r.folder}</p>
              <p className={styles.path}>{folder ?? t.settings.general.noFolder}</p>
            </div>
            {folder && canReveal() && (
              <Button variant="ghost" icon={FolderOpen} onClick={() => void revealPath(folder)}>
                {r.reveal}
              </Button>
            )}
          </div>
          <div className={styles.divider} />
          <div className={styles.actionRow}>
            <div>
              <p className={styles.rowTitle}>{r.shortcut}</p>
              <p className={styles.muted}>{r.shortcutBody}</p>
            </div>
            <kbd className={styles.kbd}>{recordShortcut()}</kbd>
          </div>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Calendars
// ---------------------------------------------------------------------------------------------

const selectCalendars = (state: AppState) => ({ dashboard: state.capture.dashboard });

function CalendarCard({ source, lessons, onFix }: { source: CaptureSource; lessons: number; onFix: () => void }) {
  const { t, format } = useI18n();
  const c = t.settings.calendars;
  const actions = useActions();
  const now = useNow(60_000);
  const { busy, run } = useRun();
  const [confirm, setConfirm] = useState(false);
  const problem = source.code ?? (source.error ? "generic" : null);
  const courses = new Set(source.mappings.map((mapping) => mapping.courseCode)).size || (source.summary?.recognizedCourses ?? 0);
  return (
    <Card
      title={source.label}
      headingLevel={3}
      description={`${source.host} · ${source.timezone} · ${source.lastSuccess ? c.lastRefresh(format.relative(new Date(source.lastSuccess), now)) : c.neverRefreshed}`}
      actions={
        <>
          <Button size="sm" variant="ghost" icon={RefreshCw} loading={busy === "sync"} onClick={() => void run("sync", () => actions.capture.syncCalendars(source.sourceID), c.refreshed, t.calendar.refreshFailed)}>
            {c.refresh}
          </Button>
          <Button size="sm" variant="ghost" icon={Trash2} onClick={() => setConfirm(true)}>
            {c.remove}
          </Button>
        </>
      }
    >
      <div className={styles.cardStack}>
        {problem && (
          <Banner tone="warning" action={{ label: c.fixes[problem] ?? c.fixes.generic ?? "", onSelect: onFix }}>
            {c.problems[problem] ?? c.problems.generic}
          </Banner>
        )}
        <p className={styles.muted}>{c.summary(source.summary?.imported ?? lessons, courses)}</p>
        <Segmented
          label={c.mode}
          value={source.importMode === "all_timed" ? "all_timed" : "mapped"}
          options={[
            { value: "mapped", label: c.modeMapped },
            { value: "all_timed", label: c.modeAll },
          ]}
          onChange={(mode) => void run("mode", () => actions.capture.setImportMode(source.sourceID, mode), t.settings.saved)}
        />
      </div>
      <Dialog
        open={confirm}
        role="alertdialog"
        onClose={() => setConfirm(false)}
        title={c.removeTitle(source.label)}
        description={c.removeBody}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(false)}>
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              loading={busy === "remove"}
              onClick={() => void run("remove", () => actions.capture.removeCalendar(source.sourceID), c.removed).then(() => setConfirm(false))}
            >
              {c.remove}
            </Button>
          </>
        }
      />
    </Card>
  );
}

function today(zone: string, offsetDays = 0): string {
  const date = new Date(Date.now() + offsetDays * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
}

export function CalendarsSection() {
  const { t } = useI18n();
  const c = t.settings.calendars;
  const actions = useActions();
  const { dashboard } = useAppState(selectCalendars, shallowEqual);
  const { busy, run } = useRun();
  const zone = systemZone();
  const [url, setUrl] = useState("");
  const [from, setFrom] = useState(() => today(zone));
  const [until, setUntil] = useState(() => today(zone, 180));
  const [error, setError] = useState<string | null>(null);
  const [focusAdd, setFocusAdd] = useState(0);

  useEffect(() => {
    if (focusAdd > 0) document.getElementById("settings-calendar-url")?.focus();
  }, [focusAdd]);

  if (!dashboard) return <div className={styles.skeleton} aria-busy="true" aria-label={t.common.loading} />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!/^(https|webcal):\/\/\S+$/i.test(url.trim())) {
      setError(c.invalidUrl);
      return;
    }
    setError(null);
    const ok = await run("connect", () => actions.capture.connectCalendar({ url: url.trim(), timezone: zone, periodStart: from, periodEnd: until }), c.connected, c.connectFailed);
    if (ok) setUrl("");
  };

  return (
    <div className={styles.stack}>
      {dashboard.sources.length === 0 && <p className={styles.muted}>{c.empty}</p>}
      {dashboard.sources.map((source) => (
        <CalendarCard
          key={source.sourceID}
          source={source}
          lessons={dashboard.events.filter((event) => event.sourceID === source.sourceID).length}
          onFix={() => setFocusAdd((n) => n + 1)}
        />
      ))}
      <Card title={c.add} description={c.addBody} headingLevel={3}>
        <form className={styles.cardStack} onSubmit={(event) => void submit(event)} noValidate>
          <TextField id="settings-calendar-url" label={c.url} type="url" placeholder={c.urlPlaceholder} value={url} error={error ?? undefined} onChange={(event) => setUrl(event.target.value)} />
          <div className={styles.fieldRow}>
            <TextField label={c.from} type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
            <TextField label={c.until} type="date" value={until} onChange={(event) => setUntil(event.target.value)} />
          </div>
          <p className={styles.muted}>{t.calendar.dialog.zoneHint(zone)}</p>
          <div className={styles.buttonRow}>
            <Button type="submit" variant="primary" icon={CalendarPlus} loading={busy === "connect"} disabled={url.trim() === ""}>
              {c.connect}
            </Button>
          </div>
        </form>
      </Card>
    </div>
  );
}
