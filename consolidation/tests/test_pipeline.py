from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from postmortem_consolidation.contracts import ChangeEvent, ClosedWindow
from postmortem_consolidation.embedding import DeterministicEmbeddingModel
from postmortem_consolidation.model import DeterministicConsolidationModel
from postmortem_consolidation.pipeline import ConsolidationProcessor
from postmortem_consolidation.repository import InMemoryRunbookRepository
from postmortem_consolidation.storage import InMemoryWindowStore

NOW = datetime(2026, 7, 30, 3, 0, tzinfo=timezone.utc)


def change_time(minutes: int) -> Decimal:
    value = NOW + timedelta(minutes=minutes)
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    elapsed = value - epoch
    return Decimal(
        elapsed.days * 86_400_000_000_000
        + elapsed.seconds * 1_000_000_000
        + elapsed.microseconds * 1_000
    )


def episode(
    event_id: str,
    event_type: str,
    *,
    incident_id: str = "incident-1",
    outcome: str | None = None,
) -> ChangeEvent:
    metadata: dict[str, object] = {
        "service_name": "checkout-api",
        "family_id": "bad-deploy",
        "error_signature": "p99-after-canary",
    }
    if event_type == "action":
        metadata.update(
            {"action_type": "rollback_deploy", "params": {"to": "#5119"}}
        )
    if outcome:
        metadata["outcome"] = outcome
    return ChangeEvent(
        event_id=event_id,
        org_id="org-1",
        agent_id="agent-1",
        incident_id=incident_id,
        service_id="service-1",
        event_type=event_type,
        content=f"{event_type} content",
        occurred_at=NOW + timedelta(seconds=len(event_id)),
        updated_at=change_time(1),
        metadata=metadata,
    )


def processor() -> tuple[
    ConsolidationProcessor, InMemoryWindowStore, InMemoryRunbookRepository
]:
    store = InMemoryWindowStore()
    repository = InMemoryRunbookRepository()
    return (
        ConsolidationProcessor(
            store=store,
            model=DeterministicConsolidationModel(),
            embedder=DeterministicEmbeddingModel(),
            repository=repository,
        ),
        store,
        repository,
    )


def add_incident(
    pipeline: ConsolidationProcessor,
    prefix: str,
    *,
    outcome: str,
    incident_id: str,
) -> None:
    for value in (
        episode(f"{prefix}-alert", "alert", incident_id=incident_id),
        episode(f"{prefix}-action", "action", incident_id=incident_id),
        episode(
            f"{prefix}-outcome",
            "outcome",
            incident_id=incident_id,
            outcome=outcome,
        ),
    ):
        pipeline.process(value)


def test_closed_window_creates_and_archives_a_runbook() -> None:
    pipeline, store, repository = processor()
    add_incident(pipeline, "a", outcome="success", incident_id="incident-1")

    result = pipeline.process(ClosedWindow(change_time(2)))

    assert result.completed_groups == 1
    assert result.mutations[0].operation == "create"
    assert result.mutations[0].status == "draft"
    assert len(store.archives) == 1
    assert len(repository.runbooks) == 1
    assert store.pending == {}


def test_recurrence_reinforces_and_counterexample_weakens() -> None:
    pipeline, _, repository = processor()
    add_incident(pipeline, "a", outcome="success", incident_id="incident-1")
    first = pipeline.process(ClosedWindow(change_time(2)))

    add_incident(pipeline, "b", outcome="success", incident_id="incident-2")
    second = pipeline.process(ClosedWindow(change_time(3)))

    add_incident(pipeline, "c", outcome="success", incident_id="incident-3")
    third = pipeline.process(ClosedWindow(change_time(4)))

    add_incident(pipeline, "d", outcome="failed", incident_id="incident-4")
    fourth = pipeline.process(ClosedWindow(change_time(5)))

    assert [
        first.mutations[0].operation,
        second.mutations[0].operation,
        third.mutations[0].operation,
    ] == [
        "create",
        "reinforce",
        "reinforce",
    ]
    assert first.mutations[0].status == "draft"
    assert second.mutations[0].status == "draft"
    assert third.mutations[0].status == "active"
    assert fourth.mutations[0].operation == "weaken"
    assert fourth.mutations[0].status == "active"
    latest = next(iter(repository.runbooks.values()))[-1]
    assert (latest.success_count, latest.failure_count) == (3, 1)
    assert len(latest.embedding) == 1024


def test_replayed_window_is_idempotent() -> None:
    pipeline, store, repository = processor()
    events = [
        episode("a-alert", "alert"),
        episode("a-action", "action"),
        episode("a-outcome", "outcome", outcome="success"),
    ]
    for value in events:
        pipeline.process(value)
    first = pipeline.process(ClosedWindow(change_time(2)))

    # Simulate at-least-once redelivery of the same rows and watermark.
    for value in events:
        store.put(value)
    replay = pipeline.process(ClosedWindow(change_time(2)))

    assert first.mutations[0].operation == "create"
    assert replay.completed_groups == 0
    assert replay.mutations == ()
    latest = next(iter(repository.runbooks.values()))[-1]
    assert latest.success_count == 1

    # Repository-level replay protection remains a second durable line of defense.
    key, archived = next(iter(store.archives.items()))
    duplicate_write = repository.apply(archived.candidate, key)
    assert duplicate_write.operation == "noop"
    assert duplicate_write.idempotent_replay is True


def test_counterexample_can_deprecate_an_immature_draft() -> None:
    pipeline, _, repository = processor()
    add_incident(pipeline, "a", outcome="success", incident_id="incident-1")
    pipeline.process(ClosedWindow(change_time(2)))

    add_incident(pipeline, "b", outcome="failed", incident_id="incident-2")
    weakened = pipeline.process(ClosedWindow(change_time(3)))

    assert weakened.mutations[0].operation == "weaken"
    assert weakened.mutations[0].status == "deprecated"
    latest = next(iter(repository.runbooks.values()))[-1]
    assert (latest.success_count, latest.failure_count) == (1, 1)


def test_incomplete_incident_stays_buffered() -> None:
    pipeline, store, _ = processor()
    pipeline.process(episode("a-alert", "alert"))

    result = pipeline.process(ClosedWindow(change_time(2)))

    assert result.completed_groups == 0
    assert len(store.pending) == 1
