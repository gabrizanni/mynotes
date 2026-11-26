import { FilePen, FolderOpen } from "lucide-react";
import { useMemo, useState } from "react";

import { Button, EmptyState, PageHeader, StatusChip, TabNav, Table, type TableColumn } from "../../components";
import { useI18n } from "../../i18n";
import { COURSE_TABS, routeToHash, useActions, type Route } from "../../state";
import { GenerateSheet } from "../lesson";
import { courseLessons, courseList, courseRecord, nextLessonOf, type LessonRow } from "./model";
import { FilesTab } from "./FilesTab";
import { NotesTab } from "./NotesTab";
import { ProfileTab } from "./ProfileTab";
import { StyleTab } from "./StyleTab";
import { useCoursesInput } from "./useCourses";
import styles from "./Courses.module.css";

/** Course workspace (UX-SPEC §3 Courses): Lessons · Notes · Style · Profile · Files. */
export function CoursePage({ route }: { route: Extract<Route, { name: "course" }> }) {
  const { t, format } = useI18n();
  const actions = useActions();
  const courses = useCoursesInput();
  const { input } = courses;
  const known = courseList(input).find((course) => course.code === route.code);
  const record = courseRecord(input.config, route.code);
  const rows = useMemo(() => courseLessons(route.code, input), [route.code, input]);

  if (!known) {
    return (
      <>
        <PageHeader title={route.code} />
        {courses.ready && (
          <EmptyState
            icon={FolderOpen}
            title={t.courses.workspace.notFound}
            action={
              <Button variant="soft" onClick={() => actions.ui.navigate({ name: "courses" })}>
                {t.courses.workspace.backToCourses}
              </Button>
            }
          />
        )}
      </>
    );
  }

  const next = nextLessonOf(route.code, input.dashboard, input.now);
  const subtitle = [t.courses.list.lessons(rows.length), next ? t.courses.nextLesson(`${format.dayShort(next.start)} · ${format.time(next.start)}`) : null].filter(Boolean).join(" · ");

  return (
    <>
      <PageHeader title={known.name} subtitle={subtitle} />
      <TabNav
        label={t.courses.workspace.sections(known.name)}
        current={route.tab}
        items={COURSE_TABS.map((tab) => ({ id: tab, label: t.courses.tabs[tab], href: routeToHash({ name: "course", code: route.code, tab }) }))}
      />
      <div className={styles.tabBody}>
        {route.tab === "lessons" && <LessonsTab code={route.code} name={known.name} rows={rows} />}
        {route.tab === "notes" && <NotesTab code={route.code} name={known.name} record={record} courses={courses} />}
        {route.tab === "style" && <StyleTab code={route.code} />}
        {route.tab === "profile" && <ProfileTab code={route.code} record={record} />}
        {route.tab === "files" && <FilesTab code={route.code} record={record} />}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------------------------

function LessonsTab({ code, name, rows }: { code: string; name: string; rows: readonly LessonRow[] }) {
  const { t, format } = useI18n();
  const l = t.courses.lessons;
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [sheet, setSheet] = useState(false);
  const chosen = rows.filter((row) => selected.has(row.id)).sort((a, b) => (a.number ?? 0) - (b.number ?? 0));
  const withoutTranscript = chosen.filter((row) => !row.canGenerate).length;

  if (rows.length === 0) return <EmptyState icon={FilePen} title={l.empty} align="start" />;

  const columns: TableColumn<LessonRow>[] = [
    {
      key: "lesson",
      header: l.lesson,
      rowHeader: true,
      cell: (row) => (
        <a className={styles.rowLink} href={routeToHash({ name: "lesson", id: row.id })}>
          {row.number !== null ? t.common.lesson(row.number) : row.id}
        </a>
      ),
    },
    { key: "date", header: l.date, cell: (row) => <span className={styles.date}>{row.date ? format.dayShort(new Date(`${row.date}T12:00:00`)) : "—"}</span> },
    { key: "recording", header: l.recording, cell: (row) => <StatusChip kind={row.chips.recording.kind} status={row.chips.recording.status} /> },
    { key: "transcript", header: l.transcript, cell: (row) => <StatusChip kind={row.chips.transcript.kind} status={row.chips.transcript.status} /> },
    { key: "notes", header: l.notes, cell: (row) => <StatusChip kind={row.chips.notes.kind} status={row.chips.notes.status} /> },
  ];

  return (
    <div className={styles.stack}>
      <div className={styles.selectionBar} aria-live="polite">
        {chosen.length === 0 ? (
          <p className={styles.mutedText}>{l.hint}</p>
        ) : (
          <>
            <div className={styles.selectionText}>
              <p className={styles.selectionCount}>{l.selected(chosen.length)}</p>
              {withoutTranscript > 0 && <p className={styles.mutedText}>{l.noTranscript(withoutTranscript)}</p>}
            </div>
            <div className={styles.selectionActions}>
              <Button variant="ghost" onClick={() => setSelected(new Set())}>
                {l.clear}
              </Button>
              <Button variant="primary" icon={FilePen} onClick={() => setSheet(true)}>
                {l.generateFor(chosen.length)}
              </Button>
            </div>
          </>
        )}
      </div>
      <Table
        caption={l.caption(name)}
        columns={columns}
        rows={rows}
        rowKey={(row) => row.id}
        selection={{
          selected,
          onChange: setSelected,
          rowLabel: (row) => l.selectRow(row.number ?? 0),
          allLabel: l.selectAll,
        }}
      />
      <GenerateSheet open={sheet} onClose={() => setSheet(false)} courseCode={code} lectureIds={chosen.map((row) => row.id)} initialJobId={null} />
    </div>
  );
}
