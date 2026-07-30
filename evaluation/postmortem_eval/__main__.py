from __future__ import annotations

import argparse
import json

from .runner import EvaluationHarness


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run deterministic Postmortem with-memory vs cold A/B"
    )
    parser.add_argument(
        "--output",
        help="Optional JSON report path; stdout is always emitted",
    )
    args = parser.parse_args()

    harness = EvaluationHarness()
    report = harness.write_json(args.output) if args.output else harness.run()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
