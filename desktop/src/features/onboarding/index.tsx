import { PlaceholderPage } from "../../app/PlaceholderPage";
import { useT } from "../../i18n";
import type { Route } from "../../state/routes";

/** Onboarding (UX-SPEC §2). Steps are named, never numbered. Strings: i18n/{en,it}/onboarding.ts. */
export function WelcomePage({ route }: { route: Extract<Route, { name: "welcome" }> }) {
  const t = useT();
  return <PlaceholderPage title={t.onboarding.steps[route.step]} />;
}
