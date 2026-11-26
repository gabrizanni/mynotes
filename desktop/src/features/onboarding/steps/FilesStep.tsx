import { AudioLines, FileCheck, FileText, FolderOpen, LoaderCircle, NotebookPen } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { Banner, Button, Card, ChoiceCards, DropZone, Icon, List, ListRow, Toggle } from "../../../components";
import { useT } from "../../../i18n";
import { useRpc } from "../../../state";
import { useOnboarding } from "../controller";
import { SELECTION_PARAM, baseName, folderPreview, type FolderPreview } from "../model";
import styles from "../Onboarding.module.css";
import { droppedPath, pickFolder } from "../platform";
import { StepLayout } from "../StepLayout";

type PreviewState = { kind: "idle" } | { kind: "loading" } | { kind: "ready"; preview: FolderPreview } | { kind: "error" };

/** 8 · Course folder: drop or choose → dry-run preview → one question if needed → Connect. */
export function FilesStep() {
  const t = useT();
  const rpc = useRpc();
  const { record, update, complete, courseCode, courseName } = useOnboarding();
  const f = t.onboarding.files;
  const folder = record.folder;
  const [preview, setPreview] = useState<PreviewState>({ kind: "idle" });
  const [choice, setChoice] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [linkError, setLinkError] = useState(false);

  const run = useCallback(
    async (path: string, selection: string | null, role: FolderPreview["question"] = null) => {
      if (!courseCode) return;
      setPreview({ kind: "loading" });
      try {
        const extra = selection && role ? { [SELECTION_PARAM[role.role]]: selection } : {};
        const { data } = await rpc.call("course.link", { code: courseCode, path, dryRun: true, ...extra });
        setPreview({ kind: "ready", preview: folderPreview(data) });
      } catch {
        setPreview({ kind: "error" });
      }
    },
    [rpc, courseCode],
  );

  // A folder kept in the draft (resume, Back) gets its preview again.
  useEffect(() => {
    if (folder?.path) void run(folder.path, null);
  }, [folder?.path, run]);

  const setFolder = (path: string) => {
    setChoice(null);
    setLinkError(false);
    update((current) => ({ ...current, folder: { path, reviewFirst: current.folder?.reviewFirst ?? false } }));
  };
  const choose = async () => {
    const path = await pickFolder(f.chooseTitle, courseName);
    if (path) setFolder(path);
  };

  const current = preview.kind === "ready" ? preview.preview : null;
  const question = current?.status === "selection" ? current.question : null;
  const connectable = current?.status === "ready";
  const [lastQuestion, setLastQuestion] = useState<FolderPreview["question"]>(null);
  useEffect(() => {
    if (question) setLastQuestion(question);
  }, [question]);

  const connect = async () => {
    if (!folder || !courseCode) return;
    if (!connectable) {
      setLinkError(true);
      return;
    }
    setConnecting(true);
    try {
      const { exitCode } = await rpc.call("course.link", {
        code: courseCode,
        path: folder.path,
        publicationMode: folder.reviewFirst ? "review" : "automatic",
        ...(choice && lastQuestion ? { [SELECTION_PARAM[lastQuestion.role]]: choice } : {}),
      });
      if (exitCode !== 0) throw new Error("link");
      complete("files");
    } catch {
      setLinkError(true);
    } finally {
      setConnecting(false);
    }
  };

  return (
    <StepLayout
      title={t.onboarding.steps.files}
      lede={f.lede}
      primary={folder ? { label: f.connect, onSelect: () => void connect(), loading: connecting, disabled: preview.kind === "loading" } : null}
      secondary={[{ label: f.useDefault, onSelect: () => complete("files", (record) => ({ ...record, folder: null })) }]}
    >
      {!folder && <DropZone title={f.dropTitle} body={f.dropBody} multiple={false} buttonLabel={f.choose} icon={FolderOpen} onFiles={(files) => files[0] && setFolder(droppedPath(files[0]))} onChoose={() => void choose()} />}

      {folder && (
        <div className={styles.folderRow}>
          <Icon icon={FolderOpen} size={20} />
          <span className={styles.folderPath} title={folder.path}>
            <span className="visually-hidden">{f.folder}: </span>
            {folder.path}
          </span>
          <Button variant="ghost" size="sm" onClick={() => void choose()}>
            {f.change}
          </Button>
        </div>
      )}

      {preview.kind === "loading" && (
        <p className={styles.liveLine} role="status">
          <Icon icon={LoaderCircle} size={20} className={styles.spin} />
          {f.previewing}
        </p>
      )}

      {preview.kind === "error" && (
        <Banner tone="danger" action={{ label: f.choose, onSelect: () => void choose() }}>
          {f.problem}
        </Banner>
      )}

      {question && folder && (
        <div className={styles.stack}>
          <ChoiceCards
            label={f.question}
            columns={1}
            options={question.options.map((option) => ({ value: option, title: baseName(option), description: option }))}
            value={choice}
            onChange={(value) => {
              setChoice(value);
              setLinkError(false);
              void run(folder.path, value, question);
            }}
          />
          <p className={styles.note}>{f.questionHint}</p>
        </div>
      )}

      {current && (
        <Card title={f.previewTitle} padding="md">
          <List>
            <li>
              <ListRow leading={<Icon icon={FileText} size={20} />} title={f.materials(current.materials)} />
            </li>
            <li>
              <ListRow leading={<Icon icon={AudioLines} size={20} />} title={f.recordings(current.recordings)} />
            </li>
            {current.notebook && (
              <li>
                <ListRow
                  leading={<Icon icon={NotebookPen} size={20} />}
                  title={current.notebook.name}
                  description={f.notebook}
                  meta={<span className={styles.tag}>{current.notebook.isNew ? f.notebookNew : f.notebookExisting}</span>}
                />
              </li>
            )}
            {current.courseNotes && (
              <li>
                <ListRow leading={<Icon icon={FileCheck} size={20} />} title={current.courseNotes} description={f.courseNotes} />
              </li>
            )}
          </List>
        </Card>
      )}

      {folder && (
        <Toggle
          label={f.reviewFirst}
          description={f.reviewFirstDescription}
          checked={folder.reviewFirst}
          onChange={(reviewFirst) => update((record) => ({ ...record, folder: record.folder ? { ...record.folder, reviewFirst } : null }))}
        />
      )}

      {linkError && <Banner tone="danger">{question ? f.needsChoice : f.linkFailed}</Banner>}
    </StepLayout>
  );
}
