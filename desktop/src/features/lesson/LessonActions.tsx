import { FileAudio, FileText, Pencil, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Dialog, DropZone, Menu, TextField } from "../../components";
import { useI18n } from "../../i18n";
import { useActions, useRpc } from "../../state";
import type { LessonDetail } from "./model";
import styles from "./Lesson.module.css";

type Open = "edit" | "recording" | "transcript" | "remove" | null;

/** The ⋯ menu of the lesson header and its dialogs (Edit, Link recording, Link TXT, Remove). */
export function LessonActions({ lesson }: { lesson: LessonDetail }) {
  const { t } = useI18n();
  const m = t.lesson.menu;
  const actions = useActions();
  const rpc = useRpc();
  const [open, setOpen] = useState<Open>(null);
  const [title, setTitle] = useState(lesson.courseName);
  const [date, setDate] = useState(lesson.date ?? "");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (open !== "edit") return;
    setTitle(lesson.courseName);
    setDate(lesson.date ?? "");
  }, [open, lesson.courseName, lesson.date]);

  const close = () => setOpen(null);

  const save = async () => {
    setBusy(true);
    try {
      await rpc.call("library.lessonEdit", { action: "update", id: lesson.id, title: title.trim(), ...(date ? { date } : {}) });
      await actions.library.refresh();
      actions.ui.toast({ message: t.lesson.edit.saved, tone: "success" });
      close();
    } catch {
      actions.ui.toast({ message: t.lesson.edit.error, tone: "danger" });
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    try {
      await rpc.call("library.lessonEdit", { action: "hide", id: lesson.id });
      await actions.library.refresh();
      actions.ui.toast({ message: t.lesson.remove.removed, tone: "neutral" });
      close();
      actions.ui.navigate(lesson.courseCode ? { name: "course", code: lesson.courseCode, tab: "lessons" } : { name: "library", course: null, q: "", unassigned: false });
    } catch {
      actions.ui.toast({ message: t.lesson.remove.error, tone: "danger" });
    } finally {
      setBusy(false);
    }
  };

  // Dropped files carry no path in a webview; the app's drag-drop event will provide it.
  const onFiles = () => {
    actions.ui.toast({ message: t.lesson.link.desktopOnly, tone: "neutral" });
    close();
  };

  return (
    <>
      <Menu
        label={m.label}
        items={[
          { id: "edit", label: m.edit, icon: Pencil, onSelect: () => setOpen("edit") },
          { id: "recording", label: m.linkRecording, icon: FileAudio, onSelect: () => setOpen("recording") },
          { id: "transcript", label: m.linkTranscript, icon: FileText, onSelect: () => setOpen("transcript") },
          { id: "remove", label: m.remove, icon: Trash2, destructive: true, onSelect: () => setOpen("remove") },
        ]}
      />
      <Dialog
        open={open === "edit"}
        onClose={close}
        title={t.lesson.edit.title}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              {t.common.cancel}
            </Button>
            <Button variant="primary" loading={busy} disabled={!title.trim()} onClick={() => void save()}>
              {t.lesson.edit.save}
            </Button>
          </>
        }
      >
        <div className={styles.dialogFields}>
          <TextField label={t.lesson.edit.name} value={title} onChange={(event) => setTitle(event.target.value)} />
          <TextField label={t.lesson.edit.date} type="date" value={date} onChange={(event) => setDate(event.target.value)} />
        </div>
      </Dialog>
      <Dialog open={open === "recording"} onClose={close} title={t.lesson.link.recordingTitle} description={t.lesson.link.recordingBody}>
        <DropZone title={t.common.dropHere} accept="audio/*" multiple={false} onFiles={onFiles} />
      </Dialog>
      <Dialog open={open === "transcript"} onClose={close} title={t.lesson.link.transcriptTitle} description={t.lesson.link.transcriptBody}>
        <DropZone title={t.common.dropHere} accept=".txt,text/plain" multiple={false} onFiles={onFiles} />
      </Dialog>
      <Dialog
        open={open === "remove"}
        onClose={close}
        role="alertdialog"
        title={t.lesson.remove.title}
        description={t.lesson.remove.body}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              {t.common.cancel}
            </Button>
            <Button variant="destructive" loading={busy} onClick={() => void remove()}>
              {t.lesson.remove.confirm}
            </Button>
          </>
        }
      />
    </>
  );
}
