/**
 * Handoff into the assistant from other screens (UX-SPEC §3, Generate notes outcomes):
 * PERSONAL_CONTENT_CONFLICT → "Ask the assistant to merge" opens the panel with the message
 * prefilled. The student reads it and presses Send; nothing is sent on their behalf.
 *
 *   const openAssistant = useOpenAssistant();
 *   openAssistant(t.assistant.handoff.personalContentConflict("STAT", 3));
 */
import { useCallback } from "react";

import type { AppActions } from "../../state";
import { useActions } from "../../state";

export function openAssistantWith(actions: Pick<AppActions, "ui" | "assistant">, message: string): void {
  actions.assistant.prefill(message);
  actions.ui.setAssistantOpen(true);
}

export function useOpenAssistant(): (message: string) => void {
  const actions = useActions();
  return useCallback((message: string) => openAssistantWith(actions, message), [actions]);
}
