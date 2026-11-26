import { CalendarPlus, ChevronLeft, ChevronRight, FileAudio, Plus, RefreshCw } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { useNow } from "../../app/useNow";
import { AnchoredPopover, Banner, Button, EmptyState, IconButton, Menu, PageHeader, Segmented } from "../../components";
import { useI18n } from "../../i18n";
import { shallowEqual, useActions, useAppState, type AppState } from "../../state";
import type { CalendarView, Route } from "../../state/routes";
import { requestImport } from "../library/importRequest";
import { calendarFormat, systemZone } from "./format";
import { LessonDialog, type LessonDraft } from "./LessonDialog";
import { LessonPopover } from "./LessonPopover";
import {
  calendarLessons,
  dayKey,
  displayZoneLabel,
  firstVisibleHour,
  monthCells,
  overlapsOf,
  parseDayKey,
  shiftAnchor,
  weekColumns,
  weekDays,
  type CalendarLesson,
} from "./model";
import { MonthView } from "./MonthView";
import { WeekView } from "./WeekView";
import styles from "./Calendar.module.css";

const select = (state: AppState) => ({
  dashboard: state.capture.dashboard,
  library: state.library.library,
  load: state.capture.load,
});

export interface CalendarPageProps {
  route: Extract<Route, { name: "calendar" }>;
  /** Display zone; the computer's own by default (tests pin it). */
  zone?: string;
}

