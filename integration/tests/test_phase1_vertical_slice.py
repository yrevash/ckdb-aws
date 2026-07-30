"""Phase 1 exit gate: simulator alert through responder and console contract."""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from postmortem_backend.config import Settings
from postmortem_backend.domain import IncidentSignal
from postmortem_backend.runtime import build_runtime
from postmortem_backend.transport import console_event
from postmortem_sim.conductor import Conductor


def fake_settings() -> Settings:
    return Settings(
        runtime_mode="fake",
        host="127.0.0.1",
        port=8080,
        log_level="INFO",
        org_id=UUID("00000000-0000-0000-0000-000000000001"),
        agent_id=UUID("00000000-0000-0000-0000-000000000002"),
        aws_region="us-east-1",
        reasoning_model_id="offline",
        embedding_model_id="offline",
        reasoner="bedrock",
        cors_origins=("http://localhost:3000",),
        database_url=None,
        mcp_url=None,
        mcp_token=None,
    )


def test_seeded_bad_deploy_is_recalled_remediated_recorded_and_renderable() -> None:
    conductor = Conductor.from_files()
    incident = conductor.inject_next()
    service = conductor.state.services[incident.service]
    runtime = build_runtime(fake_settings())

    result = runtime.responder.handle(
        IncidentSignal(
            incident_id=UUID(incident.incident_id),
            session_id=uuid5(NAMESPACE_URL, f"session:{incident.incident_id}"),
            org_id=runtime.settings.org_id,
            agent_id=runtime.settings.agent_id,
            service_id=UUID(service.service_id),
            severity=incident.severity,
            summary=incident.title,
            error_signature="HTTP_5XX_POST_DEPLOY",
            service_tags=(incident.service, service.tier),
        )
    )

    assert result.remediation is not None
    assert result.decision.command is not None
    simulation_result = conductor.apply_action(
        incident.incident_id,
        "rollback_deploy",
        incident.service,
        {"target_version": result.decision.command.target_version},
        memory_ref=str(result.remediation.event_id),
    )
    assert simulation_result.resolved is True

    envelopes = [
        console_event(event, sequence=index)
        for index, event in enumerate(
            runtime.events.history(UUID(incident.incident_id)),
            start=1,
        )
    ]
    rendered_types = {event["type"] for event in envelopes}
    assert {"incident", "recall", "reason", "act", "transaction", "record"} <= rendered_types
    committed = next(
        event
        for event in envelopes
        if event["type"] == "transaction"
        and event["payload"]["state"] == "committed"
    )
    assert committed["payload"]["transactionId"] == str(
        result.remediation.transaction_id
    )
