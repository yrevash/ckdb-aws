from __future__ import annotations

import json
import math
import unittest
from datetime import UTC, datetime
from uuid import UUID

from postmortem_backend.adapters.fakes import FakeEmbeddingAdapter
from postmortem_backend.adapters.mcp import ManagedMCPRecallAdapter
from postmortem_backend.domain import AgentEvent, EventType, RecallQuery
from postmortem_backend.transport import console_event, sse_message


INCIDENT_ID = UUID("20000000-0000-0000-0000-000000000001")


class FakeMCPTransport:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def call_tool(self, name: str, arguments: dict[str, object]):
        self.assert_select(name)
        query = str(arguments["query"])
        self.queries.append(query)
        if "FROM episodic_events" in query:
            rows = [
                {
                    "event_id": "20000000-0000-0000-0000-000000000002",
                    "service_id": "20000000-0000-0000-0000-000000000003",
                    "content": "Prior checkout rollback succeeded",
                    "similarity": 0.93,
                    "metadata": {"outcome": "success"},
                }
            ]
        elif "FROM semantic_facts" in query:
            rows = [
                {
                    "fact_id": "20000000-0000-0000-0000-000000000004",
                    "subject": "service:checkout",
                    "predicate": "safe_rollback_target",
                    "object": {"version": "v41"},
                    "confidence": 0.98,
                    "similarity": 0.9,
                }
            ]
        else:
            rows = [
                {
                    "runbook_id": "20000000-0000-0000-0000-000000000005",
                    "name": "rollback-on-5xx-spike",
                    "trigger_desc": "5xx spike after canary",
                    "steps": [{"step": 1, "tool": "rollback_deploy"}],
                    "usage_count": 3,
                    "success_rate": 0.9,
                    "similarity": 0.95,
                    "positive_provenance_count": 2,
                    "counterexample_count": 0,
                }
            ]
        return {"content": [{"type": "text", "text": json.dumps({"rows": rows})}]}

    @staticmethod
    def assert_select(name: str) -> None:
        if name != "select_query":
            raise AssertionError(f"unexpected tool {name}")


class TransportAndAdapterTests(unittest.TestCase):
    def test_fake_embedding_is_normalized_and_1024_dimensional(self) -> None:
        embedding = FakeEmbeddingAdapter().embed("checkout 5xx")
        self.assertEqual(len(embedding), 1024)
        self.assertAlmostEqual(
            math.sqrt(sum(value * value for value in embedding)),
            1.0,
            places=9,
        )

    def test_managed_mcp_recall_uses_three_read_only_queries(self) -> None:
        transport = FakeMCPTransport()
        adapter = ManagedMCPRecallAdapter(transport)
        embedding = FakeEmbeddingAdapter().embed("checkout 5xx")

        bundle = adapter.recall(
            RecallQuery(
                org_id=UUID("20000000-0000-0000-0000-000000000010"),
                agent_id=UUID("20000000-0000-0000-0000-000000000011"),
                service_id=UUID("20000000-0000-0000-0000-000000000003"),
                text="checkout 5xx",
                embedding=embedding,
            )
        )

        self.assertEqual(len(transport.queries), 3)
        self.assertEqual(len(bundle.episodes), 1)
        self.assertEqual(len(bundle.facts), 1)
        self.assertEqual(len(bundle.runbooks), 1)
        self.assertTrue(all("INSERT" not in query for query in transport.queries))
        self.assertTrue(all("UPDATE" not in query for query in transport.queries))

    def test_sse_mapper_matches_frontend_contract(self) -> None:
        event = AgentEvent(
            incident_id=INCIDENT_ID,
            type=EventType.TRANSACTION_COMMITTED,
            stage="act+record",
            message="Committed together.",
            data={"deploy_id": "deploy-1", "event_id": "event-1"},
            occurred_at=datetime(2026, 7, 30, tzinfo=UTC),
        )

        envelope = console_event(event, sequence=7)

        self.assertEqual(
            set(envelope),
            {"id", "sequence", "occurredAt", "caseId", "agent", "type", "payload"},
        )
        self.assertEqual(envelope["sequence"], 7)
        self.assertEqual(envelope["caseId"], str(INCIDENT_ID))
        self.assertEqual(
            envelope["agent"], {"id": "responder", "region": "us-east"}
        )
        self.assertEqual(envelope["type"], "transaction")
        self.assertEqual(envelope["payload"]["state"], "committed")
        self.assertIn("transactionId", envelope["payload"])
        self.assertEqual(len(envelope["payload"]["statements"]), 4)
        wire = sse_message(event, sequence=7)
        self.assertIn("event: transaction\n", wire)
        self.assertIn('"sequence":7', wire)


if __name__ == "__main__":
    unittest.main()
