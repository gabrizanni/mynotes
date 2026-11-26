import "./design";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./app/App";
import { installCrashGuard } from "./app/crashGuard";
import { isThemePreference, readStoredTheme } from "./design/theme";
import { detectLang, isLang } from "./i18n";
import { connect } from "./rpc";
import { createAppStore, initialAppState, parseRoute, startSync } from "./state";

/**
 * Boot: language from ?lang, else the OS; theme from ?theme, else the saved choice, else the OS.
 * The transport is Tauri inside the app and the fixture mock in a browser (see rpc/index.ts).
 */
// First, before anything can fail: no error may leave a blank window (see app/crashGuard.ts).
const crash = installCrashGuard(window, document.getElementById("root"));

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
  // React removes its whole tree after an uncaught render error: show the fallback instead.
  createRoot(root, { onUncaughtError: (error) => crash.report("render", error) }).render(
    <StrictMode>
      <App app={app} />
    </StrictMode>,
  );
}

boot().catch((error: unknown) => crash.report("boot", error));
