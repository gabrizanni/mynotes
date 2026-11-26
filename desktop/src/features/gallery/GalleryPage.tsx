import { Activity, CalendarDays, FileText, FolderOpen, LibraryBig, MessagesSquare, Mic, MoreHorizontal, NotebookPen, Settings, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";

import logo from "../../assets/brand/mynotes-logo-256.png";
import {
  AssistantDock,
  Banner,
  Button,
  Card,
  ChoiceCards,
  ChoiceChips,
  Dialog,
  DropZone,
  EmptyState,
  IconButton,
  knownStatuses,
  List,
  ListRow,
  PageHeader,
  Popover,
  ProgressBar,
  RecordingCapsule,
  Segmented,
  Select,
  Sheet,
  Sidebar,
  StageStepper,
  StatusChip,
  Stepper,
  Table,
  TagInput,
  TextArea,
  TextField,
  ToastRegion,
  Toggle,
  type CapsuleState,
  type ChipKind,
} from "../../components";
import { useT } from "../../i18n";
import styles from "./Gallery.module.css";

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section className={styles.section} aria-labelledby={id}>
      <h2 id={id} className={styles.sectionTitle}>
        {title}
      </h2>
      {children}
    </section>
  );
}

function Specimen({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <figure className={wide ? `${styles.specimen} ${styles.wide}` : styles.specimen}>
      <figcaption className={styles.caption}>{label}</figcaption>
      <div className={styles.stage}>{children}</div>
    </figure>
  );
}

/** Palette swatches, grouped by role; the hex values and contrast live in docs/DESIGN-TOKENS.md. */
const SWATCHES: ReadonlyArray<readonly [group: string, tokens: readonly string[]]> = [
  ["surfaces", ["bg-canvas", "bg-sunken", "bg-card", "bg-emphasis", "bg-inverse"]],
  ["ink", ["text-primary", "text-muted", "link", "border-input"]],
  ["actions", ["action-primary", "action-soft", "control-strong", "focus"]],
  ["meaning", ["success", "warning", "danger", "info", "accent-coral"]],
];

