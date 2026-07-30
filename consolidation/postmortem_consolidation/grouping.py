from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .contracts import ChangeEvent, EpisodeGroup, RunbookOutcome


def _outcome_for(events: list[ChangeEvent]) -> RunbookOutcome | None:
    outcomes = [event for event in events if event.event_type == "outcome"]
    if not outcomes:
        return None

    value = str(outcomes[-1].metadata.get("outcome", "")).lower()
    if value in {"success", "resolved"}:
        return "success"
    if value in {"failed", "failure"}:
        return "failed"
    if value in {"no_effect", "no-effect"}:
        return "no_effect"
    return None


def group_completed_incidents(events: Iterable[ChangeEvent]) -> list[EpisodeGroup]:
    """Group stable episode histories; incomplete incidents remain buffered."""

    grouped: dict[tuple[str, str], list[ChangeEvent]] = defaultdict(list)
    for event in events:
        grouped[(event.org_id, event.incident_id)].append(event)

    result: list[EpisodeGroup] = []
    for (_, _), incident_events in sorted(grouped.items()):
        ordered = sorted(
            incident_events, key=lambda event: (event.occurred_at, event.event_id)
        )
        outcome = _outcome_for(ordered)
        if outcome is None:
            continue
        first = ordered[0]
        result.append(
            EpisodeGroup(
                org_id=first.org_id,
                agent_id=first.agent_id,
                incident_id=first.incident_id,
                service_id=first.service_id,
                episodes=tuple(ordered),
                outcome=outcome,
            )
        )
    return result
