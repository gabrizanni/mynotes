import { FileAudio, FileText, Pencil, Trash2 } from "lucide-react";
import { useState } from "react";

import { ChoiceList, Disclosure, Menu, Slider, TabNav, TextArea } from "../../components";
import { useI18n } from "../../i18n";
import styles from "./Gallery.module.css";

/** Components added for the course and lesson screens (feat/desktop-lessons). */
export function LessonSpecimens() {
  const { t, format } = useI18n();
  const g = t.lesson.generate;
  const [quality, setQuality] = useState<"standard" | "approfondito" | "revisione">("standard");
  const [position, setPosition] = useState(754);
  return (
    <section className={styles.section} aria-labelledby="g-lessons">
      <h2 id="g-lessons" className={styles.sectionTitle}>
        {t.gallery.sections.lessons}
      </h2>
      <div className={styles.grid}>
        <figure className={`${styles.specimen} ${styles.wide}`}>
          <figcaption className={styles.caption}>TabNav</figcaption>
          <div className={styles.stage}>
            <TabNav
              label={t.courses.workspace.sections("Statistics")}
              current="notes"
              items={(["lessons", "notes", "style", "profile", "files"] as const).map((id) => ({ id, label: t.courses.tabs[id], href: "#/__gallery" }))}
            />
          </div>
        </figure>
        <figure className={styles.specimen}>
          <figcaption className={styles.caption}>ChoiceList</figcaption>
          <div className={styles.stage}>
            <ChoiceList
              label={g.quality}
              value={quality}
              onChange={setQuality}
              choices={[
                { value: "standard", label: g.standard, description: g.standardHint },
                { value: "approfondito", label: g.thorough, description: g.thoroughHint },
                { value: "revisione", label: g.fullReview, description: g.fullReviewHint },
              ]}
            />
          </div>
        </figure>
        <figure className={styles.specimen}>
          <figcaption className={styles.caption}>Disclosure · Slider · Menu</figcaption>
          <div className={`${styles.stage} ${styles.stack}`}>
            <Disclosure label={g.more} summary={g.moreSummary} defaultOpen>
              <TextArea label={g.instructions} hint={g.instructionsHint} rows={2} defaultValue="" />
            </Disclosure>
            <Slider label={t.lesson.player.position} value={position} max={5521} onChange={setPosition} valueText={t.lesson.player.positionText(format.clock(position), format.clock(5521))} />
            <Menu
              label={t.lesson.menu.label}
              align="start"
              items={[
                { id: "edit", label: t.lesson.menu.edit, icon: Pencil, onSelect: () => undefined },
                { id: "recording", label: t.lesson.menu.linkRecording, icon: FileAudio, onSelect: () => undefined },
                { id: "transcript", label: t.lesson.menu.linkTranscript, icon: FileText, onSelect: () => undefined },
                { id: "remove", label: t.lesson.menu.remove, icon: Trash2, destructive: true, onSelect: () => undefined },
              ]}
            />
          </div>
        </figure>
      </div>
    </section>
  );
}
