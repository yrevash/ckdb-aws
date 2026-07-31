"""HTTP status mapping for infra/upstream failures vs genuine conflicts
(audit backend#5): RecallError/ReasoningError/AtomicRemediationError/
OutcomeRecordingError must surface as 502/503, never the misleading 409 a
caller would read as "your request conflicted," while OutcomeConflict and
provenance/approval rejections stay 409 (they are genuine conflicts).
"""

from __future__ import annotations

from uuid import UUID

from fastapi.testclient import TestClient

from postmortem_backend.adapters.fakes import (
    FakeAtomicRemediationStore,
    FakeEmbeddingAdapter,
    FakeReasoningAdapter,
    FakeRecallAdapter,
)
from postmortem_backend.api import create_app
from postmortem_backend.config import Settings
from postmortem_backend.domain import MemoryCandidate, MemoryKind, RecallBundle
from postmortem_backend.errors import ReasoningError, RecallError
from postmortem_backend.events import EventBroker
from postmortem_backend.runtime import Runtime
from postmortem_backend.service import OutcomeService, ResponderService


ORG_ID = UUID("70000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("70000000-0000-0000-0000-000000000002")
INCIDENT_ID = UUID("70000000-0000-0000-0000-000000000003")
SESSION_ID = UUID("70000000-0000-0000-0000-000000000004")
SERVICE_ID = UUID("70000000-0000-0000-0000-000000000005")
RUNBOOK_ID = UUID("70000000-0000-0000-0000-000000000006")


def _settings() -> Settings:
    return Settings(
        runtime_mode="fake",
        host="127.0.0.1",
        port=8080,
        log_level="INFO",
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        aws_region="us-east-1",
        reasoning_model_id="offline",
        embedding_model_id="offline",
        reasoner="bedrock",
        cors_origins=("http://localhost:3000",),
        database_url=None,
        mcp_url=None,
        mcp_token=None,
    )


def _respond_body() -> dict[str, object]:
    return {
        "session_id": str(SESSION_ID),
        "service_id": str(SERVICE_ID),
        "severity": "SEV-1",
        "summary": "Checkout 5xx rose after the canary deploy",
        "error_signature": "HTTP_5XX_POST_DEPLOY",
        "service_tags": ["checkout"],
        # "checkout" is a server-classified critical-tier service (audit
        # C2); these tests are about error-code mapping, not the
        # destructive gate, so they supply the approval it now requires to
        # reach the remediation/outcome stages under test.
        "approved": True,
        "approved_by": "sre@granthvani.com",
    }


def _runbook_bundle() -> RecallBundle:
    return RecallBundle(
        runbooks=(
            MemoryCandidate(
                memory_id=RUNBOOK_ID,
                kind=MemoryKind.PROCEDURAL,
                content="Rollback the checkout canary.",
                similarity=0.94,
                success_rate=0.9,
                runbook_id=RUNBOOK_ID,
                metadata={
                    "action": "rollback",
                    "target_version": "1.4.2",
                    "requires_human_approval": False,
                },
            ),
        )
    )


class _RaisingRecall:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def recall(self, _query: object) -> RecallBundle:
        raise self._exc


class _RaisingReasoner:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def decide(self, _signal: object, _memory: object) -> object:
        raise self._exc


def _client(*, recall=None, reasoner=None, remediation=None) -> TestClient:
    events = EventBroker()
    store = remediation or FakeAtomicRemediationStore(auto_seed=True)
    responder = ResponderService(
        embedder=FakeEmbeddingAdapter(),
        recall=recall or FakeRecallAdapter(_runbook_bundle()),
        reasoner=reasoner or FakeReasoningAdapter(),
        remediation=store,
        events=events,
    )
    outcomes = OutcomeService(
        embedder=FakeEmbeddingAdapter(), outcomes=store, events=events
    )
    runtime = Runtime(
        settings=_settings(), responder=responder, outcomes=outcomes, events=events
    )
    return TestClient(create_app(settings=_settings(), runtime=runtime))


def test_recall_error_maps_to_503_not_409() -> None:
    with _client(recall=_RaisingRecall(RecallError("cluster unreachable"))) as client:
        response = client.post(
            f"/v1/incidents/{INCIDENT_ID}/respond", json=_respond_body()
        )
    assert response.status_code == 503
    assert response.json()["error"] == "RecallError"


def test_reasoning_error_maps_to_502_not_409() -> None:
    with _client(
        recall=FakeRecallAdapter(),
        reasoner=_RaisingReasoner(ReasoningError("malformed model output")),
    ) as client:
        response = client.post(
            f"/v1/incidents/{INCIDENT_ID}/respond", json=_respond_body()
        )
    assert response.status_code == 502
    assert response.json()["error"] == "ReasoningError"


def test_atomic_remediation_error_maps_to_503_not_409() -> None:
    store = FakeAtomicRemediationStore(auto_seed=True)
    store.fail_at = "during_memory_write"  # raises AtomicRemediationError
    with _client(remediation=store) as client:
        response = client.post(
            f"/v1/incidents/{INCIDENT_ID}/respond", json=_respond_body()
        )
    assert response.status_code == 503
    assert response.json()["error"] == "AtomicRemediationError"


def test_outcome_recording_error_maps_to_503_not_409() -> None:
    store = FakeAtomicRemediationStore(auto_seed=True)
    with _client(remediation=store) as client:
        respond = client.post(
            f"/v1/incidents/{INCIDENT_ID}/respond", json=_respond_body()
        )
        action_id = respond.json()["remediation"]["action_id"]
        store.fail_at = "outcome_after_action_update"  # raises OutcomeRecordingError
        outcome = client.post(
            f"/v1/incidents/{INCIDENT_ID}/outcomes",
            json={
                "action_id": action_id,
                "service_id": str(SERVICE_ID),
                "outcome": "success",
                "summary": "Recovered.",
            },
        )
    assert outcome.status_code == 503
    assert outcome.json()["error"] == "OutcomeRecordingError"


def test_outcome_conflict_stays_409_a_genuine_conflict() -> None:
    store = FakeAtomicRemediationStore(auto_seed=True)
    with _client(remediation=store) as client:
        respond = client.post(
            f"/v1/incidents/{INCIDENT_ID}/respond", json=_respond_body()
        )
        action_id = respond.json()["remediation"]["action_id"]
        first = client.post(
            f"/v1/incidents/{INCIDENT_ID}/outcomes",
            json={
                "action_id": action_id,
                "service_id": str(SERVICE_ID),
                "outcome": "success",
                "summary": "Recovered.",
            },
        )
        assert first.status_code == 200
        conflicting = client.post(
            f"/v1/incidents/{INCIDENT_ID}/outcomes",
            json={
                "action_id": action_id,
                "service_id": str(SERVICE_ID),
                "outcome": "failed",
                "summary": "Actually it did not recover.",
            },
        )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"] == "OutcomeConflict"