function Swatches() {
  return (
    <div className={styles.swatchGroups}>
      {SWATCHES.map(([group, tokens]) => (
        <div key={group} className={styles.chipRow}>
          <span className={styles.chipKind}>{group}</span>
          <ul role="list" className={styles.swatches}>
            {tokens.map((token) => (
              <li key={token} className={styles.swatch}>
                <span className={styles.swatchTile} style={{ background: `var(--color-${token})` }} aria-hidden="true" />
                <span className={styles.caption}>{token}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** Fixed reference time so the gallery renders identically in every screenshot. */
const REFERENCE = new Date("2026-11-26T09:47:00Z");
const minutesAgo = (minutes: number) => new Date(REFERENCE.getTime() - minutes * 60_000).toISOString();

interface LessonRow {
  id: string;
  number: number;
  date: string;
  recording: string;
  transcript: string;
  notes: string;
}

const LESSONS: LessonRow[] = [
  { id: "STAT-L05", number: 5, date: "17 Nov", recording: "COMPLETED", transcript: "COMPLETED", notes: "COMPLETED" },
  { id: "STAT-L06", number: 6, date: "24 Nov", recording: "COMPLETED", transcript: "COMPLETED", notes: "AWAITING_APPROVAL" },
  { id: "STAT-L07", number: 7, date: "26 Nov", recording: "COMPLETED", transcript: "RUNNING", notes: "NONE" },
];

/** `/__gallery`: every component and state, in the current language and theme. */
export function GalleryPage() {
  const t = useT();
  const s = t.gallery.sample;
  const [name, setName] = useState("Statistics");
  const [detail, setDetail] = useState<"concise" | "clear" | "detailed">("clear");
  const [images, setImages] = useState(3);
  const [topics, setTopics] = useState<string[]>(["Hypothesis testing", "Bayes"]);
  const [review, setReview] = useState(true);
  const [auto, setAuto] = useState(false);
  // onboarding (feat/desktop-onboarding): ChoiceCards and ChoiceChips specimens.
  const [assistantChoice, setAssistantChoice] = useState<"claude" | "codex" | "agy" | null>("claude");
  const [goals, setGoals] = useState<Array<"exam" | "understanding" | "exercises" | "work" | "personal">>(["exam"]);
  const [selected, setSelected] = useState<Set<string>>(new Set(["STAT-L07"]));
  const [dialogOpen, setDialogOpen] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [toasts, setToasts] = useState([{ id: "demo", message: s.toastText, tone: "success" as const, action: { label: s.undo, run: () => undefined } }]);

  const stages = (["CHECKING", "GENERATING", "BUILDING_WORD", "UPDATING_WORD"] as const).map((id) => ({ id, label: s.stages[id] }));
  const capsuleStates: Array<[string, CapsuleState]> = [
    ["upcoming", { kind: "upcoming", course: "Statistics", minutes: 3 }],
    ["starting", { kind: "starting" }],
    ["recording", { kind: "recording", elapsed: "48:20", title: "Statistics · 7" }],
    ["saving", { kind: "saving" }],
    ["saved", { kind: "saved", duration: "1:24:10" }],
    ["problem", { kind: "problem", problem: "microphone" }],
    ["problem", { kind: "problem", problem: "failed" }],
  ];
  const kinds: ChipKind[] = ["notes", "transcription", "recording", "assistant"];

  return (
    <div className={styles.page}>
      <PageHeader title={t.gallery.title} subtitle={t.gallery.intro} />

      <Section id="g-color" title={t.gallery.sections.color}>
        <Swatches />
      </Section>

      <Section id="g-type" title={t.gallery.sections.type}>
        <div className={styles.typeStack}>
          <p className="type-display">Corporate Finance</p>
          <p className="type-h1">Microeconomics</p>
          <p className="type-h2">{t.today.timeline.title}</p>
          <p className="type-title">{t.today.needsYou.title}</p>
          <p className="type-heading">{s.cardTitle}</p>
          <p className="type-body">{t.onboarding.intro}</p>
          <p className="type-small text-muted">{s.cardBody}</p>
          <p className="type-label">{s.courseName}</p>
          <p className="type-caption text-muted">{t.status.notes.toReview}</p>
          <p className="type-time">09:00–10:30 · 01:04:20</p>
          <p className="type-mono text-muted">capture status --json</p>
        </div>
      </Section>

      <Section id="g-buttons" title={t.gallery.sections.buttons}>
        <div className={styles.grid}>
          <Specimen label="primary · soft · ghost · outline · destructive">
            <div className={styles.row}>
              <Button variant="primary" icon={NotebookPen}>
                {t.today.action.generate}
              </Button>
              <Button variant="soft">{s.openInWord}</Button>
              <Button variant="ghost">{t.common.cancel}</Button>
              <Button variant="outline">{t.common.chooseFolder}</Button>
              <Button variant="destructive" icon={Trash2}>
                {s.dialogConfirm}
              </Button>
            </div>
          </Specimen>
          <Specimen label="sm · disabled · loading · icon">
            <div className={styles.row}>
              <Button size="sm" variant="soft">
                {t.common.retry}
              </Button>
              <Button variant="primary" disabled>
                {t.common.continue}
              </Button>
              <Button variant="soft" loading>
                {t.common.loading}
              </Button>
              <IconButton label={s.icon} icon={MoreHorizontal} />
              <IconButton label={s.icon} icon={MoreHorizontal} variant="soft" />
              <IconButton label={s.icon} icon={MoreHorizontal} variant="outline" size="sm" />
            </div>
          </Specimen>
          <Specimen label="inverse card (feature surface) · onInverse · ghostOnInverse" wide>
            <Card variant="inverse" padding="md">
              <div className={styles.row}>
                <Button variant="onInverse" icon={Mic}>
                  {t.today.action.record}
                </Button>
                <Button variant="ghostOnInverse">{t.common.more}</Button>
              </div>
            </Card>
          </Specimen>
        </div>
      </Section>

      <Section id="g-inputs" title={t.gallery.sections.inputs}>
        <div className={styles.grid}>
          <Specimen label="TextField">
            <div className={styles.stack}>
              <TextField label={s.courseName} hint={s.courseNameHint} value={name} onChange={(event) => setName(event.target.value)} />
              <TextField label={s.courseName} error={s.courseNameError} value="" onChange={() => undefined} />
            </div>
          </Specimen>
          <Specimen label="TextArea · Select">
            <div className={styles.stack}>
              <TextArea label={s.anythingElse} hint={s.anythingElseHint} optional rows={3} defaultValue="" />
              <Select
                label={s.notesLanguage}
                defaultValue="it"
                options={[
                  { value: "en", label: "English" },
                  { value: "it", label: "Italiano" },
                ]}
              />
            </div>
          </Specimen>
          <Specimen label="Segmented · Stepper">
            <div className={styles.stack}>
              <Segmented
                label={s.detail}
                value={detail}
                onChange={setDetail}
                options={[
                  { value: "concise", label: s.concise },
                  { value: "clear", label: s.clear },
                  { value: "detailed", label: s.detailed },
                ]}
              />
              <Stepper label={s.maxImages} value={images} min={0} max={12} onChange={setImages} />
            </div>
          </Specimen>
          <Specimen label="TagInput · Toggle">
            <div className={styles.stack}>
              <TagInput label={s.hardTopics} hint={s.hardTopicsHint} value={topics} onChange={setTopics} />
              <div>
                <Toggle label={s.reviewFirst} checked={review} onChange={setReview} />
                <Toggle label={s.autoRecord} description={t.today.action.fixRecorderBody} checked={auto} onChange={setAuto} />
              </div>
            </div>
          </Specimen>
          <Specimen label="DropZone" wide>
            <DropZone title={s.dropTitle} body={s.dropBody} accept="audio/*,.txt" onFiles={() => undefined} />
          </Specimen>
          {/* >>> onboarding (feat/desktop-onboarding) >>> */}
          <Specimen label="ChoiceCards" wide>
            <ChoiceCards
              label={t.onboarding.assistant.groupLabel}
              options={(["claude", "codex", "agy"] as const).map((id) => ({
                value: id,
                title: t.onboarding.assistant.providers[id].name,
                description: t.onboarding.assistant.providers[id].account,
              }))}
              value={assistantChoice}
              onChange={setAssistantChoice}
            />
          </Specimen>
          <Specimen label="ChoiceChips">
            <ChoiceChips
              label={t.onboarding.studies.goalsLabel}
              chips={(["exam", "understanding", "exercises", "work", "personal"] as const).map((goal) => ({ value: goal, label: t.onboarding.studies.goals[goal] }))}
              selected={goals}
              onChange={setGoals}
            />
          </Specimen>
          {/* <<< onboarding (feat/desktop-onboarding) <<< */}
        </div>
      </Section>

      <Section id="g-containers" title={t.gallery.sections.containers}>
        <div className={styles.grid}>
          <Specimen label="Card · emphasis · inverse">
            <div className={styles.stack}>
              <Card title={s.cardTitle} description={s.cardBody} headingLevel={3} actions={<Button size="sm">{s.openInWord}</Button>} />
              <Card variant="emphasis" title={t.today.finishSetup.title} description={t.today.finishSetup.progress(3, 4)} headingLevel={3} />
              <Card variant="inverse" title={t.today.hero.nextLesson} description="Microeconomics · 11:00–12:30" headingLevel={3} />
            </div>
          </Specimen>
          <Specimen label="List · ListRow">
            <Card padding="none">
              <List>
                <li>
                  <ListRow title={s.rowTitle} description={s.rowBody} meta={<StatusChip kind="notes" status="AWAITING_APPROVAL" />} onSelect={() => undefined} />
                </li>
                <li>
                  <ListRow title="Diritto commerciale · 2" description={t.today.needsYou.transcriptionToCheck} meta={<StatusChip kind="transcription" status="NEEDS_REVIEW" />} onSelect={() => undefined} />
                </li>
                <li>
                  <ListRow title={t.today.finishSetup.schedule} leading={<FileText size={16} strokeWidth={1.75} aria-hidden />} />
                </li>
              </List>
            </Card>
          </Specimen>
          <Specimen label="Table (multi-select)" wide>
            <Table
              caption="Statistics"
              rows={LESSONS}
              rowKey={(row) => row.id}
              selection={{ selected, onChange: setSelected, rowLabel: (row) => t.common.lesson(row.number), allLabel: t.common.seeAll }}
              columns={[
                { key: "lesson", header: s.tableLesson, cell: (row) => t.common.lesson(row.number), rowHeader: true },
                { key: "date", header: s.tableDate, cell: (row) => row.date },
                { key: "recording", header: s.tableRecording, cell: (row) => <StatusChip kind="recording" status={row.recording} /> },
                { key: "transcript", header: s.tableTranscript, cell: (row) => <StatusChip kind="transcription" status={row.transcript} /> },
                { key: "notes", header: s.tableNotes, cell: (row) => <StatusChip kind="notes" status={row.notes} /> },
              ]}
            />
          </Specimen>
        </div>
      </Section>

      <Section id="g-feedback" title={t.gallery.sections.feedback}>
        <div className={styles.grid}>
          <Specimen label="Banner" wide>
            <div className={styles.stack}>
              <Banner tone="info">{s.bannerInfo}</Banner>
              <Banner tone="warning" action={{ label: s.resume, onSelect: () => undefined }}>
                {s.bannerWarning}
              </Banner>
              <Banner tone="danger" action={{ label: s.openPrivacy, onSelect: () => undefined }}>
                {s.bannerDanger}
              </Banner>
              <Banner tone="success" onDismiss={() => undefined}>
                {s.bannerSuccess}
              </Banner>
            </div>
          </Specimen>
          <Specimen label="Toast">
            <ToastRegion inline toasts={toasts} onDismiss={(id) => setToasts((list) => list.filter((toast) => toast.id !== id))} />
          </Specimen>
          <Specimen label="EmptyState">
            <EmptyState icon={LibraryBig} title={s.emptyTitle} action={<Button variant="soft">{s.importRecording}</Button>} />
          </Specimen>
        </div>
      </Section>

      <Section id="g-chips" title={t.gallery.sections.chips}>
        <div className={styles.chipTable}>
          {kinds.map((kind) => (
            <div key={kind} className={styles.chipRow}>
              <span className={styles.chipKind}>{kind}</span>
              <div className={styles.chips}>
                {knownStatuses(kind).map((status) => (
                  <StatusChip key={status} kind={kind} status={status} />
                ))}
              </div>
            </div>
          ))}
          <Card variant="inverse" padding="md">
            <div className={styles.chips}>
              <StatusChip kind="recording" status="RECORDING" onInverse />
              <StatusChip kind="recording" status="SCHEDULED" onInverse />
              <StatusChip kind="recording" status="MIC_BLOCKED" onInverse />
            </div>
          </Card>
        </div>
      </Section>

      <Section id="g-progress" title={t.gallery.sections.progress}>
        <div className={styles.grid}>
          <Specimen label="ProgressBar (real bytes only)">
            <div className={styles.stack}>
              <ProgressBar label={s.download} value={212} max={300} valueText={s.downloadValue} />
              <Card variant="inverse" padding="md">
                <ProgressBar label={s.download} value={212} max={300} valueText={s.downloadValue} onInverse />
              </Card>
            </div>
          </Specimen>
          <Specimen label="StageStepper (no percentages)">
            <div className={styles.split}>
              <StageStepper
                label={s.stagesTitle}
                stages={stages}
                current="GENERATING"
                now={REFERENCE}
                timings={{ CHECKING: { startedAt: minutesAgo(6), finishedAt: minutesAgo(5.4) }, GENERATING: { startedAt: minutesAgo(5.4), finishedAt: null } }}
              />
              <StageStepper
                label={s.stagesTitle}
                stages={stages}
                current="done"
                now={REFERENCE}
                timings={{
                  CHECKING: { startedAt: minutesAgo(9), finishedAt: minutesAgo(8.6) },
                  GENERATING: { startedAt: minutesAgo(8.6), finishedAt: minutesAgo(2.5) },
                  BUILDING_WORD: { startedAt: minutesAgo(2.5), finishedAt: minutesAgo(1) },
                  UPDATING_WORD: { startedAt: minutesAgo(1), finishedAt: minutesAgo(0.5) },
                }}
              />
            </div>
          </Specimen>
        </div>
      </Section>

      <Section id="g-overlays" title={t.gallery.sections.overlays}>
        <div className={styles.grid}>
          <Specimen label="Popover">
            <div className={styles.popoverStage}>
              <Popover label={s.popoverTrigger} title={s.popoverTrigger} defaultOpen>
                <p className="type-small">{s.popoverBody}</p>
              </Popover>
            </div>
          </Specimen>
          <Specimen label="Dialog">
            <div className={styles.stack}>
              <Dialog
                inline
                open
                onClose={() => undefined}
                role="alertdialog"
                title={s.dialogTitle}
                description={s.dialogBody}
                footer={
                  <>
                    <Button variant="ghost">{t.common.cancel}</Button>
                    <Button variant="primary">{s.dialogConfirm}</Button>
                  </>
                }
              />
              <div className={styles.row}>
                <Button variant="outline" onClick={() => setDialogOpen(true)}>
                  {s.openDialog}
                </Button>
                <Button variant="outline" onClick={() => setSheetOpen(true)}>
                  {s.openSheet}
                </Button>
              </div>
            </div>
          </Specimen>
          <Specimen label="Sheet" wide>
            <div className={styles.sheetStage}>
              <Sheet
                inline
                open
                onClose={() => undefined}
                title={s.sheetTitle}
                footer={
                  <>
                    <span className={styles.footerNote}>{s.sheetBody}</span>
                    <Button variant="primary">{t.today.action.generate}</Button>
                  </>
                }
              >
                <StageStepper label={s.stagesTitle} stages={stages} current="CHECKING" now={REFERENCE} timings={{ CHECKING: { startedAt: minutesAgo(0.3), finishedAt: null } }} />
              </Sheet>
            </div>
          </Specimen>
        </div>
        <Dialog
          open={dialogOpen}
          onClose={() => setDialogOpen(false)}
          role="alertdialog"
          title={s.dialogTitle}
          description={s.dialogBody}
          footer={
            <>
              <Button variant="ghost" onClick={() => setDialogOpen(false)}>
                {t.common.cancel}
              </Button>
              <Button variant="primary" onClick={() => setDialogOpen(false)}>
                {s.dialogConfirm}
              </Button>
            </>
          }
        />
        <Sheet open={sheetOpen} onClose={() => setSheetOpen(false)} title={s.sheetTitle} footer={<Button variant="primary">{t.today.action.generate}</Button>}>
          <p className="type-body">{s.sheetBody}</p>
        </Sheet>
      </Section>

      <Section id="g-capsule" title={t.gallery.sections.capsule}>
        <div className={styles.capsules}>
          {capsuleStates.map(([label, state], index) => (
            <div key={`${label}-${index}`} className={styles.capsulePair}>
              <span className={styles.chipKind}>{label}</span>
              <RecordingCapsule state={state} onStop={() => undefined} onSkip={() => undefined} onTranscribe={() => undefined} onFix={() => undefined} />
              <RecordingCapsule state={state} expanded onStop={() => undefined} onSkip={() => undefined} onTranscribe={() => undefined} onFix={() => undefined} />
            </div>
          ))}
        </div>
      </Section>

      <Section id="g-shell" title={t.gallery.sections.shell}>
        <div className={styles.shellStage}>
          <div className={styles.sidebarFrame}>
            <Sidebar
              logoSrc={logo}
              activeId="today"
              items={[
                { id: "today", label: t.nav.today, icon: NotebookPen, href: "#/__gallery" },
                { id: "calendar", label: t.nav.calendar, icon: CalendarDays, href: "#/__gallery" },
                {
                  id: "courses",
                  label: t.nav.courses,
                  icon: FolderOpen,
                  href: "#/__gallery",
                  children: [
                    { id: "STAT", label: "Statistics", href: "#/__gallery", current: false },
                    { id: "MICRO", label: "Microeconomics", href: "#/__gallery", current: false },
                  ],
                },
                { id: "library", label: t.nav.library, icon: LibraryBig, href: "#/__gallery" },
                { id: "activity", label: t.nav.activity, icon: Activity, href: "#/__gallery", badge: { value: 2, label: t.nav.activityCount(2) } },
              ]}
              actions={[
                { label: t.nav.assistant, icon: MessagesSquare, onSelect: () => undefined },
                { label: t.nav.settings, icon: Settings, onSelect: () => undefined },
              ]}
            />
          </div>
          <div className={styles.dockFrame}>
            <AssistantDock context="Statistics · 7" assistantName="Claude Code" company="Anthropic" draft="" onDraftChange={() => undefined} onClose={() => undefined} available={false} />
          </div>
        </div>
      </Section>
    </div>
  );
}
