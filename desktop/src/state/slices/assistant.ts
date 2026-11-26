/**
 * Assistant panel (shell only). Real streaming waits for `serve_assistant.py`
 * (UX-SPEC §7 gap 1); until then the panel shows suggestions and a disabled composer.
 */
import type { SliceContext } from "../types";

export interface AssistantMessage {
  readonly id: string;
  readonly role: "user" | "assistant" | "tool";
  readonly text: string;
}

export interface AssistantState {
  readonly draft: string;
  readonly messages: readonly AssistantMessage[];
  /** `unavailable` until the backend exposes a streaming chat. */
  readonly availability: "unavailable" | "ready";
}

export const assistantInitial: AssistantState = { draft: "", messages: [], availability: "unavailable" };

export function assistantActions(ctx: SliceContext<"assistant">) {
  return {
    setDraft: (draft: string) => {
      ctx.set({ draft });
    },
    prefill: (text: string) => {
      ctx.set({ draft: text });
    },
  };
}
