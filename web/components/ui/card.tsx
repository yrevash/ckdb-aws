import type { ReactNode } from "react";

/**
 * The surface primitive. `title`/`aside` render a divided header; omit both for
 * a plain padded panel (`pad`). Everything in the kit sits on a Card so the
 * elevation, radius and hairline stay consistent across views.
 */
export function Card({
  title,
  aside,
  pad = false,
  className = "",
  children,
}: {
  title?: ReactNode;
  aside?: ReactNode;
  pad?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const hasHead = title !== undefined || aside !== undefined;
  return (
    <section className={`ui-card${pad && !hasHead ? " ui-card--pad" : ""} ${className}`.trim()}>
      {hasHead ? (
        <header className="ui-card__head">
          {title !== undefined ? <h3 className="ui-card__title">{title}</h3> : <span />}
          {aside}
        </header>
      ) : null}
      {hasHead ? <div className="ui-card__body">{children}</div> : children}
    </section>
  );
}
