import { useEffect, useMemo, useState, type FormEvent } from "react";

import { Button, Select, TagInput, TextArea, TextField, Toggle } from "../../components";
import { useI18n } from "../../i18n";
import { useActions, useRpc } from "../../state";
import { applyProfile, COURSE_KINDS, NOTE_LANGUAGES, profileDraft, validateProfile, type ProfileDraft } from "./model";
import styles from "./Courses.module.css";

/** Profile tab: the onboarding course form, saved with `course edit --file` (whole record). */
export function ProfileTab({ code, record }: { code: string; record: Record<string, unknown> | null }) {
  const { t } = useI18n();
  const p = t.courses.profile;
  const actions = useActions();
  const rpc = useRpc();
  const initial = useMemo(() => (record ? profileDraft(record) : null), [record]);
  const [draft, setDraft] = useState<ProfileDraft | null>(initial);
  const [showError, setShowError] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(initial), [initial]);

  if (!draft || !record) return <div className={styles.skeletonPanel} aria-busy="true" aria-label={t.common.loading} />;

  const error = validateProfile(draft);
  const dirty = JSON.stringify(draft) !== JSON.stringify(initial);
  const set = <K extends keyof ProfileDraft>(key: K, value: ProfileDraft[K]) => setDraft({ ...draft, [key]: value });
  const languages = NOTE_LANGUAGES.map((value) => ({ value, label: p.languages[value] }));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (error) {
      setShowError(true);
      return;
    }
    setBusy(true);
    try {
      await rpc.call("course.edit", { course: applyProfile(record, draft) });
      await actions.config.refresh();
      actions.ui.toast({ message: p.saved, tone: "success" });
    } catch {
      actions.ui.toast({ message: p.saveError, tone: "danger" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className={styles.form} onSubmit={(event) => void submit(event)} noValidate aria-label={`${t.courses.tabs.profile} · ${code}`}>
      <p className={styles.bodyText}>{p.intro}</p>
      <TextField label={p.name} required value={draft.name} error={showError && error === "nameRequired" ? p.nameError : undefined} onChange={(event) => set("name", event.target.value)} />
      <div className={styles.formRow}>
        <Select label={p.lectureLanguage} value={draft.lectureLanguage} options={languages} onChange={(event) => set("lectureLanguage", event.target.value)} />
        <Select label={p.notesLanguage} value={draft.notesLanguage} options={languages} onChange={(event) => set("notesLanguage", event.target.value)} />
      </div>
      <Select label={p.kind} value={draft.kind} options={COURSE_KINDS.map((value) => ({ value, label: p.kinds[value] }))} onChange={(event) => set("kind", event.target.value as ProfileDraft["kind"])} />
      <div className={styles.examGroup}>
        <TextField label={p.exam} hint={p.examHint} optional disabled={draft.examUnknown} value={draft.examUnknown ? "" : draft.assessment} onChange={(event) => set("assessment", event.target.value)} />
        <Toggle label={p.examUnknown} checked={draft.examUnknown} onChange={(value) => set("examUnknown", value)} />
      </div>
      <TagInput label={p.topics} hint={p.topicsHint} placeholder={p.topicsPlaceholder} optional value={draft.difficultTopics} onChange={(value) => set("difficultTopics", value)} />
      <TextArea label={p.context} hint={p.contextHint} optional rows={4} value={draft.context} onChange={(event) => set("context", event.target.value)} />
      <div className={styles.formActions}>
        <Button type="submit" variant="primary" loading={busy} disabled={!dirty}>
          {p.save}
        </Button>
      </div>
    </form>
  );
}
