"""Force the leaseholders of the probed tables into the region the harness is
about to kill, and verify -- via `SHOW RANGES ... WITH DETAILS`, not by
assumption -- that they actually moved there before the kill happens.

Why this exists (R5 / the audit finding): `db/bootstrap/010_multiregion.sql`
sets every table's locality to `REGIONAL BY TABLE IN PRIMARY REGION`, which
pins the lease preference to the database's PRIMARY REGION (us-east-1) --
explicitly documented there as never moving when a *non-primary* region
(us-east-2, the kill target) goes down. That is the exact bug the audit
found: killing us-east-2 exercised zero leaseholders and the harness
measured a normal write to a never-touched node, not a failover.

This module does not change that database-level default (out of scope --
db/bootstrap/010_multiregion.sql is not owned by this package). Instead it
issues a narrow, documented, per-table zone-config override at harness
runtime, immediately before each region-kill run: `ALTER TABLE ... CONFIGURE
ZONE USING lease_preferences = '[[+region=<kill_region>]]'`. Multi-region
databases protect `lease_preferences` from direct edits by default (`ERROR:
attempting to modify protected field "lease_preferences" of a multi-region
zone configuration`) -- confirmed live against this cluster -- so the
session variable `override_multi_region_zone_config` must be set first. This
only overrides which region the *lease* prefers; it does not touch
`constraints`/`voter_constraints`, so the SURVIVE REGION FAILURE replica
placement (2 in us-east-1 / 2 in us-east-2 / 1 in us-west-2, quorum-safe on a
3-of-5 loss) is unchanged -- only the leaseholder moves.

Verified live on this cluster (v26.2.0, 9-node docker-compose.multiregion.yml):
pinning `episodic_events` and `remediation_actions` this way converges within
roughly 30-150 seconds (small idle ranges can take longer to be picked up by
the replicate queue's scan than busy ones) -- hence the generous default
timeout below. If a table's ranges do NOT fully converge to the kill region
within the timeout, `pin_and_verify` raises rather than silently letting the
harness proceed to kill a region that owns none of the leaseholders it claims
to -- that would just reproduce the original bug under a different name.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from . import db
from .topology import DATABASE_NAME, TOPOLOGY, Node

DEFAULT_PIN_TIMEOUT_S = 180.0
DEFAULT_PIN_POLL_INTERVAL_S = 2.0

_REGION_RE = re.compile(r"region=([^,\"]+)")
_ZONE_RE = re.compile(r"zone=([^,\"]+)")


def _region_of(locality: str) -> str | None:
    m = _REGION_RE.search(locality or "")
    return m.group(1) if m else None


def _zone_of(locality: str) -> str | None:
    m = _ZONE_RE.search(locality or "")
    return m.group(1) if m else None


def resolve_node_from_locality(locality: str, topology: tuple[Node, ...] = TOPOLOGY) -> Node | None:
    """`locality` is a `region=...,zone=...` string as returned by
    `SHOW RANGES ... WITH DETAILS`'s `lease_holder_locality` column. Each
    (region, zone) pair uniquely identifies one node in this 3x3 topology."""
    region, zone = _region_of(locality), _zone_of(locality)
    if region is None or zone is None:
        return None
    for node in topology:
        if node.region == region and node.zone == zone:
            return node
    return None


def set_lease_preference(node: Node, *, table: str, region: str) -> None:
    """Issue the zone-config override on `table` pinning its lease
    preference to `region`. Table names are fixed identifiers this package
    controls internally (never user input), so f-string interpolation
    mirrors the existing convention in ranges.py."""
    conn = db.connect(node, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"USE {DATABASE_NAME}")  # noqa: S608 - fixed identifier
            cur.execute("SET override_multi_region_zone_config = true")
            cur.execute(
                f"ALTER TABLE {table} CONFIGURE ZONE USING "  # noqa: S608 - fixed identifier
                f"lease_preferences = '[[+region={region}]]'"
            )
    finally:
        conn.close()


@dataclass(frozen=True)
class LeaseholderVerification:
    table: str
    target_region: str
    verified: bool
    elapsed_seconds: float
    ranges_total: int
    ranges_in_region: int
    leaseholder_store_ids: tuple[int, ...]
    sample_leaseholder_locality: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "target_region": self.target_region,
            "verified": self.verified,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "ranges_total": self.ranges_total,
            "ranges_in_region": self.ranges_in_region,
            "leaseholder_store_ids": list(self.leaseholder_store_ids),
            "sample_leaseholder_locality": self.sample_leaseholder_locality,
        }


def _fetch_lease_rows(node: Node, table: str) -> list[tuple[int, int, str]]:
    conn = db.connect(node, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(f"USE {DATABASE_NAME}")  # noqa: S608 - fixed identifier
            cur.execute(
                "SELECT range_id, lease_holder, lease_holder_locality "
                f"FROM [SHOW RANGES FROM TABLE {table} WITH DETAILS]"  # noqa: S608
            )
            return [(row[0], row[1], row[2] or "") for row in cur.fetchall()]
    finally:
        conn.close()


def verify_leaseholders_in_region(
    node: Node, *, table: str, region: str,
    timeout_s: float = DEFAULT_PIN_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_PIN_POLL_INTERVAL_S,
) -> LeaseholderVerification:
    """Poll `SHOW RANGES FROM TABLE <table> WITH DETAILS` until every range's
    `lease_holder_locality` reports `region=<region>`, or `timeout_s`
    elapses. Returns the last observed state either way -- an unconverged
    result is reported honestly, not hidden."""
    start = time.time()
    rows: list[tuple[int, int, str]] = []
    while time.time() - start < timeout_s:
        rows = _fetch_lease_rows(node, table)
        total = len(rows)
        in_region = sum(1 for (_rid, _lh, loc) in rows if _region_of(loc) == region)
        if total > 0 and in_region == total:
            store_ids = tuple(sorted({lh for (_rid, lh, _loc) in rows}))
            return LeaseholderVerification(
                table=table, target_region=region, verified=True,
                elapsed_seconds=time.time() - start, ranges_total=total,
                ranges_in_region=in_region, leaseholder_store_ids=store_ids,
                sample_leaseholder_locality=rows[0][2] if rows else None,
            )
        time.sleep(poll_interval_s)

    total = len(rows)
    in_region = sum(1 for (_rid, _lh, loc) in rows if _region_of(loc) == region)
    store_ids = tuple(sorted({lh for (_rid, lh, _loc) in rows}))
    return LeaseholderVerification(
        table=table, target_region=region, verified=False,
        elapsed_seconds=time.time() - start, ranges_total=total,
        ranges_in_region=in_region, leaseholder_store_ids=store_ids,
        sample_leaseholder_locality=rows[0][2] if rows else None,
    )


class LeaseholderPinError(RuntimeError):
    """Raised when the probed tables' leaseholders cannot be verified inside
    the kill region before the harness proceeds to kill it. Charter R5: a
    resilience proof that can't confirm the failure it's about to exercise
    is real must fail loudly, not silently degrade into re-measuring a
    normal write to a surviving node (the original bug)."""


def pin_and_verify(
    node: Node, *, tables: tuple[str, ...], region: str,
    timeout_s: float = DEFAULT_PIN_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_PIN_POLL_INTERVAL_S,
) -> dict[str, Any]:
    """Pin every table in `tables` to `region` and verify convergence via
    SHOW RANGES. Raises `LeaseholderPinError` (naming exactly which tables
    failed to converge and their last-observed state) if any table doesn't
    fully converge within `timeout_s` -- callers must not proceed to kill
    the region on an unverified pin."""
    for table in tables:
        set_lease_preference(node, table=table, region=region)

    results: dict[str, LeaseholderVerification] = {}
    for table in tables:
        results[table] = verify_leaseholders_in_region(
            node, table=table, region=region,
            timeout_s=timeout_s, poll_interval_s=poll_interval_s,
        )

    unverified = [t for t, v in results.items() if not v.verified]
    out = {t: v.to_dict() for t, v in results.items()}
    if unverified:
        raise LeaseholderPinError(
            f"leaseholder pin did not converge on {region!r} for table(s) "
            f"{unverified!r} within the timeout -- refusing to proceed to a "
            f"region-kill that would not exercise a real failover. "
            f"Last observed state: {out}"
        )
    return out
