import { CircleCheck, ClipboardCopy, FileCheck, FolderOpen, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";

import { version } from "../../../package.json";
import { Banner, Button, Card, Icon, List, ListRow, Segmented, StatusChip, TextArea } from "../../components";
import type { ThemePreference } from "../../design/theme";
import { useI18n } from "../../i18n";
import type { Lang } from "../../i18n/lang";
import { useActions, useAppState } from "../../state";
import type { Route } from "../../state/routes";
import { buildReport, sortedChecks } from "./diagnostics";
import { canReveal, revealPath } from "./platform";
import styles from "./Settings.module.css";

// ---------------------------------------------------------------------------------------------
// General
// ---------------------------------------------------------------------------------------------

export function GeneralSection() {
  const { t } = useI18n();
  const g = t.settings.general;
  const actions = useActions();
  const lang = useAppState((state) => state.ui.lang);
  const theme = useAppState((state) => state.ui.theme);
  const recordings = useAppState((state) => state.capture.dashboard?.outputDirectory ?? state.library.library?.folder ?? null);
  const libraryFolder = useAppState((state) => state.library.library?.folder ?? null);

  const setLang = (next: Lang) => {
    actions.ui.setLang(next);
    // Saved with the model preferences (`preferences --models-json` carries the language).
    void actions.settings.saveLanguage(next).then((result) => {
      if (!result.ok) actions.ui.toast({ message: t.settings.saveFailed, tone: "danger" });
    });
  };

  const folders = [
    { id: "recordings", label: g.recordings, path: recordings },
    ...(libraryFolder && libraryFolder !== recordings ? [{ id: "library", label: g.library, path: libraryFolder }] : []),
  ];

  return (
    <div className={styles.stack}>
      <Card>
        <div className={styles.cardStack}>
          <Segmented
            label={t.settings.language}
            hint={g.languageHint}
            value={lang}
            options={[
              { value: "en", label: "English" },
              { value: "it", label: "Italiano" },
            ]}
            onChange={setLang}
          />
          <div className={styles.divider} />
          <Segmented<ThemePreference>
            label={t.settings.theme}
            value={theme}
            options={[
              { value: "system", label: t.settings.themeSystem },
              { value: "light", label: t.settings.themeLight },
              { value: "dark", label: t.settings.themeDark },
            ]}
            onChange={(next) => actions.ui.setTheme(next)}
          />
        </div>
      </Card>
      <Card title={g.folders} headingLevel={3} padding="none">
        <List>
          {folders.map((folder) => (
            <li key={folder.id}>
              <ListRow
                title={folder.label}
                description={<span className={styles.path}>{folder.path ?? g.noFolder}</span>}
                leading={<Icon icon={FolderOpen} size={16} />}
                meta={
                  folder.path && canReveal() ? (
                    <Button size="sm" variant="ghost" onClick={() => void revealPath(folder.path ?? "")}>
                      {t.settings.recording.reveal}
                    </Button>
                  ) : undefined
                }
              />
            </li>
          ))}
        </List>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// About you
// ---------------------------------------------------------------------------------------------

export function ProfileSection() {
  const { t } = useI18n();
  const p = t.settings.profile;
  const actions = useActions();
  const saved = useAppState((state) => state.config.config?.personal?.background ?? "");
  const [text, setText] = useState(saved);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => setText(saved), [saved]);

  const save = async () => {
    if (!text.trim()) {
      setError(p.empty);
      return;
    }
    setError(null);
    setBusy(true);
    const result = await actions.settings.saveProfile(text.trim());
    setBusy(false);
    actions.ui.toast(result.ok ? { message: t.settings.saved, tone: "success" } : { message: t.settings.saveFailed, tone: "danger" });
  };

  return (
    <div className={styles.stack}>
      <Card>
        <div className={styles.cardStack}>
          <TextArea label={p.background} hint={p.backgroundHint} placeholder={p.placeholder} value={text} rows={5} maxLength={2000} error={error ?? undefined} onChange={(event) => setText(event.target.value)} />
          <div className={styles.buttonRow}>
            <Button variant="primary" loading={busy} disabled={text.trim() === saved.trim()} onClick={() => void save()}>
              {p.save}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Check setup
// ---------------------------------------------------------------------------------------------

const FIX_ROUTES: Record<string, Route> = {
  installation: { name: "welcome", step: "language" },
  agent: { name: "settings", section: "assistant" },
  transcription: { name: "settings", section: "transcription" },
  capture: { name: "settings", section: "recording" },
};

export function DiagnosticsSection() {
  const { t } = useI18n();
  const d = t.settings.diagnostics;
  const actions = useActions();
  const doctor = useAppState((state) => state.settings.doctor);
  const dashboard = useAppState((state) => state.capture.dashboard);
  const [busy, setBusy] = useState<"word" | "copy" | null>(null);
  const [report, setReport] = useState<string | null>(null);

  useEffect(() => {
    void actions.settings.loadDoctor();
  }, [actions]);

  const verifyWord = async () => {
    setBusy("word");
    const result = await actions.settings.verifyWord();
    setBusy(null);
    actions.ui.toast(result.ok ? { message: d.verifyStarted, tone: "neutral" } : { message: t.settings.saveFailed, tone: "danger" });
  };

  const copy = async () => {
    const text = buildReport({ doctor: doctor.value, dashboard, appVersion: version, platform: navigator.platform || "unknown", now: new Date() });
    setBusy("copy");
    try {
      await navigator.clipboard.writeText(text);
      setReport(null);
      actions.ui.toast({ message: d.copied, tone: "success" });
    } catch {
      // Clipboard blocked: show the report so it can be selected by hand.
      setReport(text);
      actions.ui.toast({ message: d.copyFailed, tone: "warning" });
    } finally {
      setBusy(null);
    }
  };

  const checks = sortedChecks(doctor.value);
  const allReady = checks.length > 0 && checks.every(([, check]) => check.status === "ready" || check.status === "optional");

  return (
    <div className={styles.stack}>
      {doctor.load.status === "error" && (
        <Banner tone="danger" action={{ label: t.common.retry, onSelect: () => void actions.settings.loadDoctor(true) }}>
          {t.settings.loadError}
        </Banner>
      )}
      <Card padding="none" aria-busy={doctor.load.status === "loading" || undefined}>
        <div className={styles.checksHead}>
          <p className={styles.checksSummary}>
            {doctor.load.status === "loading" && !doctor.value ? (
              d.checking
            ) : allReady ? (
              <>
                <Icon icon={CircleCheck} size={20} className={styles.okIcon} />
                {d.allReady}
              </>
            ) : (
              d.intro
            )}
          </p>
          <Button size="sm" variant="ghost" icon={RotateCw} loading={doctor.load.status === "loading"} onClick={() => void actions.settings.loadDoctor(true)}>
            {d.checkAgain}
          </Button>
        </div>
        <List>
          {checks.map(([name, check]) => {
            const ready = check.status === "ready";
            const fix = d.fixes[name];
            const target = FIX_ROUTES[name];
            return (
              <li key={name}>
                <ListRow
                  title={d.names[name] ?? d.unknownCheck}
                  description={ready ? (d.statuses.ready ?? "") : (d.problems[name] ?? d.statuses[check.status] ?? "")}
                  meta={
                    <>
                      <StatusChip kind="check" status={check.status} />
                      {!ready && fix && name === "word" && (
                        <Button size="sm" variant="outline" loading={busy === "word"} onClick={() => void verifyWord()}>
                          {fix}
                        </Button>
                      )}
                      {!ready && fix && target && (
                        <Button size="sm" variant="outline" onClick={() => actions.ui.navigate(target)}>
                          {fix}
                        </Button>
                      )}
                    </>
                  }
                />
              </li>
            );
          })}
        </List>
      </Card>
      <div className={styles.footer}>
        <p className={styles.muted}>{d.copyBody}</p>
        <div className={styles.buttonRow}>
          {doctor.value?.checks.word?.status === "ready" && (
            <Button variant="outline" icon={FileCheck} loading={busy === "word"} onClick={() => void verifyWord()}>
              {d.verifyWord}
            </Button>
          )}
          <Button variant="primary" icon={ClipboardCopy} loading={busy === "copy"} onClick={() => void copy()}>
            {d.copyReport}
          </Button>
        </div>
      </div>
      {report && (
        <TextArea label={d.copyReport} value={report} readOnly rows={10} className={styles.report} />
      )}
    </div>
  );
}
