import { BookOpen, FileText, FolderOpen, Mic, Paperclip, type LucideIcon } from "lucide-react";
import { useState } from "react";

import { Banner, Button, Card, ChoiceList, DropZone, Icon, List, ListRow } from "../../components";
import { useI18n } from "../../i18n";
import { useActions, useRpc } from "../../state";
import { pickFolder, revealPath } from "../lesson/platform";
import { fileName, lessonAttachments, linkedFolders, linkPreview, type LinkedFolder, type LinkPreview } from "./model";
import styles from "./Courses.module.css";

const roleIcons: Record<LinkedFolder["role"], LucideIcon> = { materials: FolderOpen, personal_notes: BookOpen, recordings: Mic };

/** In the browser preview there is no folder dialog; the mock answers for this sample path. */
const PREVIEW_FOLDER = "~/Documents/University/Statistics";

/** Files tab: linked folders (Change / Show), re-link with a preview first, lesson attachments. */
export function FilesTab({ code, record }: { code: string; record: Record<string, unknown> | null }) {
  const { t } = useI18n();
  const f = t.courses.files;
  const actions = useActions();
  const rpc = useRpc();
  const folders = linkedFolders(record);
  const attachments = lessonAttachments(record);
  const [preview, setPreview] = useState<{ path: string; result: LinkPreview } | null>(null);
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const inspect = async (path: string) => {
    setBusy(true);
    try {
      const { data } = await rpc.call("course.link", { code, path, dryRun: true });
      const result = linkPreview(data);
      setPreview({ path, result });
      setChoices(Object.fromEntries(result.choices.map((choice) => [choice.role, choice.options[0] ?? ""])));
    } catch {
      actions.ui.toast({ message: f.previewError, tone: "danger" });
    } finally {
      setBusy(false);
    }
  };

  const choose = async () => {
    const path = rpc.transport === "mock" ? PREVIEW_FOLDER : await pickFolder(f.chooseFolder);
    if (path) await inspect(path);
  };

  const connect = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      await rpc.call("course.link", { code, path: preview.path });
      await actions.config.refresh();
      setPreview(null);
      actions.ui.toast({ message: f.connected, tone: "success" });
    } catch {
      actions.ui.toast({ message: f.connectError, tone: "danger" });
    } finally {
      setBusy(false);
    }
  };

  const reveal = async (path: string) => {
    try {
      const result = await revealPath(path);
      if (result === "preview") actions.ui.toast({ message: t.lesson.outcome.opening(fileName(path) ?? path), tone: "neutral" });
    } catch {
      actions.ui.toast({ message: t.lesson.outcome.openError, tone: "danger" });
    }
  };

  return (
    <div className={styles.stack}>
      <p className={styles.bodyText}>{f.intro}</p>
      <Card as="section" padding="none" title={f.folders}>
        {folders.length === 0 ? (
          <p className={styles.cardPad}>{f.noFolders}</p>
        ) : (
          <List>
            {folders.map((folder) => (
              <li key={folder.role}>
                <ListRow
                  leading={<Icon icon={roleIcons[folder.role]} size={16} />}
                  title={f.roles[folder.role]}
                  description={<span className={styles.path}>{folder.path}</span>}
                  meta={
                    <span className={styles.rowActions}>
                      <Button variant="ghost" size="sm" aria-label={f.changeLabel(f.roles[folder.role])} onClick={() => void choose()}>
                        {f.change}
                      </Button>
                      <Button variant="ghost" size="sm" aria-label={f.revealLabel(f.roles[folder.role])} onClick={() => void reveal(folder.path)}>
                        {f.reveal}
                      </Button>
                    </span>
                  }
                />
              </li>
            ))}
          </List>
        )}
      </Card>

      {preview ? (
        <Card as="section" title={f.previewTitle} description={preview.result.folder ?? preview.path} className={styles.previewPanel}>
          <ul role="list" className={styles.previewFacts}>
            <li>
              <Icon icon={FileText} size={16} className={styles.cardIcon} />
              <span>{f.previewCounts(preview.result.pdfs, preview.result.recordings)}</span>
            </li>
            {preview.result.notebook && (
              <li>
                <Icon icon={BookOpen} size={16} className={styles.cardIcon} />
                <span>{f.notebookFile(preview.result.notebook.name, preview.result.notebook.isNew)}</span>
              </li>
            )}
            {preview.result.handout && (
              <li>
                <Icon icon={FileText} size={16} className={styles.cardIcon} />
                <span>{f.handoutFile(preview.result.handout)}</span>
              </li>
            )}
          </ul>
          {preview.result.choices.map((choice) => (
            <ChoiceList
              key={choice.role}
              label={f.choose(f.roles[choice.role as LinkedFolder["role"]] ?? choice.role)}
              value={choices[choice.role] ?? ""}
              onChange={(value) => setChoices({ ...choices, [choice.role]: value })}
              choices={choice.options.map((option) => ({ value: option, label: fileName(option) ?? option, description: option }))}
            />
          ))}
          {preview.result.status === "needsSelection" && preview.result.choices.length === 0 && <Banner tone="warning">{f.previewError}</Banner>}
          <div className={styles.formActions}>
            <Button variant="primary" loading={busy} onClick={() => void connect()}>
              {f.connect}
            </Button>
            <Button variant="ghost" onClick={() => setPreview(null)}>
              {t.common.cancel}
            </Button>
          </div>
        </Card>
      ) : (
        <DropZone title={f.dropTitle} body={f.dropBody} buttonLabel={f.chooseFolder} multiple={false} onChoose={() => void choose()} onFiles={() => void choose()} />
      )}

      <Card as="section" padding="none" title={f.attachments} description={f.attachmentsBody}>
        {attachments.length === 0 ? (
          <p className={styles.cardPad}>{f.noAttachments}</p>
        ) : (
          <List>
            {attachments.map((item) => (
              <li key={`${item.lectureId}:${item.path}`}>
                <ListRow
                  leading={<Icon icon={Paperclip} size={16} />}
                  title={item.name}
                  description={item.lessonNumber !== null ? t.common.lesson(item.lessonNumber) : item.lectureId}
                  meta={
                    <Button variant="ghost" size="sm" aria-label={`${f.reveal} · ${item.name}`} onClick={() => void reveal(item.path)}>
                      {f.reveal}
                    </Button>
                  }
                />
              </li>
            ))}
          </List>
        )}
      </Card>
    </div>
  );
}
