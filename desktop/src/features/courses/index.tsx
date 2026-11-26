import { PlaceholderPage } from "../../app/PlaceholderPage";
import { useT } from "../../i18n";
import { useAppState } from "../../state";
import type { Route } from "../../state/routes";

/** Courses list and course workspace (UX-SPEC §3 Courses). Strings: i18n/{en,it}/courses.ts. */
export function CoursesPage() {
  const t = useT();
  return <PlaceholderPage title={t.courses.title} />;
}

export function CoursePage({ route }: { route: Extract<Route, { name: "course" }> }) {
  const t = useT();
  const name = useAppState((state) => state.capture.dashboard?.courses.find((course) => course.code === route.code)?.name ?? route.code);
  return <PlaceholderPage title={name} subtitle={t.courses.tabs[route.tab]} />;
}
