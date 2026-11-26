import { useLayoutEffect } from "react";

import { routeForSection, SECTIONS } from "../state/routes";
import { useActions } from "../state";
import { isMac } from "./platform";

/** ⌘/Ctrl 1–5 sections, ⌘/Ctrl J assistant, ⌘/Ctrl , settings (UX-SPEC §1 and §3 Keyboard). */
export function useShortcuts(): void {
  const { ui } = useActions();
  // Layout effect: the listener is in place before the first paint, so a key pressed as soon
  // as the page shows is never lost (a passive effect left a gap under load).
  useLayoutEffect(() => {
    const mac = isMac();
    const onKey = (event: KeyboardEvent) => {
      const mod = mac ? event.metaKey : event.ctrlKey;
      if (!mod || event.altKey || event.shiftKey) return;
      const index = Number(event.key) - 1;
      const section = SECTIONS[index];
      if (section) {
        event.preventDefault();
        ui.navigate(routeForSection(section));
      } else if (event.key.toLowerCase() === "j") {
        event.preventDefault();
        ui.toggleAssistant();
      } else if (event.key === ",") {
        event.preventDefault();
        ui.navigate({ name: "settings", section: "assistant" });
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ui]);
}
