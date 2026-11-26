import { PlaceholderPage } from "../../app/PlaceholderPage";
import { useT } from "../../i18n";
import type { Route } from "../../state/routes";

/** Library (UX-SPEC §3 Library). Strings: i18n/{en,it}/library.ts. Data: state.library. */
export function LibraryPage({ route }: { route: Extract<Route, { name: "library" }> }) {
  const t = useT();
  return <PlaceholderPage title={t.library.title} subtitle={route.unassigned ? t.library.filterUnassigned : undefined} />;
}
