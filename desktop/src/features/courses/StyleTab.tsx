import { FileText, Image as ImageIcon, RotateCcw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Banner, Button, Card, Dialog, Icon, Segmented, Select, Stepper, Toggle } from "../../components";
import { cx } from "../../design/cx";
import { useI18n } from "../../i18n";
import { useActions, useRpc } from "../../state";
import { openPath } from "../lesson/platform";
import { useRpcData } from "../lesson/useRpcData";
import { fileName, styleChanges, styleDraft, type StyleDraft } from "./model";
import styles from "./Courses.module.css";

/** Style tab: the onboarding style controls, a live approximation, sample in Word, reset. */
export function StyleTab({ code }: { code: string }) {
  const { t } = useI18n();
  const s = t.courses.style;
  const actions = useActions();
  const rpc = useRpc();
  const shown = useRpcData("style.show", { course: code }, code);
  const saved = useMemo(() => (shown.status === "ready" ? styleDraft(shown.data.data.effective) : null), [shown]);
  const [draft, setDraft] = useState<StyleDraft | null>(null);
  const [busy, setBusy] = useState<"save" | "sample" | "reset" | null>(null);
  const [resetOpen, setResetOpen] = useState(false);
  useEffect(() => setDraft(saved), [saved]);

  if (shown.status === "error") {
    return (
      <Banner tone="danger" action={{ label: t.common.retry, onSelect: shown.reload }}>
        {s.loadError}
      </Banner>
    );
  }
  if (!draft || !saved) return <div className={styles.skeletonPanel} aria-busy="true" aria-label={t.common.loading} />;

  const changes = styleChanges(saved, draft);
  const dirty = Object.keys(changes).length > 0;
  const set = <K extends keyof StyleDraft>(key: K, value: StyleDraft[K]) => setDraft({ ...draft, [key]: value });

  const save = async () => {
    setBusy("save");
    try {
      await rpc.call("style.edit", { course: code, style: changes });
      shown.reload();
      actions.ui.toast({ message: s.saved, tone: "success" });
    } catch {
      actions.ui.toast({ message: s.saveError, tone: "danger" });
    } finally {
      setBusy(null);
    }
  };

  const sample = async () => {
    setBusy("sample");
    try {
      if (dirty) await rpc.call("style.edit", { course: code, style: changes });
      const { data } = await rpc.call("style.preview", { course: code });
      const result = await openPath(data.preview);
      if (result === "preview") actions.ui.toast({ message: t.lesson.outcome.opening(fileName(data.preview) ?? data.preview), tone: "neutral" });
      if (dirty) shown.reload();
    } catch {
      actions.ui.toast({ message: s.sampleError, tone: "danger" });
    } finally {
      setBusy(null);
    }
  };

  const reset = async () => {
    setBusy("reset");
    try {
      await rpc.call("style.edit", { course: code, reset: true });
      shown.reload();
      setResetOpen(false);
      actions.ui.toast({ message: s.resetDone, tone: "neutral" });
    } catch {
      actions.ui.toast({ message: s.saveError, tone: "danger" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={styles.styleGrid}>
      <div className={styles.form}>
        <p className={styles.bodyText}>{s.intro}</p>
        <Segmented
          label={s.detail}
          value={draft.writing_style}
          onChange={(value) => set("writing_style", value)}
          options={[
            { value: "concise", label: s.concise },
            { value: "clear_study", label: s.clear },
            { value: "detailed", label: s.detailed },
          ]}
        />
        <Segmented
          label={s.layout}
          value={draft.list_density}
          onChange={(value) => set("list_density", value)}
          options={[
            { value: "prose_first", label: s.prose },
            { value: "balanced", label: s.balanced },
            { value: "list_first", label: s.lists },
          ]}
        />
        <Segmented
          label={s.images}
          value={draft.image_policy}
          onChange={(value) => set("image_policy", value)}
          options={[
            { value: "none", label: s.imagesNone },
            { value: "essential", label: s.imagesEssential },
            { value: "explanatory", label: s.imagesExplained },
          ]}
        />
        {draft.image_policy !== "none" && (
          <div className={styles.limitRow}>
            <Toggle label={s.setLimit} checked={draft.image_limit !== null} onChange={(on) => set("image_limit", on ? 4 : null)} />
            {draft.image_limit !== null ? (
              <Stepper label={s.maxImages} value={draft.image_limit} min={0} max={12} onChange={(value) => set("image_limit", value)} valueText={s.maxImagesText} />
            ) : (
              <p className={styles.mutedText}>{s.noLimit}</p>
            )}
          </div>
        )}
        <Toggle label={s.recap} description={s.recapHint} checked={draft.recap} onChange={(value) => set("recap", value)} />
        {draft.recap && (
          <Select
            label={s.recapLanguage}
            value={draft.recap_language}
            onChange={(event) => set("recap_language", event.target.value as StyleDraft["recap_language"])}
            options={[
              { value: "same_as_notes", label: s.sameAsNotes },
              { value: "it", label: s.italian },
              { value: "en", label: s.english },
            ]}
          />
        )}
        <Stepper label={s.questions} value={draft.question_count} min={1} max={5} onChange={(value) => set("question_count", value)} valueText={s.questionsText} />
        <div className={styles.formActions}>
          <Button variant="primary" loading={busy === "save"} disabled={!dirty} onClick={() => void save()}>
            {s.save}
          </Button>
          <Button variant="soft" icon={FileText} loading={busy === "sample"} onClick={() => void sample()}>
            {s.sample}
          </Button>
          <Button variant="ghost" icon={RotateCcw} onClick={() => setResetOpen(true)}>
            {s.reset}
          </Button>
        </div>
      </div>
      <StylePreview draft={draft} />
      <Dialog
        open={resetOpen}
        onClose={() => setResetOpen(false)}
        role="alertdialog"
        title={s.resetTitle}
        description={s.resetBody}
        footer={
          <>
            <Button variant="ghost" onClick={() => setResetOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button variant="primary" loading={busy === "reset"} onClick={() => void reset()}>
              {s.resetConfirm}
            </Button>
          </>
        }
      />
    </div>
  );
}

/** A client-side approximation of one handout page (UX-SPEC §7 gap 13: only `style preview` is real). */
function StylePreview({ draft }: { draft: StyleDraft }) {
  const { t } = useI18n();
  const s = t.courses.style;
  const prose = draft.list_density !== "list_first";
  const points = draft.list_density !== "prose_first";
  const figures = draft.image_policy === "none" ? 0 : Math.min(draft.image_limit ?? 1, 1);
  return (
    <Card as="section" title={s.previewTitle} description={s.previewNote} headingLevel={2} className={styles.previewCard}>
      <div className={styles.page} aria-hidden="true">
        <p className={styles.pageHeading}>{s.previewHeading}</p>
        <p className={styles.pageSection}>{s.previewSection}</p>
        {prose && <p className={styles.pageProse}>{s.previewProse}</p>}
        {prose && draft.writing_style === "detailed" && <p className={styles.pageProse}>{s.previewProseMore}</p>}
        {points && (
          <ul className={styles.pagePoints}>
            {s.previewPoints.slice(0, draft.writing_style === "concise" ? 2 : 3).map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        )}
        {figures > 0 && (
          <figure className={styles.pageFigure}>
            <Icon icon={ImageIcon} size={20} />
            {draft.image_policy === "explanatory" && <figcaption>{s.previewFigure}</figcaption>}
          </figure>
        )}
        {draft.recap && (
          <div className={styles.pageRecap}>
            <p className={styles.pageSection}>{s.previewRecap}</p>
            <p className={styles.pageProse}>{s.previewRecapText}</p>
          </div>
        )}
        <p className={styles.pageSection}>{s.previewQuestions}</p>
        <ol className={cx(styles.pagePoints, styles.pageQuestions)}>
          {Array.from({ length: draft.question_count }, (_, index) => (
            <li key={index}>{s.previewQuestion(index + 1)}</li>
          ))}
        </ol>
      </div>
    </Card>
  );
}
