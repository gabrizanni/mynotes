import { FileAudio, FileText, FolderCheck, LibraryBig, Search, SearchX } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Banner, Button, Card, EmptyState, FilterChip, Icon, List, ListRow, PageHeader, Select, Sheet, StatusChip, TextField } from "../../components";
import { cx } from "../../design/cx";
import { useI18n } from "../../i18n";
import { shallowEqual, useActions, useAppState, type AppState } from "../../state";
import type { Route } from "../../state/routes";
import { ImportDialog, type ImportRequest } from "./ImportDialog";
import { consumeImportRequest } from "./importRequest";
import { itemLabel, LibraryDetail } from "./LibraryDetail";
import { deriveLibrary, type LibraryItem } from "./model";
import { useAudioDrop } from "./useAudioDrop";
import styles from "./Library.module.css";

const select = (state: AppState) => ({
  library: state.library.library,
  load: state.library.load,
  courses: state.capture.dashboard?.courses ?? state.config.config?.courses ?? null,
});

/** The split view needs room for both columns (UX-SPEC §1: split at ≥ 1100 px). */
const WIDE = "(min-width: 1100px)";

function useWide(): boolean {
  const query = useMemo(() => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(WIDE) : null), []);
  const [wide, setWide] = useState(query?.matches ?? true);
  useEffect(() => {
    if (!query) return undefined;
    const onChange = (event: MediaQueryListEvent) => setWide(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, [query]);
  return wide;
}

/** Library (UX-SPEC §3): search, filters, list and details, drag audio anywhere to import. */
export function LibraryPage({ route }: { route: Extract<Route, { name: "library" }> }) {
  const { t, format } = useI18n();
  const actions = useActions();
  const { library, load, courses: rawCourses } = useAppState(select, shallowEqual);
  const courses = useMemo(() => rawCourses ?? [], [rawCourses]);
  const wide = useWide();

  const [q, setQ] = useState(route.q);
  const [course, setCourse] = useState<string | null>(route.course);
  const [unassigned, setUnassigned] = useState(route.unassigned);
  const [hidden, setHidden] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importing, setImporting] = useState<ImportRequest | null>(() => (consumeImportRequest() ? { kind: "choose" } : null));
  const [verifying, setVerifying] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  // A link from another screen (Today's "Needs a course") changes the route while mounted.
  useEffect(() => {
    setQ(route.q);
    setCourse(route.course);
    setUnassigned(route.unassigned);
  }, [route.q, route.course, route.unassigned]);

  const dragging = useAudioDrop((paths) => setImporting({ kind: "paths", paths }), importing === null);

  const model = useMemo(() => deriveLibrary(library, courses, { q, course, unassigned, hidden }), [library, courses, q, course, unassigned, hidden]);
  const selected: LibraryItem | null = model.items.find((item) => item.id === selectedId) ?? (wide ? (model.items[0] ?? null) : null);
  const filtered = q.trim() !== "" || course !== null || unassigned || hidden;

  const verify = async () => {
    setVerifying(true);
    const result = await actions.library.verifyFiles();
    setVerifying(false);
    actions.ui.toast(result.ok ? { message: t.library.verified, tone: "success" } : { message: t.library.verifyFailed, tone: "danger", action: { label: t.common.retry, run: () => void verify() } });
  };

  const clearFilters = () => {
    setQ("");
    setCourse(null);
    setUnassigned(false);
    setHidden(false);
  };

  const header = (
    <PageHeader
      title={t.library.title}
      subtitle={model.ready ? `${t.library.count(model.items.length, model.total)} · ${t.library.dropHint}` : undefined}
      actions={
        <>
          <Button variant="ghost" icon={FolderCheck} loading={verifying} onClick={() => void verify()}>
            {t.library.verifyFiles}
          </Button>
          <Button variant="primary" icon={FileAudio} onClick={() => fileInput.current?.click()}>
            {t.library.importAudio}
          </Button>
          <input
            ref={fileInput}
            type="file"
            accept="audio/*"
            multiple
            hidden
            data-testid="library-file-input"
            onChange={(event) => {
              const files = Array.from(event.target.files ?? []);
              event.target.value = "";
              if (files.length > 0) setImporting({ kind: "paths", paths: files.map((file) => file.name) });
            }}
          />
        </>
      }
    />
  );

  const dialog = <ImportDialog request={importing} library={library} courses={courses} onClose={() => setImporting(null)} />;

  if (!model.ready) {
    return (
      <>
        {header}
        {load.status === "error" ? (
          <Banner tone="danger" action={{ label: t.common.retry, onSelect: () => void actions.library.refresh() }}>
            {t.library.loadError}
          </Banner>
        ) : (
          <div className={styles.skeleton} aria-busy="true" aria-label={t.common.loading} />
        )}
        {dialog}
      </>
    );
  }

  const list =
    model.items.length === 0 ? (
      <div className={styles.emptyCard}>
        {filtered ? (
          <EmptyState icon={SearchX} title={t.library.noResults} body={t.library.noResultsBody} align="start" action={<Button variant="soft" onClick={clearFilters}>{t.library.clearFilters}</Button>} />
        ) : (
          <EmptyState icon={LibraryBig} title={t.library.empty} body={t.library.emptyBody} align="start" />
        )}
      </div>
    ) : (
      <Card padding="none" className={styles.listCard}>
        <h2 className="visually-hidden">{t.library.results(model.items.length)}</h2>
        <List>
          {model.items.map((item) => {
            const date = item.date ? format.dayShort(new Date(`${item.date}T12:00:00`)) : t.library.row.dateUnknown;
            const kind = item.recordings.length === 0 ? t.library.row.transcriptOnly : item.imported ? t.library.row.imported : t.library.row.recorded;
            const description = [date, item.duration ? format.minutes(item.duration) : null, kind].filter(Boolean).join(" · ");
            return (
              <li key={item.id}>
                <ListRow
                  title={itemLabel(t, item)}
                  description={description}
                  leading={<Icon icon={item.recordings.length === 0 ? FileText : FileAudio} size={16} />}
                  meta={item.unassigned ? <StatusChip kind="recording" status="NEEDS_COURSE" /> : <StatusChip kind="transcription" status={item.transcription} />}
                  selected={wide && selected?.id === item.id}
                  chevron={!wide}
                  onSelect={() => setSelectedId(item.id)}
                />
              </li>
            );
          })}
        </List>
      </Card>
    );

  return (
    <>
      {header}
      <div className={styles.filters} role="search">
        <div className={styles.search}>
          <TextField label={t.library.search} hideLabel leadingIcon={Search} type="search" placeholder={t.library.searchPlaceholder} value={q} onChange={(event) => setQ(event.target.value)} />
        </div>
        <div className={styles.courseFilter}>
          <Select
            label={t.library.filterCourse}
            hideLabel
            value={course ?? ""}
            onChange={(event) => setCourse(event.target.value || null)}
            options={[{ value: "", label: t.library.allCourses }, ...courses.map((option) => ({ value: option.code, label: option.name }))]}
          />
        </div>
        <FilterChip label={t.library.filterUnassigned} pressed={unassigned} count={model.counts.unassigned} onChange={setUnassigned} />
        <FilterChip label={t.library.filterHidden} pressed={hidden} count={model.counts.hidden} onChange={setHidden} />
      </div>

      <div className={cx(styles.split, wide && styles.splitWide)}>
        {list}
        {wide && (
          <aside className={styles.detailPane} aria-label={selected ? itemLabel(t, selected) : t.library.detail.empty}>
            {selected ? <LibraryDetail key={selected.id} item={selected} courses={courses} /> : <p className={styles.detailEmpty}>{t.library.detail.empty}</p>}
          </aside>
        )}
      </div>

      {!wide && (
        <Sheet open={selected !== null} onClose={() => setSelectedId(null)} title={selected ? itemLabel(t, selected) : ""}>
          {selected && <LibraryDetail key={selected.id} item={selected} courses={courses} headingLevel={3} />}
        </Sheet>
      )}

      {dragging && (
        <div className={styles.dropOverlay} aria-hidden="true">
          <div className={styles.dropCard}>
            <Icon icon={FileAudio} size={24} />
            <p className={styles.dropTitle}>{t.library.dropOverlay}</p>
            <p className={styles.dropBody}>{t.library.dropOverlayBody}</p>
          </div>
        </div>
      )}
      {dialog}
    </>
  );
}
