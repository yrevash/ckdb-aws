import type { ReactNode } from "react";

/**
 * Shared wrapper for every bespoke SVG chart. Gives each one:
 *  · an accessible name/description (role="img" + <title>/<desc>);
 *  · a responsive viewBox (width:100% / height:auto in CSS);
 *  · an optional screen-reader table (the dataviz "table view exists" rule) so
 *    the data is never color-alone or shape-alone.
 * Charts render their marks as children in the same viewBox coordinate space.
 */
export function ChartFrame({
  title,
  desc,
  width,
  height,
  table,
  caption,
  children,
}: {
  title: string;
  desc?: string;
  width: number;
  height: number;
  /** rows of [label, value] rendered as a visually-hidden table fallback. */
  table?: [string, string][];
  caption?: ReactNode;
  children: ReactNode;
}) {
  return (
    <figure className="chart">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={title}
        preserveAspectRatio="xMinYMin meet"
      >
        <title>{title}</title>
        {desc ? <desc>{desc}</desc> : null}
        {children}
      </svg>
      {table && table.length > 0 ? (
        <table className="sr-only">
          <caption>{title}</caption>
          <tbody>
            {table.map(([k, v]) => (
              <tr key={k}>
                <th scope="row">{k}</th>
                <td>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
      {caption ? <figcaption className="chart-caption">{caption}</figcaption> : null}
    </figure>
  );
}
