import { useState, type FormEvent } from "react";

import { Button, Dialog, Select, TextField } from "../../components";
import { useT } from "../../i18n";
import { dayKey, defaultSlot, wallClock, wallTimeToIso, type CalendarLesson } from "./model";
import styles from "./Calendar.module.css";

export interface LessonDraft {
  courseCode: string | null;
  lessonNumber: number | null;
  title: string;
  startsAt: string;
  endsAt: string;
  timezone: string;
}

export interface LessonDialogProps {
  open: boolean;
  /** Edit an existing lesson's time; otherwise schedule a new one. */
  editing: CalendarLesson | null;
  courses: ReadonlyArray<{ code: string; name: string }>;
  zone: string;
  now: Date;
  busy: boolean;
  /** Error from the backend, already in the student's language. */
  error: string | null;
  onClose: () => void;
  onSubmit: (draft: LessonDraft) => void;
}

interface Fields {
  course: string;
  number: string;
  title: string;
  day: string;
  start: string;
  end: string;
}

function initialFields(editing: CalendarLesson | null, courses: LessonDialogProps["courses"], zone: string, now: Date): Fields {
  if (editing) {
    return {
      course: editing.courseCode ?? "",
      number: editing.lessonNumber ? String(editing.lessonNumber) : "",
      title: editing.title,
      day: dayKey(editing.start, zone),
      start: wallClock(editing.start, zone),
      end: wallClock(editing.end, zone),
    };
  }
  const slot = defaultSlot(now, zone);
  return { course: courses[0]?.code ?? "", number: "", title: "", day: slot.day, start: slot.start, end: slot.end };
}

/** "Schedule lesson" and "Edit time": one form, validated before it reaches the recorder. */
export function LessonDialog(props: LessonDialogProps) {
  // Remount per opening so the form always starts from the lesson (or the next free hour).
  if (!props.open) return null;
  return <LessonForm key={props.editing?.id ?? "new"} {...props} />;
}

function LessonForm({ editing, courses, zone, now, busy, error, onClose, onSubmit }: LessonDialogProps) {
  const t = useT();
  const d = t.calendar.dialog;
  const [fields, setFields] = useState<Fields>(() => initialFields(editing, courses, zone, now));
  const [problem, setProblem] = useState<{ field: "number" | "end" | "start"; message: string } | null>(null);
  const set = (key: keyof Fields) => (value: string) => setFields((current) => ({ ...current, [key]: value }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const startsAt = wallTimeToIso(fields.day, fields.start, zone);
    const endsAt = wallTimeToIso(fields.day, fields.end, zone);
    const number = fields.number.trim() === "" ? null : Number(fields.number);
    if (number !== null && (!Number.isInteger(number) || number < 1 || number > 999)) {
      setProblem({ field: "number", message: d.invalidNumber });
      return;
    }
    if (!startsAt || !endsAt || Date.parse(endsAt) <= Date.parse(startsAt)) {
      setProblem({ field: "end", message: d.endBeforeStart });
      return;
    }
    if (Date.parse(startsAt) <= now.getTime()) {
      setProblem({ field: "start", message: d.inPast });
      return;
    }
    setProblem(null);
    const courseName = courses.find((course) => course.code === fields.course)?.name ?? "";
    onSubmit({
      courseCode: fields.course || null,
      lessonNumber: number,
      title: fields.title.trim() || courseName || t.calendar.scheduleLesson,
      startsAt,
      endsAt,
      timezone: zone,
    });
  };

  const formId = "calendar-lesson-form";
  return (
    <Dialog
      open
      onClose={onClose}
      title={editing ? d.editTitle : d.addTitle}
      description={editing ? d.editBody : d.addBody}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            {t.common.cancel}
          </Button>
          <Button variant="primary" type="submit" form={formId} loading={busy}>
            {editing ? d.submitEdit : d.submitAdd}
          </Button>
        </>
      }
    >
      <form id={formId} className={styles.form} onSubmit={submit} noValidate>
        {!editing && (
          <div className={styles.formRow}>
            <Select
              label={d.course}
              value={fields.course}
              onChange={(event) => set("course")(event.target.value)}
              options={[...courses.map((course) => ({ value: course.code, label: course.name })), { value: "", label: d.noCourse }]}
            />
            <TextField
              label={d.lessonNumber}
              optional
              inputMode="numeric"
              value={fields.number}
              disabled={!fields.course}
              hint={d.lessonNumberHint}
              error={problem?.field === "number" ? problem.message : undefined}
              onChange={(event) => set("number")(event.target.value)}
            />
          </div>
        )}
        <TextField label={d.lessonTitle} optional hint={d.lessonTitleHint} value={fields.title} maxLength={300} onChange={(event) => set("title")(event.target.value)} />
        <div className={styles.formTimes}>
          <TextField label={d.day} type="date" value={fields.day} required onChange={(event) => set("day")(event.target.value)} />
          <TextField
            label={d.start}
            type="time"
            value={fields.start}
            required
            error={problem?.field === "start" ? problem.message : undefined}
            onChange={(event) => set("start")(event.target.value)}
          />
          <TextField
            label={d.end}
            type="time"
            value={fields.end}
            required
            error={problem?.field === "end" ? problem.message : undefined}
            onChange={(event) => set("end")(event.target.value)}
          />
        </div>
        <p className={styles.formHint}>{d.zoneHint(zone)}</p>
        {error && (
          <p className={styles.formError} role="alert">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  );
}
