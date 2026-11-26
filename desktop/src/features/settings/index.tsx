import { PlaceholderPage } from "../../app/PlaceholderPage";
import { useT } from "../../i18n";
import type { Route } from "../../state/routes";

/** Settings (UX-SPEC §3 Settings). Strings: i18n/{en,it}/settings.ts. */
export function SettingsPage({ route }: { route: Extract<Route, { name: "settings" }> }) {
  const t = useT();
  return <PlaceholderPage title={t.settings.title} subtitle={t.settings.sections[route.section]} />;
}
