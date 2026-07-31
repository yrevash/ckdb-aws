"""Bedrock reasoner adapter: guarded parsing of untrusted model output
(audit backend#4) and client timeout configuration (audit backend#8).
"""

from __future__ import annotations

import json
import unittest
from uuid import UUID

from postmortem_backend.adapters.bedrock import (
    _BEDROCK_CLIENT_CONFIG,
    BedrockReasoningAdapter,
)
from postmortem_backend.domain import (
    IncidentSignal,
    MemoryCandidate,
    MemoryKind,
    RecallBundle,
)
from postmortem_backend.errors import ReasoningError


ORG_ID = UUID("60000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("60000000-0000-0000-0000-000000000002")
INCIDENT_ID = UUID("60000000-0000-0000-0000-000000000003")
SESSION_ID = UUID("60000000-0000-0000-0000-000000000004")
SERVICE_ID = UUID("60000000-0000-0000-0000-000000000005")
MEMORY_ID = UUID("60000000-0000-0000-0000-000000000006")


def signal() -> IncidentSignal:
    return IncidentSignal(
        incident_id=INCIDENT_ID,
        session_id=SESSION_ID,
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        service_id=SERVICE_ID,
        severity="SEV-1",
        summary="Checkout 5xx rose after the canary deploy",
    )


def memory() -> RecallBundle:
    runbook = MemoryCandidate(
        memory_id=MEMORY_ID,
        kind=MemoryKind.PROCEDURAL,
        content="Rollback the checkout canary.",
        similarity=0.9,
        success_rate=0.9,
        runbook_id=MEMORY_ID,
        metadata={"actionable": True},
    )
    return RecallBundle(runbooks=(runbook,))


class StubBedrockClient:
    """Returns a fixed Converse response text, capturing no real AWS calls."""

    def __init__(self, text: str) -> None:
        self._text = text

    def converse(self, **_: object) -> dict[str, object]:
        return {"output": {"message": {"content": [{"text": self._text}]}}}


def _decide(payload: dict[str, object]) -> object:
    client = StubBedrockClient(json.dumps(payload))
    adapter = BedrockReasoningAdapter(
        region="us-east-1", model_id="offline-test", client=client
    )
    return adapter.decide(signal(), memory())


class GuardedParsingTests(unittest.TestCase):
    """backend#4: malformed model output must map to ReasoningError, not an
    unguarded ValueError/KeyError escaping to an unmapped HTTP 500.
    """

    def test_malformed_cited_memory_id_is_a_reasoning_error_not_a_value_error(self) -> None:
        payload = {
            "decision": "remediate_and_record",
            "explanation": "rollback",
            "cited_memory_id": "not-a-uuid",
            "action": "rollback",
            "target_version": "1.4.2",
            "requires_human_approval": False,
            "confidence": 0.9,
        }
        with self.assertRaises(ReasoningError):
            _decide(payload)

    def test_missing_action_key_is_a_reasoning_error_not_a_key_error(self) -> None:
        payload = {
            "decision": "remediate_and_record",
            "explanation": "rollback",
            "cited_memory_id": str(MEMORY_ID),
            # "action" omitted entirely.
            "target_version": "1.4.2",
            "confidence": 0.9,
        }
        with self.assertRaises(ReasoningError):
            _decide(payload)

    def test_invalid_action_enum_value_is_a_reasoning_error_not_a_value_error(self) -> None:
        payload = {
            "decision": "remediate_and_record",
            "explanation": "rollback",
            "cited_memory_id": str(MEMORY_ID),
            "action": "drop_database",
            "target_version": "1.4.2",
            "confidence": 0.9,
        }
        with self.assertRaises(ReasoningError):
            _decide(payload)

    def test_non_dict_requires_human_approval_does_not_crash_parsing(self) -> None:
        # A weird-but-JSON-legal shape (e.g. a string) must still resolve to
        # a well-typed bool via the guarded block, not raise unexpectedly.
        payload = {
            "decision": "remediate_and_record",
            "explanation": "rollback",
            "cited_memory_id": str(MEMORY_ID),
            "action": "rollback",
            "target_version": "1.4.2",
            "requires_human_approval": "yes",
            "confidence": 0.9,
        }
        decision = _decide(payload)
        self.assertIsNotNone(decision.command)

    def test_well_formed_payload_still_parses_correctly(self) -> None:
        payload = {
            "decision": "remediate_and_record",
            "explanation": "Prior rollback matched.",
            "cited_memory_id": str(MEMORY_ID),
            "action": "rollback",
            "target_version": "1.4.2",
            "requires_human_approval": False,
            "confidence": 0.95,
        }
        decision = _decide(payload)
        self.assertIsNotNone(decision.command)
        self.assertEqual(decision.command.target_version, "1.4.2")


class ClientTimeoutConfigTests(unittest.TestCase):
    """backend#8: Bedrock clients must not use unbounded default timeouts."""

    def test_shared_client_config_bounds_connect_and_read_timeouts(self) -> None:
        self.assertEqual(_BEDROCK_CLIENT_CONFIG.connect_timeout, 5)
        self.assertEqual(_BEDROCK_CLIENT_CONFIG.read_timeout, 30)
        self.assertEqual(_BEDROCK_CLIENT_CONFIG.retries["max_attempts"], 3)


if __name__ == "__main__":
    unittest.main()
