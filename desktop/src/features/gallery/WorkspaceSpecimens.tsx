import { CalendarPlus, FileAudio, Plus, Search } from "lucide-react";
import { useState } from "react";

import { AnchoredPopover, FilterChip, Icon, List, ListRow, Menu, StatusChip, TextField } from "../../components";
import { useT } from "../../i18n";
import styles from "./Gallery.module.css";

/** Components added for Calendar, Library, Activity and Settings, in their main states. */
export function WorkspaceSpecimens() {
  const t = useT();
  const [unassigned, setUnassigned] = useState(true);
  const [hidden, setHidden] = useState(false);
  const [query, setQuery] = useState("");
  return (
    <div className={styles.grid}>
      <figure className={styles.specimen}>
        <figcaption className={styles.caption}>Menu (open) · Menu icon trigger</figcaption>
        <div className={`${styles.stage} ${styles.menuStage}`}>
          <div className={styles.row}>
            <Menu
              label={t.calendar.add}
              icon={Plus}
              size="sm"
              defaultOpen
              items={[
                { id: "schedule", label: t.calendar.scheduleLesson, description: t.calendar.scheduleLessonBody, icon: CalendarPlus, onSelect: () => undefined },
                { id: "import", label: t.calendar.importAudio, description: t.calendar.importAudioBody, icon: FileAudio, onSelect: () => undefined },
              ]}
            />
            <Menu
              trigger="icon"
              size="sm"
              label={t.activity.actions.more("Statistics · 7")}
              items={[{ id: "remove", label: t.activity.actions.remove, tone: "danger", onSelect: () => undefined }]}
            />
          </div>
        </div>
      </figure>
      <figure className={styles.specimen}>
        <figcaption className={styles.caption}>FilterChip · TextField search</figcaption>
        <div className={styles.stage}>
          <div className={styles.stack}>
            <TextField label={t.library.search} hideLabel leadingIcon={Search} type="search" placeholder={t.library.searchPlaceholder} value={query} onChange={(event) => setQuery(event.target.value)} />
            <div className={styles.row}>
              <FilterChip label={t.library.filterUnassigned} pressed={unassigned} count={1} onChange={setUnassigned} />
              <FilterChip label={t.library.filterHidden} pressed={hidden} count={1} onChange={setHidden} />
            </div>
          </div>
        </div>
      </figure>
      <figure className={styles.specimen}>
        <figcaption className={styles.caption}>ListRow selected</figcaption>
        <div className={styles.stage}>
          <List>
            <li>
              <ListRow title="Statistics · Lesson 7" description="Thu 26 Nov · 1 h 32 min" leading={<Icon icon={FileAudio} size={16} />} meta={<StatusChip kind="transcription" status="RUNNING" />} selected onSelect={() => undefined} />
            </li>
            <li>
              <ListRow title="Recording 19 Nov 14:02" description="Thu 19 Nov · 52 min" leading={<Icon icon={FileAudio} size={16} />} meta={<StatusChip kind="recording" status="NEEDS_COURSE" />} onSelect={() => undefined} />
            </li>
          </List>
        </div>
      </figure>
      <figure className={styles.specimen}>
        <figcaption className={styles.caption}>AnchoredPopover (inline)</figcaption>
        <div className={styles.stage}>
          <AnchoredPopover open inline anchor={null} onClose={() => undefined} title={t.calendar.popover.label("Corporate Finance")}>
            <div className={styles.stack}>
              <p className="type-heading">Corporate Finance</p>
              <p className="type-small text-muted">{t.calendar.popover.clashWith("Diritto commerciale", "09:00–10:30")}</p>
              <div className={styles.row}>
                <StatusChip kind="recording" status="CONFLICT" />
              </div>
            </div>
          </AnchoredPopover>
        </div>
      </figure>
    </div>
  );
}
