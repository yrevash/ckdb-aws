"""Range/leaseholder distribution snapshot -- the mechanical "why" behind the
RPO/RTO numbers (see research/postmortem/04-cockroachdb-deployment-
resilience.md §2.3 step 4: "pair this with SHOW RANGES ... to visually show
ranges rebalancing off the dead region's replicas"). Kept as a compact
per-region tally rather than the full raw `SHOW RANGES` output, so it stays
cheap to embed in the JSON report and easy for the UI track to render as a
bar/donut without re-deriving anything.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from . import db
from .topology import DATABASE_NAME, Node

_REGION_RE = re.compile(r"region=([^,\"]+)")


def _regions_in(text: str) -> list[str]:
    return _REGION_RE.findall(text or "")


def range_snapshot(node: Node, *, table: str = "episodic_events") -> dict[str, Any]:
    conn = db.connect(node, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"USE {DATABASE_NAME}")  # noqa: S608 - fixed identifier
            cur.execute(
                f"SHOW RANGES FROM TABLE {table} WITH DETAILS"  # noqa: S608 - fixed identifier
            )
            columns = [c.name for c in cur.description]
            rows = [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    finally:
        conn.close()

    leaseholder_region_counts: Counter[str] = Counter()
    replica_region_counts: Counter[str] = Counter()
    for row in rows:
        for region in _regions_in(str(row.get("lease_holder_locality", ""))):
            leaseholder_region_counts[region] += 1
        for region in _regions_in(str(row.get("replica_localities", ""))):
            replica_region_counts[region] += 1

    return {
        "table": table,
        "range_count": len(rows),
        "leaseholder_region_counts": dict(leaseholder_region_counts),
        "replica_region_counts": dict(replica_region_counts),
    }
