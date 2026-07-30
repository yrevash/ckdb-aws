from uuid import UUID

from fastapi.testclient import TestClient

from postmortem_backend.api import create_app


INCIDENT_ID = UUID("10000000-0000-0000-0000-000000000003")
SESSION_ID = UUID("10000000-0000-0000-0000-000000000004")
SERVICE_ID = UUID("10000000-0000-0000-0000-000000000005")


def test_fake_api_runs_the_seeded_vertical_slice() -> None:
    with TestClient(create_app()) as client:
        health = client.get("/healthz")
        preflight = client.options(
            f"/v1/incidents/{INCIDENT_ID}/events",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        response = client.post(
            f"/v1/incidents/{INCIDENT_ID}/respond",
            json={
                "session_id": str(SESSION_ID),
                "service_id": str(SERVICE_ID),
                "severity": "SEV-1",
                "summary": "Checkout 5xx rose after the canary deploy",
                "error_signature": "HTTP_5XX_POST_DEPLOY",
                "service_tags": ["checkout", "critical-path"],
            },
        )
        detail = client.get(f"/v1/incidents/{INCIDENT_ID}")

    assert health.status_code == 200
    assert health.json()["runtimeMode"] == "fake"
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert response.status_code == 200
    assert response.json()["decision"]["kind"] == "remediate_and_record"
    assert response.json()["remediation"]["event_id"]
    assert detail.status_code == 200
    assert detail.json()["event_count"] == 9


def test_fake_api_records_idempotent_consolidation_ready_outcome() -> None:
    with TestClient(create_app()) as client:
        response = client.post(
            f"/v1/incidents/{INCIDENT_ID}/respond",
            json={
                "session_id": str(SESSION_ID),
                "service_id": str(SERVICE_ID),
                "severity": "SEV-1",
                "summary": "Checkout 5xx rose after the canary deploy",
                "error_signature": "HTTP_5XX_POST_DEPLOY",
                "service_tags": ["checkout", "critical-path"],
            },
        )
        action_id = response.json()["remediation"]["action_id"]
        payload = {
            "action_id": action_id,
            "service_id": str(SERVICE_ID),
            "outcome": "success",
            "summary": "Checkout error rate returned to baseline.",
            "error_signature": "HTTP_5XX_POST_DEPLOY",
        }
        outcome = client.post(
            f"/v1/incidents/{INCIDENT_ID}/outcomes",
            json=payload,
        )
        replay = client.post(
            f"/v1/incidents/{INCIDENT_ID}/outcomes",
            json=payload,
        )

    assert outcome.status_code == 200
    assert outcome.json()["outcome"] == "success"
    assert outcome.json()["incident_status"] == "resolved"
    assert outcome.json()["idempotent_replay"] is False
    assert replay.status_code == 200
    assert replay.json()["event_id"] == outcome.json()["event_id"]
    assert replay.json()["idempotent_replay"] is True
