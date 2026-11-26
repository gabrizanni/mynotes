import { useEffect, useState } from "react";

import { Banner, ChoiceChips, Segmented, Select, Stepper, TagInput, TextArea, TextField, Toggle } from "../../../components";
import { catalogs, useT, type Lang } from "../../../i18n";
import { useActions, useRpc } from "../../../state";
import { useOnboarding } from "../controller";
import {
  COURSE_KINDS,
  DEFAULT_STYLE,
  DETAILS,
  GOALS,
  IMAGE_LIMIT,
  IMAGE_POLICIES,
  LAYOUTS,
  LECTURE_LANGUAGES,
  QUESTIONS,
  RECAP_LANGUAGES,
  defaultCourse,
  markStep,
  validateCourse,
  type CourseDraft,
  type Goal,
  type StyleDraft,
} from "../model";
import styles from "../Onboarding.module.css";
import { openPath } from "../platform";
import { StepLayout } from "../StepLayout";

/** 5 · What do you study? Free text plus optional goal chips (profile --background or --skip). */
export function StudiesStep() {
  const t = useT();
  const { record, update, complete, skip } = useOnboarding();
  const s = t.onboarding.studies;
  const studies = record.studies ?? { background: "", goals: [] };
  const set = (patch: Partial<typeof studies>) => update((current) => ({ ...current, studies: { ...studies, ...patch } }));
  const empty = studies.background.trim() === "" && studies.goals.length === 0;
  return (
    <StepLayout
      title={t.onboarding.steps.studies}
      lede={s.lede}
      primary={{ label: t.common.continue, onSelect: () => (empty ? skip("studies") : complete("studies")) }}
      secondary={[{ label: s.skip, onSelect: () => skip("studies") }]}
    >
      <TextArea
        label={s.label}
        hint={s.hint}
        placeholder={s.placeholder}
        rows={3}
        maxLength={2000}
        value={studies.background}
        onChange={(event) => set({ background: event.target.value })}
      />
      <ChoiceChips<Goal>
        label={s.goalsLabel}
        chips={GOALS.map((goal) => ({ value: goal, label: s.goals[goal] }))}
        selected={studies.goals}
        onChange={(goals) => set({ goals })}
      />
    </StepLayout>
  );
}

/** 6 · First course: name, languages, type, exam, hard topics, anything else. */
export function CourseStep() {
  const t = useT();
  const { record, update, complete } = useOnboarding();
  const c = t.onboarding.course;
  const course: CourseDraft = record.course ?? defaultCourse(record.language ?? "en");
  const [touched, setTouched] = useState(false);
  const errors = validateCourse(course);
  const set = (patch: Partial<CourseDraft>) => update((current) => ({ ...current, course: { ...course, ...patch } }));
  const nameError = touched && errors.name ? (errors.name === "required" ? c.nameRequired : c.nameTooLong) : undefined;

  const onContinue = () => {
    setTouched(true);
    if (errors.name) {
      document.getElementById("course-name")?.focus();
      return;
    }
    complete("course", (current) => ({ ...current, course: { ...course, name: course.name.trim() } }));
  };

  return (
    <StepLayout title={t.onboarding.steps.course} lede={c.lede} width="narrow" primary={{ label: t.common.continue, onSelect: onContinue }}>
      <TextField
        id="course-name"
        label={c.name}
        hint={c.nameHint}
        placeholder={c.namePlaceholder}
        autoComplete="off"
        required
        maxLength={120}
        value={course.name}
        error={nameError}
        onChange={(event) => set({ name: event.target.value })}
        onBlur={() => course.name && setTouched(true)}
      />
      <div className={styles.row2}>
        <Select
          label={c.lectureLanguage}
          options={LECTURE_LANGUAGES.map((lang) => ({ value: lang, label: c.languages[lang] }))}
          value={course.lectureLanguage}
          onChange={(event) => {
            const value = LECTURE_LANGUAGES.find((lang) => lang === event.target.value);
            if (value) set({ lectureLanguage: value });
          }}
        />
        <Segmented<Lang>
          label={c.notesLanguage}
          options={[
            { value: "en", label: c.languages.en },
            { value: "it", label: c.languages.it },
          ]}
          value={course.notesLanguage}
          onChange={(notesLanguage) => set({ notesLanguage })}
        />
      </div>
      <Segmented
        label={c.kind}
        options={COURSE_KINDS.map((kind) => ({ value: kind, label: c.kinds[kind] }))}
        value={course.kind}
        onChange={(kind) => set({ kind })}
      />
      <div className={styles.stack}>
        <TextField
          label={c.exam}
          placeholder={c.examPlaceholder}
          optional
          disabled={course.examUnknown}
          value={course.examUnknown ? "" : course.examFormat}
          onChange={(event) => set({ examFormat: event.target.value })}
        />
        <ChoiceChips<"unknown">
          label={c.exam}
          hideLabel
          chips={[{ value: "unknown", label: c.examUnknown }]}
          selected={course.examUnknown ? ["unknown"] : []}
          onChange={(selected) => set({ examUnknown: selected.includes("unknown") })}
        />
      </div>
      <TagInput
        label={c.hardTopics}
        hint={c.hardTopicsHint}
        placeholder={c.hardTopicsPlaceholder}
        optional
        value={course.hardTopics}
        onChange={(hardTopics) => set({ hardTopics })}
      />
      <TextArea
        label={c.anythingElse}
        hint={c.anythingElseHint}
        optional
        rows={3}
        maxLength={2000}
        value={course.anythingElse}
        onChange={(event) => set({ anythingElse: event.target.value })}
      />
    </StepLayout>
  );
}

