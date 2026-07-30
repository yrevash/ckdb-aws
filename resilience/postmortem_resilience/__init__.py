"""Phase 3, Track A — resilience & failover measurement harness.

Proves, with real machine-readable telemetry against the local simulated
9-node / 3-region CockroachDB cluster (docker-compose.multiregion.yml), the
three wedge properties from research/postmortem/00-charter.md:

  * RPO = 0   -- zero committed rows lost across a region kill.
  * RTO < 10s -- wall-clock from region kill to restored write availability.
  * Single-store atomicity, read-your-own-writes freshness, and cross-agent
    (cross-node) visibility with zero lag.

Entry point: `python -m postmortem_resilience` (see __main__.py), or import
`ResilienceHarness` from `postmortem_resilience.harness` directly.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
