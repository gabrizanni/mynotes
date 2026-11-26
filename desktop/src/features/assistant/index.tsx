import { useEffect, useMemo } from "react";

import { AssistantDock } from "../../components";
import { useT } from "../../i18n";
import type { Messages } from "../../i18n";
import { useActions, useAppState } from "../../state";
import type { Route } from "../../state/routes";
import { Conversation } from "./Conversation";
import { isBusy, pageContext, providerInfo, type ErrorFix, type Turn } from "./model";
import { useAssistantStore } from "./store";

export { openAssistantWith, useOpenAssistant } from "./handoff";

function contextLabel(route: Route, t: Messages): string {
  switch (route.name) {
    case "today":
      return t.nav.today;
    case "calendar":
      return t.nav.calendar;
    case "courses":
      return t.nav.courses;
    case "course":
      return route.code;
    case "library":
      return t.nav.library;
    case "activity":
      return t.nav.activity;
    case "lesson":
      return route.id;
    case "settings":
      return t.settings.sections[route.section];
    case "welcome":
      return t.onboarding.steps[route.step];
    case "gallery":
      return t.gallery.title;
  }
}

/** Open tool rows from the URL (`?assistantTools=toolu_1`), for screenshots of an expanded row. */
function openToolsFromUrl(): string[] {
  if (typeof window === "undefined") return [];
  return (new URLSearchParams(window.location.search).get("assistantTools") ?? "").split(",").filter(Boolean);
}

/** The connected assistant panel: streams `assistant.*` from `mynotes serve`. */
export function AssistantPanel() {
  const t = useT();
  const { ui, assistant } = useActions();
  const route = useAppState((state) => state.ui.route);
  const draft = useAppState((state) => state.assistant.draft);
  const defaultProvider = useAppState((state) => state.config.config?.providers?.default ?? null);
  const { store, view } = useAssistantStore();
  const openTools = useMemo(openToolsFromUrl, []);

  useEffect(() => {
    void store.restore();
  }, [store]);

  const label = contextLabel(route, t);
  const context = pageContext(route, label);
  const conversation = view.status === "ready" ? view.conversation : null;
  const provider = providerInfo(conversation?.provider ?? defaultProvider);
  const busy = isBusy(view);

  const send = (text: string) => {
    assistant.setDraft("");
    void store.send(text, context);
  };
  const fix = (kind: ErrorFix, turn: Turn) => {
    if (kind === "retry") void store.send(turn.message, context);
    if (kind === "signIn" || kind === "settings") ui.navigate({ name: "settings", section: "assistant" });
  };

  return (
    <AssistantDock
      context={label}
      assistantName={provider.name}
      company={provider.company}
      draft={draft}
      onDraftChange={assistant.setDraft}
      onClose={() => ui.setAssistantOpen(false)}
      onSend={send}
      onStop={() => void store.stop()}
      onNewConversation={() => store.reset()}
      busy={busy}
      status={view.status === "restoring" ? t.assistant.restoring : undefined}
      conversation={conversation ? <Conversation turns={conversation.turns} provider={provider} onFix={fix} openTools={openTools} /> : null}
    />
  );
}
