/**
 * Entry of the recording capsule window (capsule.html, label `capsule`).
 *
 * Rust shows this window only while a lesson records or is saved (src-tauri/src/capsule.rs).
 * The page renders the existing RecordingCapsule from `capture.changed` notifications plus one
 * `capture.dashboard` read at start, and Stop calls `capture.stop`: the only methods Rust lets
 * this window call.
 */
import "./design";
import "./capsule.css";

import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { RecordingCapsule } from "./components/RecordingCapsule";
import { capsuleState, dashboardFrom } from "./capsule-model";
import { detectLang, I18nProvider } from "./i18n";
import type { CaptureDashboard } from "./rpc/schema";
import { createTauriTransport } from "./rpc/transports/tauri";

const transport = createTauriTransport();
const TICK_MS = 1000;

function Capsule() {
  const [dashboard, setDashboard] = useState<CaptureDashboard | null>(null);
  const [now, setNow] = useState(() => new Date());
  const [stopping, setStopping] = useState(false);

  useEffect(() => {
    const unsubscribe = transport.subscribe((method, params) => {
      if (method !== "capture.changed") return;
      const next = dashboardFrom(params);
      if (next) setDashboard(next);
    });
    transport
      .request("capture.dashboard", undefined)
      .then((answer) => setDashboard((current) => current ?? dashboardFrom(answer)))
      .catch((error: unknown) => console.warn("mynotes capsule: dashboard unavailable", error));
    const timer = setInterval(() => setNow(new Date()), TICK_MS);
    return () => {
      unsubscribe();
      clearInterval(timer);
    };
  }, []);

  const state = capsuleState(dashboard, now);
  if (!state) return null;
  const stop = () => {
    setStopping(true);
    transport
      .request("capture.stop", undefined)
      .catch((error: unknown) => console.warn("mynotes capsule: stop failed", error))
      .finally(() => setStopping(false));
  };
  return <RecordingCapsule state={stopping ? { kind: "saving" } : state} expanded onStop={stop} />;
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <I18nProvider lang={detectLang(navigator.languages)}>
        <Capsule />
      </I18nProvider>
    </StrictMode>,
  );
}
