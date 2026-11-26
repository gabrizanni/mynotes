import { Hammer } from "lucide-react";
import type { ReactNode } from "react";

import { EmptyState, PageHeader } from "../components";
import { useT } from "../i18n";

/** Temporary page body for sections another agent is building. Replace it, don't extend it. */
export function PlaceholderPage({ title, subtitle, children }: { title: string; subtitle?: ReactNode; children?: ReactNode }) {
  const t = useT();
  return (
    <>
      <PageHeader title={title} subtitle={subtitle} />
      {children}
      <EmptyState icon={Hammer} title={t.common.comingNext} body={t.common.comingNextBody} align="start" />
    </>
  );
}