const CHART = [14, 30, 58, 84, 96, 80, 54, 28, 12];

/** Client-side approximation of the Word page (UX-SPEC §7 gap 13); `style preview` is exact. */
export function StylePreview({ style, course, notesLanguage }: { style: StyleDraft; course: string; notesLanguage: Lang }) {
  const t = useT();
  const recapLang: Lang = style.recapLanguage === "same" ? notesLanguage : style.recapLanguage;
  const doc = catalogs[notesLanguage].onboarding.style.preview;
  const recap = catalogs[recapLang].onboarding.style.preview;
  const ui = t.onboarding.style.preview;
  const bullets = doc.bullets[style.detail];
  return (
    <figure className={styles.previewWrap} aria-labelledby="style-preview-label">
      <div className={styles.previewLabel}>
        <span id="style-preview-label">{ui.label}</span>
      </div>
      <div className={styles.paper} lang={notesLanguage}>
        <p className={styles.paperKicker}>{doc.lesson(course || t.onboarding.course.sampleCourse)}</p>
        <p className={styles.paperHeading}>{doc.heading}</p>
        {style.layout !== "lists" && <p className={styles.paperText}>{style.layout === "balanced" ? doc.prose[style.detail === "detailed" ? "clear" : "concise"] : doc.prose[style.detail]}</p>}
        {style.layout !== "prose" && (
          <ul className={styles.paperList}>
            {(style.layout === "balanced" ? bullets.slice(0, Math.max(2, bullets.length - 1)) : bullets).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
        {style.images !== "none" && (
          <div className={styles.figure}>
            <div className={styles.chart} aria-hidden="true">
              {CHART.map((height, index) => (
                <span key={index} className={styles.chartBar} style={{ height: `${height}%` }} />
              ))}
            </div>
            <p className={styles.figureCaption}>{doc.figure}</p>
            {style.images === "explained" && <p className={styles.paperText}>{doc.figureNote}</p>}
          </div>
        )}
        {style.recap && (
          <div className={styles.recapBox} lang={recapLang}>
            <p className={styles.paperSub}>{recap.recap}</p>
            <p>{recap.recapText}</p>
          </div>
        )}
        <div className={styles.stack}>
          <p className={styles.paperSub}>{doc.questions}</p>
          <ol className={styles.paperList}>
            {doc.questionList.slice(0, style.questions).map((question) => (
              <li key={question}>{question}</li>
            ))}
          </ol>
        </div>
      </div>
      <figcaption className={styles.previewCaption}>{ui.caption}</figcaption>
    </figure>
  );
}

/** 7 · Note style with a live preview; "Open sample in Word" saves the course, then previews. */
export function StyleStep() {
  const t = useT();
  const rpc = useRpc();
  const { ui } = useActions();
  const { record, update, complete, setup, courseCode, courseName, pending, queue } = useOnboarding();
  const s = t.onboarding.style;
  const style: StyleDraft = record.style ?? DEFAULT_STYLE;
  const notesLanguage = record.course?.notesLanguage ?? record.language ?? "en";
  const set = (patch: Partial<StyleDraft>) => update((current) => ({ ...current, style: { ...style, ...patch } }));
  const [wantSample, setWantSample] = useState(false);
  const [sampleError, setSampleError] = useState(false);
  const courseSaved = courseCode !== null && !pending.some((op) => op.id === "course");

  useEffect(() => {
    if (!wantSample || !courseSaved || !courseCode) return;
    setWantSample(false);
    const open = async () => {
      try {
        const { data } = await rpc.call("style.preview", { course: courseCode });
        const path = data.path ?? data.output ?? null;
        const opened = path ? await openPath(path) : false;
        if (!opened) ui.toast({ message: s.sampleOutsideApp, tone: "neutral" });
      } catch {
        setSampleError(true);
      }
    };
    void open();
  }, [wantSample, courseSaved, courseCode, rpc, ui, s.sampleOutsideApp]);

  const openSample = () => {
    setSampleError(false);
    update((current) => markStep({ ...current, style }, "style", "done"));
    setWantSample(true);
  };
  const ready = setup.phase === "complete";

  return (
    <StepLayout
      title={t.onboarding.steps.style}
      lede={s.lede}
      width="wide"
      primary={{ label: s.use, onSelect: () => complete("style", (current) => ({ ...current, style })) }}
      secondary={[{ label: s.openSample, onSelect: openSample, disabled: !ready, loading: wantSample && queue.error === null }]}
    >
      <div className={styles.styleGrid}>
        <div className={styles.controls}>
          <Segmented label={s.detail} options={DETAILS.map((value) => ({ value, label: s.details[value] }))} value={style.detail} onChange={(detail) => set({ detail })} />
          <Segmented label={s.layout} options={LAYOUTS.map((value) => ({ value, label: s.layouts[value] }))} value={style.layout} onChange={(layout) => set({ layout })} />
          <Segmented label={s.images} options={IMAGE_POLICIES.map((value) => ({ value, label: s.imagePolicies[value] }))} value={style.images} onChange={(images) => set({ images })} />
          {style.images !== "none" && (
            <Stepper
              label={s.imageLimit}
              value={style.imageLimit}
              min={IMAGE_LIMIT.min}
              max={IMAGE_LIMIT.max}
              valueText={s.imageLimitValue}
              onChange={(imageLimit) => set({ imageLimit })}
            />
          )}
          <Toggle label={s.recap} description={s.recapDescription} checked={style.recap} onChange={(recap) => set({ recap })} />
          {style.recap && (
            <Segmented
              label={s.recapLanguage}
              options={RECAP_LANGUAGES.map((value) => ({ value, label: s.recapLanguages[value] }))}
              value={style.recapLanguage}
              onChange={(recapLanguage) => set({ recapLanguage })}
            />
          )}
          <Stepper
            label={s.questions}
            value={style.questions}
            min={QUESTIONS.min}
            max={QUESTIONS.max}
            valueText={s.questionsValue}
            onChange={(questions) => set({ questions })}
          />
          {!ready && <p className={styles.note}>{s.sampleLater}</p>}
          {sampleError && <Banner tone="danger">{s.sampleFailed}</Banner>}
        </div>
        <StylePreview style={style} course={courseName} notesLanguage={notesLanguage} />
      </div>
    </StepLayout>
  );
}
