from __future__ import annotations

import hashlib
import json

from .conductor import Conductor


def main() -> None:
    conductor = Conductor.from_files()
    injected: list[dict[str, str]] = []
    while conductor.remaining_scenarios:
        incident = conductor.inject_next()
        injected.append(
            {
                "incident_id": incident.incident_id,
                "family_id": incident.family_id,
                "variant_id": incident.variant_id,
                "status": str(incident.status),
            }
        )

    snapshot = json.dumps(
        conductor.state.snapshot(), sort_keys=True, separators=(",", ":")
    ).encode()
    print(
        json.dumps(
            {
                "seed": conductor.seed,
                "incidents_injected": injected,
                "snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
