import { BookOpen, FileText, FolderOpen, History, Lightbulb, RotateCcw, Undo2 } from "lucide-react";
import { useMemo, useState } from "react";

import { Banner, Button, Card, Dialog, EmptyState, Icon, List, ListRow, Sheet } from "../../components";
import { useI18n } from "../../i18n";
import { useActions, useRpc } from "../../state";
import { openPath, revealPath } from "../lesson/platform";
import { courseFiles, fileName, historyEntries, notesSummary, reviewFirst, type HistoryEntry } from "./model";
import type { useCoursesInput } from "./useCourses";
import styles from "./Courses.module.css";

export interface NotesTabProps {
  code: string;
  name: string;
  record: Record<string, unknown> | null;
  courses: ReturnType<typeof useCoursesInput>;
}

/** Notes tab: the course notes card (Word, folder, History, Undo) and the notebook card. */
export function NotesTab({ code, name, record, courses }: NotesTabProps) {
  const { t, format } = useI18n();
  const n = t.courses.notes;
  const actions = useActions();
  const rpc = useRpc();
  const { input } = courses;
  const summary = useMemo(() => notesSummary(code, input.backups, input.jobs), [code, input.backups, input.jobs]);
  const entries = useMemo(() => historyEntries(input.backups.filter((backup) => backup.course === code)), [input.backups, code]);
  const files = courseFiles(record);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [undoOpen, setUndoOpen] = useState(false);
  const [restoring, setRestoring] = useState<HistoryEntry | null>(null);
  const [busy, setBusy] = useState(false);

  const bridge = async (path: string | null, reveal = false) => {
    if (!path) {
      actions.ui.toast({ message: n.noFile, tone: "warning" });
      return;
    }
    try {
      const result = reveal ? await revealPath(path) : await openPath(path);
      if (result === "preview") actions.ui.toast({ message: t.lesson.outcome.opening(fileName(path) ?? path), tone: "neutral" });
    } catch {
      actions.ui.toast({ message: t.lesson.outcome.openError, tone: "danger" });
    }
  };

  const undo = async () => {
    setBusy(true);
    try {
      await rpc.call("notes.undo", { course: code });
      actions.ui.toast({ message: n.undone, tone: "neutral" });
      setUndoOpen(false);
      courses.reloadHistory();
    } catch {
      actions.ui.toast({ message: n.undoError, tone: "danger" });
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    if (!restoring) return;
    setBusy(true);
    try {
      await rpc.call("notes.restore", { backupId: restoring.id });
      actions.ui.toast({ message: n.restored, tone: "neutral" });
      setRestoring(null);
      courses.reloadHistory();
    } catch {
      actions.ui.toast({ message: n.restoreError, tone: "danger" });
    } finally {
      setBusy(false);
    }
  };

  const when = (date: Date | null) => (date ? `${format.dayShort(date)} · ${format.time(date)}` : "—");
  const entryTitle = (entry: HistoryEntry) =>
    entry.kind === "publication" && entry.lessonNumber !== null
      ? n.entryPublication(t.common.lesson(entry.lessonNumber))
      : entry.kind === "undo"
        ? n.entryUndo
        : entry.kind === "restore"
          ? n.entryRestore
          : n.entryOther;

  const updated =
    summary.updatedAt && summary.lessonNumber !== null ? n.updated(format.relative(summary.updatedAt, input.now), t.common.lesson(summary.lessonNumber)) : n.never;

  return (
    <div className={styles.notesGrid}>
      <Card
        as="section"
        title={n.courseNotes}
        description={fileName(files.handout) ?? n.noFile}
        className={styles.notesCard}
        actions={<Icon icon={FileText} size={24} className={styles.fileGlyph} />}
      >
        <dl className={styles.notesFacts}>
          <div>
            <dt className="visually-hidden">{n.courseNotes}</dt>
            <dd>{updated}</dd>
          </div>
          {summary.lessons.length > 0 && (
            <div>
              <dt className="visually-hidden">{t.courses.tabs.lessons}</dt>
              <dd className={styles.mutedText}>{n.holds(summary.lessons.length)}</dd>
            </div>
          )}
          <div>
            <dt className="visually-hidden">{t.lesson.generate.reviewFirst}</dt>
            <dd className={styles.mutedText}>{reviewFirst(record) ? n.reviewMode : n.automaticMode}</dd>
          </div>
        </dl>
        <div className={styles.cardActions}>
          <Button variant="primary" icon={FileText} disabled={!files.handout} onClick={() => void bridge(files.handout)}>
            {t.courses.openInWord}
          </Button>
          <Button variant="ghost" icon={FolderOpen} disabled={!files.handout} onClick={() => void bridge(files.handout, true)}>
            {t.courses.showInFolder}
          </Button>
          <Button variant="ghost" icon={History} onClick={() => setHistoryOpen(true)}>
            {t.courses.history}
          </Button>
          <Button variant="ghost" icon={Undo2} disabled={!summary.canUndo} onClick={() => setUndoOpen(true)}>
            {t.courses.undoLast}
          </Button>
        </div>
      </Card>

      <Card as="section" title={n.notebook} description={fileName(files.notebook) ?? undefined} className={styles.notesCard} actions={<Icon icon={BookOpen} size={24} className={styles.fileGlyph} />}>
        <p className={styles.bodyText}>{n.notebookBody}</p>
        <div className={styles.cardActions}>
          <Button variant="soft" icon={BookOpen} disabled={!files.notebook} onClick={() => void bridge(files.notebook)}>
            {n.openNotebook}
          </Button>
        </div>
        <aside className={styles.tip} aria-label={n.tipTitle}>
          <Icon icon={Lightbulb} size={20} className={styles.tipIcon} />
          <div className={styles.tipText}>
            <p className={styles.tipTitle}>{n.tipTitle}</p>
            <p>{n.tip}</p>
            <p className={styles.mutedText}>{n.tipHeadings}</p>
          </div>
        </aside>
      </Card>

      <Sheet open={historyOpen} onClose={() => setHistoryOpen(false)} title={n.historyTitle(name)}>
        <div className={styles.stack}>
          <p className={styles.mutedText}>{n.historyIntro}</p>
          {courses.historyStatus === "error" ? (
            <Banner tone="danger" action={{ label: t.common.retry, onSelect: courses.reloadHistory }}>
              {n.historyError}
            </Banner>
          ) : entries.length === 0 ? (
            <EmptyState icon={History} title={n.historyEmpty} align="start" />
          ) : (
            <Card padding="none">
              <List>
                {entries.map((entry) => (
                  <li key={entry.id}>
                    <ListRow
                      title={entryTitle(entry)}
                      description={
                        <span className={styles.historyMeta}>
                          <span>{when(entry.createdAt)}</span>
                          {entry.lessonCount > 0 && (
                            <>
                              <span aria-hidden="true">·</span>
                              <span>{n.entryLessons(entry.lessonCount)}</span>
                            </>
                          )}
                        </span>
                      }
                      meta={
                        <Button variant="ghost" size="sm" icon={RotateCcw} aria-label={n.restoreLabel(when(entry.createdAt))} onClick={() => setRestoring(entry)}>
                          {n.restore}
                        </Button>
                      }
                    />
                  </li>
                ))}
              </List>
            </Card>
          )}
        </div>
      </Sheet>

      <Dialog
        open={undoOpen}
        onClose={() => setUndoOpen(false)}
        role="alertdialog"
        title={n.undoTitle}
        description={n.undoBody}
        footer={
          <>
            <Button variant="ghost" onClick={() => setUndoOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button variant="primary" loading={busy} onClick={() => void undo()}>
              {n.undoConfirm}
            </Button>
          </>
        }
      />
      <Dialog
        open={restoring !== null}
        onClose={() => setRestoring(null)}
        role="alertdialog"
        title={n.restoreTitle}
        description={restoring ? `${entryTitle(restoring)} · ${when(restoring.createdAt)}. ${n.restoreBody}` : n.restoreBody}
        footer={
          <>
            <Button variant="ghost" onClick={() => setRestoring(null)}>
              {t.common.cancel}
            </Button>
            <Button variant="primary" loading={busy} onClick={() => void restore()}>
              {n.restoreConfirm}
            </Button>
          </>
        }
      />
    </div>
  );
}
