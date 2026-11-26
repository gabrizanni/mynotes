import { Bot, CalendarDays, Mic, ScanSearch, SlidersHorizontal, UserRound, AudioLines, type LucideIcon } from "lucide-react";
import { useId } from "react";

import { Icon, PageHeader } from "../../components";
import { cx } from "../../design/cx";
import { useT } from "../../i18n";
import { routeToHash, SETTINGS_SECTIONS, type Route, type SettingsSection } from "../../state/routes";
import { AssistantSection } from "./AssistantSection";
import { CalendarsSection, RecordingSection, TranscriptionSection } from "./CaptureSections";
import { DiagnosticsSection, GeneralSection, ProfileSection } from "./GeneralSections";
import styles from "./Settings.module.css";

const ICONS: Record<SettingsSection, LucideIcon> = {
  assistant: Bot,
  transcription: AudioLines,
  recording: Mic,
  calendars: CalendarDays,
  general: SlidersHorizontal,
  profile: UserRound,
  diagnostics: ScanSearch,
};

function SectionBody({ section }: { section: SettingsSection }) {
  switch (section) {
    case "assistant":
      return <AssistantSection />;
    case "transcription":
      return <TranscriptionSection />;
    case "recording":
      return <RecordingSection />;
    case "calendars":
      return <CalendarsSection />;
    case "general":
      return <GeneralSection />;
    case "profile":
      return <ProfileSection />;
    case "diagnostics":
      return <DiagnosticsSection />;
  }
}

function intro(t: ReturnType<typeof useT>, section: SettingsSection): string {
  switch (section) {
    case "assistant":
      return t.settings.assistant.intro;
    case "transcription":
      return t.settings.transcription.intro;
    case "recording":
      return t.settings.recording.intro;
    case "calendars":
      return t.settings.calendars.intro;
    case "general":
      return t.settings.general.languageHint;
    case "profile":
      return t.settings.profile.intro;
    case "diagnostics":
      return t.settings.diagnostics.intro;
  }
}

/** Settings (UX-SPEC §3): a section list on the left, one section at a time on the right. */
export function SettingsPage({ route }: { route: Extract<Route, { name: "settings" }> }) {
  const t = useT();
  const titleId = useId();
  return (
    <>
      <PageHeader title={t.settings.title} />
      <div className={styles.layout}>
        <nav className={styles.nav} aria-label={t.settings.sectionsLabel}>
          <ul role="list" className={styles.navList}>
            {SETTINGS_SECTIONS.map((section) => (
              <li key={section}>
                <a
                  href={routeToHash({ name: "settings", section })}
                  className={cx(styles.navItem, route.section === section && styles.navCurrent)}
                  aria-current={route.section === section ? "page" : undefined}
                >
                  <Icon icon={ICONS[section]} size={20} />
                  <span>{t.settings.sections[section]}</span>
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <section className={styles.content} aria-labelledby={titleId}>
          <header className={styles.sectionHead}>
            <h2 id={titleId} className={styles.sectionTitle}>
              {t.settings.sections[route.section]}
            </h2>
            <p className={styles.sectionIntro}>{intro(t, route.section)}</p>
          </header>
          <SectionBody key={route.section} section={route.section} />
        </section>
      </div>
    </>
  );
}
