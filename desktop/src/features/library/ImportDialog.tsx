import { CircleCheck, Copy, FileAudio } from "lucide-react";
import { useEffect, useState } from "react";

import { Button, Dialog, DropZone, Icon, Select } from "../../components";
import { useI18n } from "../../i18n";
import type { Library } from "../../rpc";
import { useActions } from "../../state";
import { audioPaths, importItems, openLessons, parseTarget, planImport, targetValue, type ImportRow } from "./model";
import styles from "./Library.module.css";

export type ImportRequest = { kind: "choose" } | { kind: "paths"; paths: string[] };

export interface ImportDialogProps {
  request: ImportRequest | null;
  library: Library | null;
  courses: ReadonlyArray<{ code: string; name: string }>;
  onClose: () => void;
}

type Step = { kind: "choose" } | { kind: "checking"; count: number } | { kind: "review"; rows: ImportRow[] } | { kind: "importing"; rows: ImportRow[] };

/** Import audio: inspect the files, choose a destination per file, then copy them in. */
export function ImportDialog(props: ImportDialogProps) {
  if (!props.request) return null;
  return <ImportFlow key={props.request.kind === "paths" ? props.request.paths.join("|") : "choose"} {...props} request={props.request} />;
}

function ImportFlow({ request, library, courses, onClose }: ImportDialogProps & { request: ImportRequest }) {
  const { t, format } = useI18n();
  const l = t.library.import;
  const actions = useActions();
  const [step, setStep] = useState<Step>(request.kind === "paths" ? { kind: "checking", count: request.paths.length } : { kind: "choose" });
  const [error, setError] = useState<string | null>(null);

  const inspect = async (paths: string[]) => {
    const audio = audioPaths(paths);
    if (audio.length === 0) {
      setError(l.notAudio);
      setStep({ kind: "choose" });
      return;
    }
    setError(null);
    setStep({ kind: "checking", count: audio.length });
    const result = await actions.library.inspectAudio(audio);
    if (!result.ok) {
      setError(l.failedInspect);
      setStep({ kind: "choose" });
      return;
    }
    setStep({ kind: "review", rows: planImport(result.value, library) });
  };

  useEffect(() => {
    if (request.kind === "paths") void inspect(request.paths);
    // Inspect once per request; the dialog remounts (new key) for a new drop.
  }, []);

  const rows = step.kind === "review" || step.kind === "importing" ? step.rows : [];
  const toImport = rows.filter((row) => !row.skip).length;
  const names = new Map(courses.map((course) => [course.code, course.name]));
  const lessons = openLessons(library);
  const targetOptions = [
    { value: "none", label: l.targetNone },
    ...lessons.map((lesson) => ({
      value: `lesson:${lesson.id}`,
      label: l.targetLesson(lesson.number ? t.common.courseLesson(names.get(lesson.course ?? "") ?? lesson.title, lesson.number) : lesson.title),
    })),
    ...courses.map((course) => ({ value: `course:${course.code}`, label: l.targetCourse(course.name) })),
  ];

  const submit = async () => {
    if (step.kind !== "review") return;
    if (toImport === 0) {
      actions.ui.toast({ message: l.nothing, tone: "neutral" });
      onClose();
      return;
    }
    setStep({ kind: "importing", rows: step.rows });
    const result = await actions.library.importAudio(importItems(step.rows));
    if (!result.ok) {
      setError(l.failedImport);
      setStep({ kind: "review", rows: step.rows });
      return;
    }
    const imported = (result.value.imported ?? []).filter((item) => !item.duplicate).length || toImport;
    actions.ui.toast({ message: l.done(imported), tone: "success" });
    onClose();
  };

  const setTarget = (index: number, value: string) => {
    if (step.kind !== "review") return;
    setStep({ kind: "review", rows: step.rows.map((row, i) => (i === index ? { ...row, target: parseTarget(value) } : row)) });
  };

  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={l.title}
      description={step.kind === "choose" ? l.chooseBody : step.kind === "checking" ? undefined : l.reviewBody}
      footer={
        step.kind === "review" || step.kind === "importing" ? (
          <>
            <Button variant="ghost" onClick={onClose}>
              {t.common.cancel}
            </Button>
            <Button variant="primary" loading={step.kind === "importing"} onClick={() => void submit()}>
              {toImport > 0 ? l.submit(toImport) : t.common.close}
            </Button>
          </>
        ) : undefined
      }
    >
      {step.kind === "choose" && (
        <DropZone title={l.dropTitle} body={l.dropBody} accept="audio/*" onFiles={(files) => void inspect(files.map((file) => file.name))} />
      )}
      {step.kind === "checking" && (
        <p className={styles.checking} role="status" aria-busy="true">
          {l.checking(step.count)}
        </p>
      )}
      {rows.length > 0 && (
        <ul className={styles.importList} role="list">
          {rows.map((row, index) => {
            const recorded = row.file.recorded_at ? new Date(row.file.recorded_at) : null;
            const meta = [recorded ? `${format.dayShort(recorded)} ${format.time(recorded)}` : l.noDate, row.file.duration ? format.clock(row.file.duration) : null]
              .filter(Boolean)
              .join(" · ");
            return (
              <li key={row.file.path} className={styles.importRow}>
                <span className={styles.importIcon}>
                  <Icon icon={row.skip ? Copy : FileAudio} size={16} />
                </span>
                <div className={styles.importText}>
                  <span className={styles.importName}>{row.file.name}</span>
                  <span className={styles.importMeta}>{meta}</span>
                </div>
                {row.skip ? (
                  <span className={styles.importSkip}>
                    <Icon icon={CircleCheck} size={14} />
                    {l.duplicate}
                  </span>
                ) : (
                  <div className={styles.importTarget}>
                    <Select
                      label={l.goesTo(row.file.name)}
                      hideLabel
                      value={targetValue(row.target)}
                      disabled={step.kind === "importing"}
                      options={targetOptions}
                      onChange={(event) => setTarget(index, event.target.value)}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {error && (
        <p className={styles.formError} role="alert">
          {error}
        </p>
      )}
    </Dialog>
  );
}
