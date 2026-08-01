/**
 * Provenance tag (Reality Charter R9): every number states how it was produced.
 *  · measured  — a real run wrote it, cite the script;
 *  · replay    — a labelled deterministic fixture (real prior numbers, pre-recorded);
 *  · pending   — not yet measured; waits for the real-agent run (never a fake value).
 */
export type SourceKind = "measured" | "replay" | "pending";

const DEFAULT_LABEL: Record<SourceKind, string> = {
  measured: "measured",
  replay: "replay",
  pending: "pending real run",
};

export function SourceTag({
  kind,
  detail,
}: {
  kind: SourceKind;
  /** e.g. the script or artifact that produced the number. */
  detail?: string;
}) {
  return (
    <span className={`ui-source ui-source--${kind}`}>
      <span className="ui-source__key" aria-hidden="true" />
      {DEFAULT_LABEL[kind]}
      {detail ? <span aria-hidden="true">· {detail}</span> : null}
    </span>
  );
}
