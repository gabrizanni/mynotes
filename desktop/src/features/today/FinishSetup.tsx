import { CalendarClock, CalendarPlus, Mic, UserRound, type LucideIcon } from "lucide-react";

import { Card, Icon, List, ListRow } from "../../components";
import { useT } from "../../i18n";
import { routeToHash } from "../../state";
import type { SetupItem, SetupItemId } from "./model";
import styles from "./Today.module.css";

const icons: Record<SetupItemId, LucideIcon> = { profile: UserRound, schedule: CalendarPlus, microphone: Mic, automatic: CalendarClock };

/** Optional onboarding steps the student skipped (UX-SPEC §2 "Finish setup"). Hidden when none. */
export function FinishSetup({ items }: { items: readonly SetupItem[] }) {
  const t = useT();
  const pending = items.filter((item) => !item.done);
  if (pending.length === 0) return null;
  return (
    <Card variant="emphasis" title={t.today.finishSetup.title} description={t.today.finishSetup.progress(items.length - pending.length, items.length)}>
      <List className={styles.setupList}>
        {pending.map((item) => (
          <li key={item.id}>
            <ListRow title={t.today.finishSetup[item.id]} leading={<Icon icon={icons[item.id]} size={16} />} href={routeToHash(item.target)} />
          </li>
        ))}
      </List>
    </Card>
  );
}
