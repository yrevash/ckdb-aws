"""CLI entry point: `python -m postmortem_resilience --output <path>`.

Runs the full resilience harness (seed -> baseline probes -> region kill ->
RTO measurement -> restore -> RPO verification) exactly once against
whatever multi-region cluster `postmortem_resilience.topology` points at
(defaults to docker-compose.multiregion.yml's localhost port mapping), and
writes the resulting report as JSON. Always also prints the report to stdout.

The cluster must already be up, migrated, and multi-region-bootstrapped
before this runs -- see scripts/failover_demo.sh / scripts/measure_
resilience.sh, which do that and then invoke this module.
"""

from __future__ import annotations

import argparse
import json
import sys

from .harness import run_and_write


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Phase 3 Track A resilience/failover proof harness"
    )
    parser.add_argument(
        "--output",
        default="evaluation/reports/phase3-resilience.json",
        help="JSON report output path (default: evaluation/reports/phase3-resilience.json)",
    )
    args = parser.parse_args()

    report = run_and_write(args.output)
    print(json.dumps(report, indent=2, sort_keys=True))

    return 0 if report["overall"]["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
