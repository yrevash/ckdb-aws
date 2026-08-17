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
    parser.add_argument(
        "--decision-quality",
        help=(
            "Path to a measured real-agent report from "
            "`python -m postmortem_eval.real_agent`. Omit to publish decision "
            "quality as pending_real_agent_run."
        ),
    )
    args = parser.parse_args()

    harness = EvaluationHarness(decision_quality_path=args.decision_quality)
    report = harness.write_json(args.output) if args.output else harness.run()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
