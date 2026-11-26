import { PlaceholderPage } from "../../app/PlaceholderPage";
import { useT } from "../../i18n";
import type { Route } from "../../state/routes";

/**
 * Lesson detail and the Generate notes sheet (UX-SPEC §3 Lesson detail, Generate notes flow).
 * Today's "Generate notes" and "Review notes" buttons land here with the lecture id.
 */
export function LessonPage({ route }: { route: Extract<Route, { name: "lesson" }> }) {
  const t = useT();
  return <PlaceholderPage title={t.lesson.title} subtitle={route.id} />;
}
