import { useEffect } from "react";

import { applyTheme, storeTheme } from "../design/theme";
import { GalleryPage } from "../features/gallery";
import { OnboardingWindow, useOnboardingLaunch } from "../features/onboarding";
import { I18nProvider } from "../i18n";
import { StoreProvider, useAppState, type AppStore } from "../state";
import { Shell } from "./Shell";

function Themed() {
  const theme = useAppState((state) => state.ui.theme);
  const lang = useAppState((state) => state.ui.lang);
  const gallery = useAppState((state) => state.ui.route.name === "gallery");
  // onboarding: full window without sidebar; first launch resumes it.
  const route = useAppState((state) => state.ui.route);
  useOnboardingLaunch();
  useEffect(() => {
    applyTheme(document.documentElement, theme);
    try {
      storeTheme(window.localStorage, theme);
    } catch {
      // Storage blocked (private window): the choice lasts for this session only.
    }
  }, [theme]);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  const page = gallery ? <GalleryPage /> : route.name === "welcome" ? <OnboardingWindow route={route} /> : <Shell />;
  return <I18nProvider lang={lang}>{page}</I18nProvider>;
}

export function App({ app }: { app: AppStore }) {
  return (
    <StoreProvider value={app}>
      <Themed />
    </StoreProvider>
  );
}
