"""Thin psycopg connection helpers for the resilience harness.

Kept deliberately small: the harness needs short, controllable connect
timeouts (so probing a just-killed node fails fast instead of hanging) and a
uniform way to round-trip wall-clock timing around a statement. It does not
attempt to be a general-purpose driver layer -- backend/ owns that for the
application.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .topology import Node

DEFAULT_CONNECT_TIMEOUT_S = 2.0


def connect(node: Node, *, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_S,
            autocommit: bool = False):
    """Open a fresh psycopg connection to `node`. Raises on failure -- callers
    that expect a node might be dead (RTO retry loop) must catch."""
    import psycopg  # local import: keep psycopg optional for pure-unit tests

    conn = psycopg.connect(
        node.dsn(), connect_timeout=connect_timeout, autocommit=autocommit
    )
    return conn


@dataclass(frozen=True)
class TimedResult:
    value: Any
    elapsed_ms: float


def timed(fn, *args, **kwargs) -> TimedResult:
    start = time.perf_counter()
    value = fn(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return TimedResult(value=value, elapsed_ms=elapsed_ms)


def can_connect(node: Node, *, timeout: float = 1.5) -> bool:
    """True if `node` currently accepts a SQL connection. Used both by
    resilience/tests/_live.py (to skip live-cluster tests when the compose
    cluster isn't up) and by scripts/measure_resilience.sh's preflight
    check."""
    try:
        conn = connect(node, connect_timeout=timeout)
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        conn.close()


def try_nodes_in_order(nodes: Iterable[Node], attempt, *, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT_S):
    """Try `attempt(node)` against each node in order, returning the first
    success as (node, result). Swallows connection/statement errors from dead
    nodes and moves on; re-raises only if every node fails.

    `attempt(node)` is expected to open its own connection (via `connect`)
    and close it -- this helper does not own connection lifetime, only the
    node-selection/retry policy.
    """
    last_exc: Exception | None = None
    for node in nodes:
        try:
            return node, attempt(node)
        except Exception as exc:  # noqa: BLE001 - deliberately broad: any
            # failure mode against a dead/unreachable node (connection
            # refused, timeout, "node is decommissioning", etc.) should just
            # advance to the next candidate node.
            last_exc = exc
            continue
    assert last_exc is not None
    raise last_exc
