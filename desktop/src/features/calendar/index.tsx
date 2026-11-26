import { PlaceholderPage } from "../../app/PlaceholderPage";
import { useT } from "../../i18n";
import type { Route } from "../../state/routes";

/** Calendar (UX-SPEC §3 Calendar). Owner: calendar agent. Strings: i18n/{en,it}/calendar.ts. */
export function CalendarPage({ route }: { route: Extract<Route, { name: "calendar" }> }) {
  const t = useT();
  return <PlaceholderPage title={t.calendar.title} subtitle={route.view === "month" ? t.calendar.month : t.calendar.week} />;
}
