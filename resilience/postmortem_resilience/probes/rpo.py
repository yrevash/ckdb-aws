"""RPO=0 probe: every row whose write returned a successful commit ack --
including rows written *while the region was down* -- must still be present,
byte-for-byte, after recovery.

This is a precise proof, not a raw `SELECT count(*)` before/after (which
could mask one row lost and a concurrent unrelated one gained): the harness
records the exact (table, id_column, row_id, content_column, expected_
content) of every write it makes for the whole run duration via
`RpoTracker`, then this probe re-selects every single one of them and fails
if even one is missing or its content doesn't match what was written.

Charter R5: "verify data during the outage", not only after full recovery --
the harness calls this probe twice with different `phase` labels: once while
the killed region is still down (before `restore_region`), and once again
after full node liveness is restored. Both runs check the SAME set of
tracked rows on the SAME (surviving) node; the during-outage call is the one
that actually proves RPO=0 under the failure, not just eventually.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import db
from ..report import ProbeResult
from ..topology import Node


@dataclass
class _TrackedRow:
    table: str
    id_column: str
    row_id: str
    org_id: str
    content_column: str | None
    expected_content: str | None


@dataclass
class RpoTracker:
    """Accumulates every row the harness commits during a run so the final
    RPO probe can verify all of them survived, exactly."""

    rows: list[_TrackedRow] = field(default_factory=list)

    def track(self, *, table: str, id_column: str, row_id: str, org_id: str,
              content_column: str | None = None, expected_content: str | None = None) -> None:
        self.rows.append(
            _TrackedRow(table=table, id_column=id_column, row_id=row_id,
                        org_id=org_id, content_column=content_column,
                        expected_content=expected_content)
        )

    def __len__(self) -> int:
        return len(self.rows)


def probe_rpo(*, node: Node, tracker: RpoTracker, phase: str = "after_recovery") -> ProbeResult:
    conn = db.connect(node)
    missing: list[dict[str, str]] = []
    mismatched: list[dict[str, str]] = []
    try:
        with conn.cursor() as cur:
            for tracked in tracker.rows:
                if tracked.content_column:
                    cur.execute(
                        f"SELECT {tracked.content_column} FROM {tracked.table} "  # noqa: S608
                        f"WHERE org_id = %s AND {tracked.id_column} = %s",
                        (tracked.org_id, tracked.row_id),
                    )
                    row = cur.fetchone()
                    if row is None:
                        missing.append({"table": tracked.table, "row_id": tracked.row_id})
                    elif tracked.expected_content is not None and row[0] != tracked.expected_content:
                        mismatched.append({"table": tracked.table, "row_id": tracked.row_id})
                else:
                    cur.execute(
                        f"SELECT 1 FROM {tracked.table} "  # noqa: S608
                        f"WHERE org_id = %s AND {tracked.id_column} = %s",
                        (tracked.org_id, tracked.row_id),
                    )
                    if cur.fetchone() is None:
                        missing.append({"table": tracked.table, "row_id": tracked.row_id})
    finally:
        conn.close()

    rows_expected = len(tracker.rows)
    rows_lost = len(missing) + len(mismatched)
    status = "pass" if rows_lost == 0 else "fail"

    return ProbeResult(
        probe_type="rpo",
        status=status,
        measured_value=float(rows_lost),
        unit="rows_lost",
        details={
            "phase": phase,
            "rows_expected": rows_expected,
            "rows_found": rows_expected - len(missing),
            "rows_content_checked": sum(1 for t in tracker.rows if t.content_column),
            "rows_missing": missing,
            "rows_content_mismatched": mismatched,
        },
    )
