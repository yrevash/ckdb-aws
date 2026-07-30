from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest

from postmortem_backend.adapters.fakes import (
    FakeAtomicRemediationStore,
    FakeEmbeddingAdapter,
)
from postmortem_backend.adapters.outcome import CockroachOutcomeStore
from postmortem_backend.domain import (
    ActionKind,
    EventType,
    OutcomeCommand,
    OutcomeKind,
    RemediationCommand,
)
from postmortem_backend.errors import (
    OutcomeConflict,
    OutcomeRecordingError,
    ProvenanceError,
)
from postmortem_backend.events import EventBroker
from postmortem_backend.service import OutcomeService
from postmortem_backend.transport import console_event


ORG_ID = UUID("50000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("50000000-0000-0000-0000-000000000002")
INCIDENT_ID = UUID("50000000-0000-0000-0000-000000000003")
SESSION_ID = UUID("50000000-0000-0000-0000-000000000004")
SERVICE_ID = UUID("50000000-0000-0000-0000-000000000005")
RUNBOOK_ID = UUID("50000000-0000-0000-0000-000000000006")
OBSERVED_AT = datetime(2026, 7, 30, 12, tzinfo=UTC)


def seeded_store() -> tuple[FakeAtomicRemediationStore, UUID]:
    store = FakeAtomicRemediationStore()
    store.seed(
        service_id=SERVICE_ID,
        incident_id=INCIDENT_ID,
        cited_memory_id=RUNBOOK_ID,
        org_id=ORG_ID,
    )
    remediation = store.remediate_and_record(
        RemediationCommand(
            org_id=ORG_ID,
            agent_id=AGENT_ID,
            incident_id=INCIDENT_ID,
            session_id=SESSION_ID,
            service_id=SERVICE_ID,
            action=ActionKind.ROLLBACK,
            target_version="1.4.2",
            cited_memory_id=RUNBOOK_ID,
            runbook_id=RUNBOOK_ID,
            rationale="Prior successful runbook matched.",
        ),
        FakeEmbeddingAdapter().embed("rollback checkout"),
    )
    return store, remediation.action_id


def outcome_command(
    action_id: UUID,
    outcome: OutcomeKind = OutcomeKind.SUCCESS,
    **changes: object,
) -> OutcomeCommand:
    command = OutcomeCommand(
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        incident_id=INCIDENT_ID,
        service_id=SERVICE_ID,
        action_id=action_id,
        outcome=outcome,
        summary="Checkout error rate returned to baseline.",
        error_signature="HTTP_5XX_POST_DEPLOY",
        observed_at=OBSERVED_AT,
    )
    from dataclasses import replace

    return replace(command, **changes)


def test_success_outcome_resolves_incident_and_appends_consolidation_episode() -> None:
    store, action_id = seeded_store()

    result = store.record_outcome(
        outcome_command(action_id),
        FakeEmbeddingAdapter().embed("rollback succeeded"),
    )

    assert result.outcome is OutcomeKind.SUCCESS
    assert result.incident_status == "resolved"
    assert store.incidents[INCIDENT_ID]["status"] == "resolved"
    assert store.remediation_actions[action_id]["outcome"] == "success"
    event = store.episodes[result.event_id]
    assert event["event_type"] == "outcome"
    assert event["metadata"] == {
        "outcome": "success",
        "action_id": str(action_id),
        "transaction_id": str(result.transaction_id),
        "action_type": "rollback_deploy",
        "params": {"target_version": "1.4.2"},
        "service_id": str(SERVICE_ID),
        "error_signature": "HTTP_5XX_POST_DEPLOY",
        "consolidation_ready": True,
        "source": "responder_outcome",
    }
    assert len(event["embedding"]) == 1024


def test_failed_outcome_does_not_resolve_incident() -> None:
    store, action_id = seeded_store()

    result = store.record_outcome(
        outcome_command(action_id, OutcomeKind.FAILED),
        FakeEmbeddingAdapter().embed("rollback failed"),
    )

    assert result.incident_status == "mitigating"
    assert store.incidents[INCIDENT_ID]["status"] == "mitigating"
    assert store.remediation_actions[action_id]["outcome"] == "failed"


def test_outcome_replay_is_idempotent_and_conflicting_replay_is_rejected() -> None:
    store, action_id = seeded_store()
    embedding = FakeEmbeddingAdapter().embed("verified outcome")

    first = store.record_outcome(outcome_command(action_id), embedding)
    replay = store.record_outcome(outcome_command(action_id), embedding)

    assert replay.event_id == first.event_id
    assert replay.idempotent_replay is True
    assert sum(
        event["event_type"] == "outcome" for event in store.episodes.values()
    ) == 1
    with pytest.raises(OutcomeConflict):
        store.record_outcome(
            outcome_command(action_id, OutcomeKind.NO_EFFECT),
            embedding,
        )


def test_outcome_provenance_gate_checks_org_incident_service_and_action() -> None:
    store, action_id = seeded_store()
    before = store.snapshot()

    with pytest.raises(ProvenanceError):
        store.record_outcome(
            outcome_command(
                action_id,
                service_id=UUID("ffffffff-0000-0000-0000-000000000001"),
            ),
            FakeEmbeddingAdapter().embed("invalid provenance"),
        )

    assert store.snapshot() == before


@pytest.mark.parametrize(
    "failure_point",
    ["outcome_after_action_update", "outcome_during_event_write"],
)
def test_outcome_transaction_rolls_back_every_partial_write(
    failure_point: str,
) -> None:
    store, action_id = seeded_store()
    before = store.snapshot()
    store.fail_at = failure_point

    with pytest.raises(OutcomeRecordingError):
        store.record_outcome(
            outcome_command(action_id, OutcomeKind.FAILED),
            FakeEmbeddingAdapter().embed("injected failure"),
        )

    assert store.snapshot() == before
    assert store.outcome_commit_count == 0
    assert store.outcome_rollback_count == 1


def test_outcome_service_publishes_frontend_compatible_record_event() -> None:
    store, action_id = seeded_store()
    events = EventBroker()
    service = OutcomeService(
        embedder=FakeEmbeddingAdapter(),
        outcomes=store,
        events=events,
    )

    result = service.record(outcome_command(action_id))
    replay = service.record(outcome_command(action_id))

    event = events.history(INCIDENT_ID)[-1]
    assert replay.idempotent_replay is True
    assert len(events.history(INCIDENT_ID)) == 1
    assert event.type is EventType.OUTCOME_RECORDED
    envelope = console_event(event, sequence=1)
    assert envelope["type"] == "record"
    assert envelope["payload"]["memoryId"] == str(result.event_id)
    assert envelope["payload"]["memoryKind"] == "episodic"
    assert envelope["payload"]["staleReadsObserved"] == 0


class StubCursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row
        self.executions: list[tuple[str, dict[str, object] | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, query: str, params=None) -> None:
        self.executions.append((query, params))

    def fetchone(self):
        return self.row


class StubConnection:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.stub_cursor = StubCursor(row)
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.stub_cursor

    @contextmanager
    def transaction(self):
        try:
            yield
        except Exception:
            self.rollbacks += 1
            raise
        else:
            self.commits += 1


class StubProvider:
    def __init__(self, connection: StubConnection) -> None:
        self.connection = connection

    @contextmanager
    def __call__(self):
        yield self.connection


def test_cockroach_outcome_adapter_returns_event_and_enforces_three_way_gate() -> None:
    action_id = UUID("50000000-0000-0000-0000-000000000020")
    event_id = UUID("50000000-0000-0000-0000-000000000021")
    transaction_id = UUID("50000000-0000-0000-0000-000000000022")
    connection = StubConnection(
        (
            event_id,
            OBSERVED_AT,
            transaction_id,
            "resolved",
            "success",
            False,
        )
    )
    adapter = CockroachOutcomeStore(StubProvider(connection))

    result = adapter.record_outcome(
        outcome_command(action_id),
        FakeEmbeddingAdapter().embed("success outcome"),
    )

    assert connection.commits == 1
    assert result.action_id == action_id
    assert result.event_id == event_id
    assert result.transaction_id == transaction_id
    sql, params = connection.stub_cursor.executions[1]
    assert "UPDATE remediation_actions" in sql
    assert "UPDATE incidents" in sql
    assert "event_type" in sql
    assert "'outcome'" in sql
    assert "action.incident_id = %(incident_id)s" in sql
    assert "action.target_id = %(service_id)s" in sql
    assert params is not None
    assert params["action_id"] == action_id


def test_cockroach_outcome_adapter_rolls_back_missing_provenance() -> None:
    connection = StubConnection(None)
    adapter = CockroachOutcomeStore(StubProvider(connection))

    with pytest.raises(ProvenanceError):
        adapter.record_outcome(
            outcome_command(UUID("50000000-0000-0000-0000-000000000030")),
            FakeEmbeddingAdapter().embed("missing"),
        )

    assert connection.commits == 0
    assert connection.rollbacks == 1
