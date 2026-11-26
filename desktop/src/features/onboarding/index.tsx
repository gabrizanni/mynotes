/**
 * Onboarding (UX-SPEC §2): a full window with three labeled segments and 13 named steps.
 * `App` renders `OnboardingWindow` for `/welcome/:step` (no sidebar) and calls
 * `useOnboardingLaunch()` so a first launch opens the first incomplete step.
 * Logic: model.ts (tested); wiring: controller.tsx; strings: i18n/{en,it}/onboarding.ts.
 */
import type { Route } from "../../state/routes";
import { OnboardingWindow } from "./OnboardingWindow";

export { OnboardingWindow, useOnboardingLaunch } from "./OnboardingWindow";

/** Kept for `app/Routes.tsx`; App short-circuits welcome routes to the full window. */
export function WelcomePage({ route }: { route: Extract<Route, { name: "welcome" }> }) {
  return <OnboardingWindow route={route} />;
}
