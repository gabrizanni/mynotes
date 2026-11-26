import { FileAudio } from "lucide-react";
import { useId, useRef, useState, type DragEvent } from "react";

import { cx } from "../../design/cx";
import { useT } from "../../i18n";
import { Button } from "../Button";
import { Icon } from "../Icon";
import styles from "./DropZone.module.css";

export interface DropZoneProps {
  title: string;
  body?: string;
  /** e.g. "audio/*,.txt" */
  accept?: string;
  multiple?: boolean;
  onFiles: (files: File[]) => void;
  buttonLabel?: string;
  /** Replaces the file picker, e.g. with a native folder dialog (feat/desktop-lessons). */
  onChoose?: () => void;
}

/** Drag-and-drop target whose keyboard path is the "Choose files" button. */
export function DropZone({ title, body, accept, multiple = true, onFiles, buttonLabel, onChoose }: DropZoneProps) {
  const t = useT();
  const [active, setActive] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const titleId = useId();

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setActive(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length > 0) onFiles(multiple ? files : files.slice(0, 1));
  };

  return (
    <div
      role="group"
      aria-labelledby={titleId}
      className={cx(styles.zone, active && styles.active)}
      onDragEnter={(event) => {
        event.preventDefault();
        setActive(true);
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setActive(false);
      }}
      onDrop={onDrop}
    >
      <Icon icon={FileAudio} size={24} className={styles.icon} />
      <p className={styles.title} id={titleId}>
        {active ? t.common.dropRelease : title}
      </p>
      {body && <p className={styles.body}>{body}</p>}
      <div className={styles.actions}>
        <Button variant="outline" size="sm" onClick={() => (onChoose ? onChoose() : input.current?.click())}>
          {buttonLabel ?? t.common.chooseFiles}
        </Button>
      </div>
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (files.length > 0) onFiles(files);
          event.target.value = "";
        }}
      />
    </div>
  );
}
