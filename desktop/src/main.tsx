import "./design";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { isThemePreference, readStoredTheme } from "./design/theme";
import { detectLang, isLang } from "./i18n";
import { connect } from "./rpc";
import { createAppStore, initialAppState, parseRoute, startSync } from "./state";

/**
 * Boot: language from ?lang, else the OS; theme from ?theme, else the saved choice, else the OS.
 * The transport is Tauri inside the app and the fixture mock in a browser (see rpc/index.ts).
 */
async function boot() {
  const params = new URLSearchParams(window.location.search);
  const langParam = params.get("lang");
  const themeParam = params.get("theme");
  const storage = (() => {
    try {
      return window.localStorage;
    } catch {
      return undefined;
    }
  })();
  const lang = isLang(langParam) ? langParam : detectLang(navigator.languages);
  const theme = isThemePreference(themeParam) ? themeParam : readStoredTheme(storage);

  const rpc = await connect();
  const app = createAppStore(rpc, initialAppState({ lang, theme, route: parseRoute(window.location.hash, window.location.pathname) }));
  startSync(app);

  const root = document.getElementById("root");
  if (!root) throw new Error("Missing #root");
  createRoot(root).render(
    <StrictMode>
      <App app={app} />
    </StrictMode>,
  );
}

void boot();
