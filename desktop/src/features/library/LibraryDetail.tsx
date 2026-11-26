import { AudioLines, EyeOff, FileAudio, FileText, FolderInput } from "lucide-react";
import { useId, useState } from "react";

import { Button, Icon, Select, StatusChip, TextField } from "../../components";
import { useI18n, type Messages } from "../../i18n";
import { routeToHash, useActions } from "../../state";
import type { LibraryItem } from "./model";
import styles from "./Library.module.css";

export function itemLabel(t: Messages, item: LibraryItem): string {
  if (item.courseName && item.lessonNumber) return t.common.courseLesson(item.courseName, item.lessonNumber);
  return item.title;
}

function fileName(path: string): string {
  const name = path.split(/[\\/]/).pop() ?? path;
  // Imported copies carry a unique suffix (`--a1b2c3`): hide it.
  return name.replace(/--[0-9a-f]{6,}(?=\.[^.]+$)/i, "");
}

export interface LibraryDetailProps {
  item: LibraryItem;
  courses: ReadonlyArray<{ code: string; name: string }>;
  headingLevel?: 2 | 3;
}

/** The selected lesson's files, with one fix when something is missing. */
export function LibraryDetail({ item, courses, headingLevel = 2 }: LibraryDetailProps) {
  const { t, format } = useI18n();
  const d = t.library.detail;
  const actions = useActions();
  const titleId = useId();
  const [busy, setBusy] = useState<"transcribe" | "assign" | null>(null);
  const [course, setCourse] = useState(courses[0]?.code ?? "");
  const [number, setNumber] = useState("");
  const [numberError, setNumberError] = useState<string | null>(null);

  const Heading = headingLevel === 2 ? "h2" : "h3";
  const Sub = headingLevel === 2 ? "h3" : "h4";
  const date = item.date ? new Date(`${item.date}T12:00:00`) : null;
  const meta = [date ? format.dayLong(date) : t.library.row.dateUnknown, item.duration ? d.duration(format.minutes(item.duration)) : null].filter(Boolean).join(" · ");
  const transcribable = item.recordings.filter((recording) => recording.can_transcribe === true && (recording.transcription_status ?? "NOT_STARTED") === "NOT_STARTED");

  const transcribe = async () => {
    setBusy("transcribe");
    const result = await actions.queue.enqueue(transcribable.map((recording) => recording.id));
    setBusy(null);
    actions.ui.toast(result.ok ? { message: d.transcribing, tone: "success" } : { message: d.transcribeFailed, tone: "danger" });
  };

  const assign = async () => {
    const recording = item.recordings[0];
    if (!recording || !course) return;
    const lesson = number.trim() === "" ? undefined : Number(number);
    if (lesson !== undefined && (!Number.isInteger(lesson) || lesson < 1 || lesson > 999)) {
      setNumberError(t.calendar.dialog.invalidNumber);
      return;
    }
    setNumberError(null);
    setBusy("assign");
    const result = await actions.library.assignCourse(recording.id, course, lesson);
    setBusy(null);
    const name = courses.find((candidate) => candidate.code === course)?.name ?? course;
    actions.ui.toast(result.ok ? { message: d.assigned(name), tone: "success" } : { message: d.assignFailed, tone: "danger" });
  };

  return (
    <article className={styles.detail} aria-labelledby={titleId}>
      <header className={styles.detailHead}>
        <Heading id={titleId} className={styles.detailTitle}>
          {itemLabel(t, item)}
        </Heading>
        <p className={styles.detailMeta}>{meta}</p>
        <div className={styles.detailChips}>
          {item.recordings.length > 0 || item.transcripts.length > 0 ? <StatusChip kind="transcription" status={item.transcription} /> : null}
          {item.unassigned && <StatusChip kind="recording" status="NEEDS_COURSE" />}
        </div>
      </header>

      {item.hidden && (
        <p className={styles.detailNote}>
          <Icon icon={EyeOff} size={16} />
          {d.hidden}
        </p>
      )}

      {item.unassigned && item.recordings.length > 0 && !item.hidden && (
        <section className={styles.assign} aria-label={d.assignTitle}>
          <Sub className={styles.detailSubtitle}>{d.assignTitle}</Sub>
          <p className={styles.detailBody}>{d.assignBody}</p>
          <div className={styles.assignFields}>
            <Select label={d.assignCourse} value={course} options={courses.map((option) => ({ value: option.code, label: option.name }))} onChange={(event) => setCourse(event.target.value)} />
            <TextField label={d.assignNumber} optional inputMode="numeric" value={number} error={numberError ?? undefined} onChange={(event) => setNumber(event.target.value)} />
          </div>
          <div>
            <Button variant="soft" icon={FolderInput} loading={busy === "assign"} disabled={!course} onClick={() => void assign()}>
              {d.assign}
            </Button>
          </div>
        </section>
      )}

      <section className={styles.detailSection}>
        <Sub className={styles.detailSubtitle}>{d.recordings}</Sub>
        {item.recordings.length === 0 ? (
          <p className={styles.detailBody}>{d.noRecordings}</p>
        ) : (
          <ul className={styles.files} role="list">
            {item.recordings.map((recording) => (
              <li key={recording.id} className={styles.file}>
                <Icon icon={FileAudio} size={16} className={styles.fileIcon} />
                <span className={styles.fileText}>
                  <span className={styles.fileName}>{fileName(recording.path)}</span>
                  <span className={styles.fileMeta}>
                    {[
                      recording.origin && recording.origin !== "capture" ? t.library.row.imported : t.library.row.recorded,
                      recording.duration ? format.clock(recording.duration) : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.detailSection}>
        <Sub className={styles.detailSubtitle}>{d.transcripts}</Sub>
        {item.transcripts.length === 0 ? (
          <p className={styles.detailBody}>{d.noTranscripts}</p>
        ) : (
          <ul className={styles.files} role="list">
            {item.transcripts.map((transcript) => (
              <li key={transcript.id} className={styles.file}>
                <Icon icon={FileText} size={16} className={styles.fileIcon} />
                <span className={styles.fileText}>
                  <span className={styles.fileName}>{fileName(transcript.path)}</span>
                  <span className={styles.fileMeta}>{d.created(format.dayShort(new Date(transcript.created_at)))}</span>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>

      {(transcribable.length > 0 || item.lectureId) && (
        <div className={styles.detailActions}>
          {transcribable.length > 0 && !item.hidden && (
            <Button variant="outline" icon={AudioLines} loading={busy === "transcribe"} onClick={() => void transcribe()}>
              {d.transcribe}
            </Button>
          )}
          {item.lectureId && (
            <a className={styles.detailLink} href={routeToHash({ name: "lesson", id: item.lectureId })}>
              {d.openLesson}
            </a>
          )}
        </div>
      )}
    </article>
  );
}
