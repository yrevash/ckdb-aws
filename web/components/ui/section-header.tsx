import type { ReactNode } from "react";

/**
 * A view/section header carrying the tick-rail signature (the eyebrow's leading
 * marks). Use `eyebrow` for the machine-register label, `title` for the human
 * headline, `meta` for a right-aligned control or provenance tag.
 */
export function SectionHeader({
  eyebrow,
  title,
  description,
  meta,
}: {
  eyebrow?: ReactNode;
  title: ReactNode;
  description?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <div className="ui-section">
      <div>
        {eyebrow ? <div className="ui-section__eyebrow">{eyebrow}</div> : null}
        <h2 className="ui-section__title">{title}</h2>
        {description ? <p className="ui-section__desc">{description}</p> : null}
      </div>
      {meta ? <div className="ui-section__meta">{meta}</div> : null}
    </div>
  );
}
