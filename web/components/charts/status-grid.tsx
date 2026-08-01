import { IconCheck, IconCross } from "@/components/ui/icons";

export type ProbeCell = {
  label: string;
  status: "pass" | "fail";
  /** mono meta line, e.g. "0 ms · found immediately". */
  meta?: string;
};

/**
 * A grid of probe outcomes. State is carried by icon + label + color together
 * (never color alone — dataviz status rule), so a colorblind or greyscale
 * reader still gets pass/fail from the check/cross glyph.
 */
export function StatusGrid({ items }: { items: ProbeCell[] }) {
  return (
    <div className="sgrid" role="list">
      {items.map((it) => {
        const good = it.status === "pass";
        return (
          <div className="sgrid__cell" role="listitem" key={it.label}>
            {good ? (
              <IconCheck className="sgrid__ico sgrid__ico--good" />
            ) : (
              <IconCross className="sgrid__ico sgrid__ico--critical" />
            )}
            <div>
              <div className="sgrid__label">{it.label}</div>
              <div className="sgrid__meta">
                {good ? "pass" : "fail"}
                {it.meta ? ` · ${it.meta}` : ""}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
