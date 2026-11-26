import { PlaceholderPage } from "../../app/PlaceholderPage";
import { useT } from "../../i18n";

/** Activity (UX-SPEC §3 Activity). Strings: i18n/{en,it}/activity.ts. Data: state.queue, state.jobs. */
export function ActivityPage() {
  const t = useT();
  return <PlaceholderPage title={t.activity.title} />;
}
