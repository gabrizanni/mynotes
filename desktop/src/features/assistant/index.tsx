import { AssistantDock } from "../../components";
import { useT } from "../../i18n";
import { useActions, useAppState } from "../../state";
import type { Route } from "../../state/routes";
import type { Messages } from "../../i18n";

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

/** Connected assistant dock (shell only until the backend streams chat). */
export function AssistantPanel() {
  const t = useT();
  const { ui, assistant } = useActions();
  const route = useAppState((state) => state.ui.route);
  const draft = useAppState((state) => state.assistant.draft);
  const available = useAppState((state) => state.assistant.availability === "ready");
  return (
    <AssistantDock
      context={contextLabel(route, t)}
      assistantName="Claude Code"
      company="Anthropic"
      draft={draft}
      onDraftChange={assistant.setDraft}
      onClose={() => ui.setAssistantOpen(false)}
      available={available}
    />
  );
}
