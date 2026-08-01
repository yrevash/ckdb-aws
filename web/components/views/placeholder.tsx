import type { ReactNode } from "react";

import { SourceTag } from "@/components/ui";

/**
 * A clearly-labelled Wave 2 stub. The three deep views (Incident, Resilience,
 * Memory & Retrieval) are built by later agents on this same kit — this states
 * what will live here and which real data source drives it, so the shell is
 * navigable and honest in the meantime.
 */
export function Placeholder({
  glyph,
  title,
  what,
  sources,
}: {
  glyph: ReactNode;
  title: string;
  what: string;
  sources: string[];
}) {
  return (
    <div className="stub">
      <div className="stub__glyph">{glyph}</div>
      <h2 className="stub__title">{title}</h2>
      <p className="stub__text">{what}</p>
      <div className="stub__sources">
        {sources.map((s) => (
          <SourceTag key={s} kind="pending" detail={s} />
        ))}
      </div>
      <span className="ui-badge ui-badge--neutral">Wave 2 · not yet built</span>
    </div>
  );
}
