import { CalendarDays, LoaderCircle, Split, Tag } from "lucide-react";
import { useMemo, useState } from "react";

import { Banner, Card, Icon, Segmented, Select, TextField } from "../../../components";
import { useT } from "../../../i18n";
import { useActions, useAppState, useRpc } from "../../../state";
import { useOnboarding } from "../controller";
import { calendarProblem, defaultPeriod, schedulePreview, validateCalendarUrl, type CalendarCode, type CalendarFix } from "../model";
import styles from "../Onboarding.module.css";
import { StepLayout } from "../StepLayout";

type Tab = "link" | "google";

const COMMON_ZONES = ["Europe/Rome", "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "America/New_York", "Asia/Kolkata"];

/** 9 · Class schedule: paste a link, preview what it brings, keep it or remove it. */
export function ScheduleStep() {
  const t = useT();
  const rpc = useRpc();
  const actions = useActions();
  const { record, update, complete, skip, courseName, courseCode, now } = useOnboarding();
  const s = t.onboarding.schedule;
  const events = useAppState((state) => state.capture.dashboard?.events ?? null);
  const zone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Rome", []);
  const [tab, setTab] = useState<Tab>(record.calendar?.url.includes("calendar.google.com") ? "google" : "link");
  const [url, setUrl] = useState(record.calendar?.url ?? "");
  const [period, setPeriod] = useState(() => defaultPeriod(now));
  const [timezone, setTimezone] = useState(zone);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<{ code: CalendarCode; fix: CalendarFix } | null>(null);
  const [removeFailed, setRemoveFailed] = useState(false);

  const connected = record.calendar;
  const urlError = validateCalendarUrl(url);
  const summary = connected && events ? schedulePreview(events, connected.sourceID) : null;
  const zones = COMMON_ZONES.includes(timezone) ? COMMON_ZONES : [timezone, ...COMMON_ZONES];

  const focusLink = () => document.getElementById("calendar-url")?.focus();

  const preview = async () => {
    setTouched(true);
    setProblem(null);
    if (urlError) {
      focusLink();
      return;
    }
    setBusy(true);
    try {
      await rpc.call("capture.configure", { config: {} });
      const { data } = await rpc.call("capture.connect", {
        source: {
          url: url.trim(),
          label: courseName || s.tabs.link,
          timezone,
          periodStart: period.start,
          periodEnd: period.end,
          importMode: "all_timed",
          mappings: courseCode && courseName ? [{ match: courseName, courseCode }] : [],
        },
      });
      if (data.status === "CONNECTED" && data.sourceID) {
        const sourceID = data.sourceID;
        update((current) => ({ ...current, calendar: { url: url.trim(), sourceID } }));
        await actions.capture.refresh();
      } else {
        setProblem(calendarProblem(data.code));
      }
    } catch {
      setProblem(calendarProblem("INTERNAL_ERROR"));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!connected) return;
    setBusy(true);
    setRemoveFailed(false);
    try {
      await rpc.call("capture.unsubscribe", { source: connected.sourceID });
      update((current) => ({ ...current, calendar: null }));
      await actions.capture.refresh();
    } catch {
      setRemoveFailed(true);
    } finally {
      setBusy(false);
    }
  };

  const fix = (kind: CalendarFix) => {
    if (kind === "showGuide") {
      setTab("google");
      setProblem(null);
      window.setTimeout(focusLink, 0);
    } else if (kind === "editLink") {
      setProblem(null);
      focusLink();
    } else {
      void preview();
    }
  };

  const errorText = touched && urlError ? s.errors[urlError] : undefined;

  if (connected) {
    return (
      <StepLayout
        title={t.onboarding.steps.schedule}
        lede={s.lede}
        primary={{ label: s.looksRight, onSelect: () => complete("schedule") }}
        secondary={[{ label: s.remove, onSelect: () => void remove(), loading: busy }]}
      >
        <Card title={s.summaryTitle} padding="lg">
          {summary ? (
            <ul className={styles.summary} role="list">
              <li className={styles.summaryItem}>
                <Icon icon={CalendarDays} size={20} />
                {s.lessons(summary.lessons)}
              </li>
              <li className={styles.summaryItem}>
                <Icon icon={Split} size={20} />
                {s.clashes(summary.clashes)}
              </li>
              <li className={styles.summaryItem}>
                <Icon icon={Tag} size={20} />
                {s.needCourse(summary.needCourse)}
              </li>
            </ul>
          ) : (
            <p className={styles.liveLine} role="status">
              <Icon icon={LoaderCircle} size={20} className={styles.spin} />
              {s.previewing}
            </p>
          )}
        </Card>
        <p className={styles.note}>{s.summaryHint}</p>
        {removeFailed && <Banner tone="danger">{s.removeFailed}</Banner>}
      </StepLayout>
    );
  }

  return (
    <StepLayout
      title={t.onboarding.steps.schedule}
      lede={s.lede}
      primary={{ label: s.preview, onSelect: () => void preview(), loading: busy }}
      secondary={[{ label: t.onboarding.window.skip, onSelect: () => skip("schedule") }]}
    >
      <Segmented<Tab>
        label={s.tabsLabel}
        hideLabel
        options={[
          { value: "link", label: s.tabs.link },
          { value: "google", label: s.tabs.google },
        ]}
        value={tab}
        onChange={(value) => {
          setTab(value);
          setProblem(null);
        }}
      />
      <TextField
        id="calendar-url"
        type="url"
        inputMode="url"
        autoComplete="off"
        spellCheck={false}
        label={tab === "google" ? s.urlGoogle : s.url}
        hint={tab === "google" ? s.urlGoogleHint : s.urlHint}
        placeholder={tab === "google" ? "https://calendar.google.com/calendar/ical/…/basic.ics" : "https://"}
        value={url}
        error={errorText}
        onChange={(event) => {
          setUrl(event.target.value);
          setProblem(null);
        }}
      />
      {problem && (
        <Banner tone="danger" action={{ label: s.fixes[problem.fix], onSelect: () => fix(problem.fix) }}>
          {s.codes[problem.code]}
        </Banner>
      )}
      {tab === "google" && (
        <Card variant="emphasis" title={s.guideTitle} headingLevel={2}>
          <ol className={styles.guide}>
            {s.guide.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ol>
        </Card>
      )}
      <div className={styles.row2}>
        <TextField label={s.from} type="date" value={period.start} onChange={(event) => setPeriod((current) => ({ ...current, start: event.target.value }))} />
        <TextField label={s.to} type="date" value={period.end} onChange={(event) => setPeriod((current) => ({ ...current, end: event.target.value }))} />
      </div>
      <Select label={s.timezone} options={zones.map((value) => ({ value, label: value.replace("_", " ") }))} value={timezone} onChange={(event) => setTimezone(event.target.value)} />
    </StepLayout>
  );
}