/** Calendar (UX-SPEC §3): week with a now-line or month, lesson details, Schedule lesson. */
export function CalendarPage({ route, zone: zoneProp }: CalendarPageProps) {
  const { t, locale } = useI18n();
  const actions = useActions();
  const now = useNow(30_000);
  const zone = useMemo(() => zoneProp ?? systemZone(), [zoneProp]);
  const fmt = useMemo(() => calendarFormat(locale, zone), [locale, zone]);
  const { dashboard, library, load } = useAppState(select, shallowEqual);

  const anchor = useMemo(() => parseDayKey(route.date, zone) ?? now, [route.date, zone, now]);
  const lessons = useMemo(() => calendarLessons({ dashboard, library, now }), [dashboard, library, now]);
  const overlaps = useMemo(() => overlapsOf(lessons.filter((lesson) => !lesson.excluded)), [lessons]);
  const columns = useMemo(() => weekColumns(anchor, zone, lessons, now), [anchor, zone, lessons, now]);
  const cells = useMemo(() => (route.view === "month" ? monthCells(anchor, zone, lessons, now) : []), [route.view, anchor, zone, lessons, now]);
  const firstHour = useMemo(() => firstVisibleHour(columns), [columns]);

  const [open, setOpen] = useState<{ lesson: CalendarLesson; anchor: HTMLElement } | null>(null);
  const [dialog, setDialog] = useState<{ editing: CalendarLesson | null } | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"lesson" | "dialog" | "refresh" | null>(null);

  const go = useCallback(
    (view: CalendarView, date: Date | null) => {
      setOpen(null);
      actions.ui.navigate({ name: "calendar", view, date: date ? dayKey(date, zone) : null });
    },
    [actions, zone],
  );
  const closePopover = useCallback(() => setOpen(null), []);

  const refresh = async () => {
    setBusy("refresh");
    const result = await actions.capture.syncCalendars();
    setBusy(null);
    actions.ui.toast(
      result.ok
        ? { message: t.calendar.refreshed, tone: "success" }
        : { message: t.calendar.refreshFailed, tone: "danger", action: { label: t.common.retry, run: () => void refresh() } },
    );
  };

  const restore = async (lesson: CalendarLesson) => {
    setBusy("lesson");
    const result = await actions.capture.resetLesson(lesson.eventId);
    setBusy(null);
    setOpen(null);
    actions.ui.toast(result.ok ? { message: t.calendar.popover.restored, tone: "success" } : { message: t.calendar.popover.restoreFailed, tone: "danger" });
  };

  const toggleRecording = async (lesson: CalendarLesson, record: boolean) => {
    setBusy("lesson");
    const result = await actions.capture.editLesson(lesson.eventId, { recordingEnabled: record });
    setBusy(null);
    setOpen(null);
    actions.ui.toast(
      result.ok
        ? { message: record ? t.calendar.popover.recordOn : t.calendar.popover.skipped, tone: "neutral" }
        : { message: t.calendar.popover.changeFailed, tone: "danger" },
    );
  };

  const submit = async (draft: LessonDraft) => {
    const editing = dialog?.editing ?? null;
    setBusy("dialog");
    setDialogError(null);
    const result = editing
      ? await actions.capture.editLesson(editing.eventId, { startsAt: draft.startsAt, endsAt: draft.endsAt, timezone: draft.timezone, title: draft.title })
      : await actions.capture.addLesson({
          title: draft.title,
          startsAt: draft.startsAt,
          endsAt: draft.endsAt,
          timezone: draft.timezone,
          ...(draft.courseCode ? { courseCode: draft.courseCode, lessonNumber: draft.lessonNumber } : { unassigned: true }),
        });
    setBusy(null);
    if (!result.ok) {
      setDialogError(t.calendar.dialog.failed);
      return;
    }
    setDialog(null);
    actions.ui.toast({ message: editing ? t.calendar.dialog.saved : t.calendar.dialog.added, tone: "success" });
  };

  const days = weekDays(anchor, zone);
  const first = days[0] ?? anchor;
  const last = days[6] ?? anchor;
  const month = route.view === "month";
  const period = month ? fmt.month(anchor) : fmt.week(first, last);
  const visibleCount = month ? cells.filter((cell) => cell.inMonth).reduce((sum, cell) => sum + cell.lessons.length, 0) : columns.reduce((sum, column) => sum + column.placements.length, 0);

  const header = <PageHeader title={t.calendar.title} subtitle={t.calendar.zone(displayZoneLabel(anchor, zone, locale))} />;

  if (!dashboard) {
    return (
      <>
        {header}
        {load.status === "error" ? (
          <Banner tone="danger" action={{ label: t.common.retry, onSelect: () => void actions.capture.refresh() }}>
            {t.calendar.loadError}
          </Banner>
        ) : (
          <div className={styles.skeleton} aria-busy="true" aria-label={t.common.loading} />
        )}
      </>
    );
  }

  const noCalendar = dashboard.sources.length === 0 && lessons.length === 0;

  return (
    <>
      {header}
      <div className={styles.toolbar}>
        <div className={styles.toolbarNav}>
          <Button variant="outline" size="sm" onClick={() => go(route.view, null)}>
            {t.calendar.goToToday}
          </Button>
          <div className={styles.arrows}>
            <IconButton
              label={month ? t.calendar.previousMonth : t.calendar.previousWeek}
              icon={ChevronLeft}
              size="sm"
              onClick={() => go(route.view, shiftAnchor(anchor, route.view, -1, zone))}
            />
            <IconButton
              label={month ? t.calendar.nextMonth : t.calendar.nextWeek}
              icon={ChevronRight}
              size="sm"
              onClick={() => go(route.view, shiftAnchor(anchor, route.view, 1, zone))}
            />
          </div>
          <h2 className={styles.period} aria-live="polite">
            {period}
          </h2>
        </div>
        <div className={styles.toolbarActions}>
          <Segmented
            label={t.calendar.view}
            hideLabel
            value={route.view}
            onChange={(view) => go(view, anchor)}
            options={[
              { value: "week", label: t.calendar.week },
              { value: "month", label: t.calendar.month },
            ]}
          />
          <Menu
            label={t.calendar.add}
            icon={Plus}
            size="sm"
            align="end"
            items={[
              {
                id: "schedule",
                label: t.calendar.scheduleLesson,
                description: t.calendar.scheduleLessonBody,
                icon: CalendarPlus,
                onSelect: () => {
                  setDialogError(null);
                  setDialog({ editing: null });
                },
              },
              {
                id: "import",
                label: t.calendar.importAudio,
                description: t.calendar.importAudioBody,
                icon: FileAudio,
                onSelect: () => {
                  requestImport();
                  actions.ui.navigate({ name: "library", course: null, q: "", unassigned: false });
                },
              },
            ]}
          />
          <IconButton label={t.calendar.refresh} icon={RefreshCw} size="sm" variant="outline" disabled={busy === "refresh"} onClick={() => void refresh()} />
        </div>
      </div>

      {noCalendar ? (
        <div className={styles.emptyCard}>
          <EmptyState
            icon={CalendarPlus}
            title={t.calendar.empty}
            body={t.calendar.emptyBody}
            align="start"
            action={
              <Button variant="soft" onClick={() => actions.ui.navigate({ name: "settings", section: "calendars" })}>
                {t.calendar.connectCalendar}
              </Button>
            }
          />
        </div>
      ) : month ? (
        <MonthView cells={cells} fmt={fmt} openId={open?.lesson.id ?? null} onOpen={(lesson, element) => setOpen({ lesson, anchor: element })} onShowDay={(date) => go("week", date)} />
      ) : (
        <WeekView columns={columns} fmt={fmt} openId={open?.lesson.id ?? null} firstHour={firstHour} now={now} onOpen={(lesson, element) => setOpen({ lesson, anchor: element })} />
      )}
      {!noCalendar && visibleCount === 0 && <p className={styles.quietNote}>{month ? t.calendar.emptyMonth : t.calendar.empty}</p>}

      <AnchoredPopover open={open !== null} anchor={open?.anchor ?? null} onClose={closePopover} title={open ? t.calendar.popover.label(open.lesson.courseName) : ""}>
        {open && (
          <LessonPopover
            lesson={open.lesson}
            overlaps={overlaps.get(open.lesson.id) ?? []}
            fmt={fmt}
            zone={zone}
            locale={locale}
            now={now}
            busy={busy === "lesson"}
            onClose={() => {
              const element = open.anchor;
              setOpen(null);
              element.focus();
            }}
            onRestore={() => void restore(open.lesson)}
            onEdit={() => {
              setDialogError(null);
              setDialog({ editing: open.lesson });
              setOpen(null);
            }}
            onToggleRecording={(record) => void toggleRecording(open.lesson, record)}
          />
        )}
      </AnchoredPopover>

      <LessonDialog
        open={dialog !== null}
        editing={dialog?.editing ?? null}
        courses={dashboard.courses}
        zone={zone}
        now={now}
        busy={busy === "dialog"}
        error={dialogError}
        onClose={() => setDialog(null)}
        onSubmit={(draft) => void submit(draft)}
      />
    </>
  );
}
