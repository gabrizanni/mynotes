import { Activity, CalendarDays, FolderOpen, LibraryBig, MessagesSquare, NotebookPen, Settings } from "lucide-react";
import { useId } from "react";

import logo from "../assets/brand/mynotes-logo-256.png";
import { Sidebar, ToastRegion, type SidebarItem } from "../components";
import { AssistantPanel } from "../features/assistant";
import { CommandPalette } from "../features/palette"; // ⌘/Ctrl K
import { useT } from "../i18n";
import { routeForSection, routeToHash, sectionOf, shallowEqual, useActions, useAppState } from "../state";
import { RecorderStatus } from "./RecorderStatus";
import { AppRoutes } from "./Routes";
import styles from "./Shell.module.css";
import { shortcutLabel } from "./platform";
import { useHashRoute } from "./useHashRoute";
import { useShortcuts } from "./useShortcuts";

/** Window layout: sidebar (232 px) · page · assistant dock (right, ⌘J). */
export function Shell() {
  useHashRoute();
  useShortcuts();
  const t = useT();
  const mainId = useId();
  const { ui } = useActions();
  const { route, assistantOpen, toasts } = useAppState(
    (state) => ({ route: state.ui.route, assistantOpen: state.ui.assistantOpen, toasts: state.ui.toasts }),
    shallowEqual,
  );
  const courses = useAppState((state) => state.capture.dashboard?.courses ?? null);
  const attention = useAppState((state) => (state.queue.status?.jobs ?? []).filter((job) => ["FAILED", "NEEDS_REVIEW", "INTERRUPTED"].includes(job.status)).length);

  const active = sectionOf(route);
  const items: SidebarItem[] = [
    { id: "today", label: t.nav.today, icon: NotebookPen, href: routeToHash(routeForSection("today")), shortcut: shortcutLabel("1") },
    { id: "calendar", label: t.nav.calendar, icon: CalendarDays, href: routeToHash(routeForSection("calendar")), shortcut: shortcutLabel("2") },
    {
      id: "courses",
      label: t.nav.courses,
      icon: FolderOpen,
      href: routeToHash(routeForSection("courses")),
      shortcut: shortcutLabel("3"),
      children: (courses ?? []).map((course) => ({
        id: course.code,
        label: course.name,
        href: routeToHash({ name: "course", code: course.code, tab: "lessons" }),
        current: route.name === "course" && route.code === course.code,
      })),
    },
    { id: "library", label: t.nav.library, icon: LibraryBig, href: routeToHash(routeForSection("library")), shortcut: shortcutLabel("4") },
    {
      id: "activity",
      label: t.nav.activity,
      icon: Activity,
      href: routeToHash(routeForSection("activity")),
      shortcut: shortcutLabel("5"),
      badge: { value: attention, label: t.nav.activityCount(attention) },
    },
  ];

  return (
    <div className={styles.shell}>
      <a className={styles.skip} href={`#${mainId}`} onClick={(event) => {
        event.preventDefault();
        document.getElementById(mainId)?.focus();
      }}>
        {t.nav.skipToContent}
      </a>
      <Sidebar
        logoSrc={logo}
        items={items}
        activeId={active}
        status={<RecorderStatus />}
        actions={[
          { label: t.nav.assistant, icon: MessagesSquare, shortcut: shortcutLabel("J"), pressed: assistantOpen, onSelect: () => ui.toggleAssistant() },
          { label: t.nav.settings, icon: Settings, shortcut: shortcutLabel(","), pressed: route.name === "settings", onSelect: () => ui.navigate({ name: "settings", section: "assistant" }) },
        ]}
      />
      <main id={mainId} className={styles.main} tabIndex={-1}>
        <div className={styles.content}>
          <AppRoutes route={route} />
        </div>
      </main>
      {assistantOpen && (
        <div className={styles.dock}>
          <AssistantPanel />
        </div>
      )}
      {/* command palette, mounted once for the whole window */}
      <CommandPalette />
      <ToastRegion toasts={toasts} onDismiss={ui.dismissToast} />
    </div>
  );
}
