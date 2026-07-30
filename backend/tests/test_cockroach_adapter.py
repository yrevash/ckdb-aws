from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

from postmortem_backend.adapters.cockroach import CockroachAtomicRemediationStore
from postmortem_backend.adapters.fakes import FakeEmbeddingAdapter
from postmortem_backend.domain import ActionKind, RemediationCommand
from postmortem_backend.errors import ProvenanceError


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


def command() -> RemediationCommand:
    return RemediationCommand(
        org_id=UUID("30000000-0000-0000-0000-000000000001"),
        agent_id=UUID("30000000-0000-0000-0000-000000000002"),
        incident_id=UUID("30000000-0000-0000-0000-000000000003"),
        session_id=UUID("30000000-0000-0000-0000-000000000004"),
        service_id=UUID("30000000-0000-0000-0000-000000000005"),
        action=ActionKind.ROLLBACK,
        target_version="v41",
        cited_memory_id=UUID("30000000-0000-0000-0000-000000000006"),
        runbook_id=UUID("30000000-0000-0000-0000-000000000006"),
        rationale="Prior runbook succeeded.",
    )


class CockroachAdapterTests(unittest.TestCase):
    def test_returns_distinct_action_and_transaction_provenance(self) -> None:
        deploy_id = UUID("30000000-0000-0000-0000-000000000010")
        event_id = UUID("30000000-0000-0000-0000-000000000011")
        action_id = UUID("30000000-0000-0000-0000-000000000012")
        transaction_id = UUID("30000000-0000-0000-0000-000000000013")
        committed_at = datetime(2026, 7, 30, tzinfo=UTC)
        connection = StubConnection(
            (deploy_id, event_id, action_id, transaction_id, committed_at)
        )
        adapter = CockroachAtomicRemediationStore(StubProvider(connection))

        result = adapter.remediate_and_record(
            command(), FakeEmbeddingAdapter().embed("rollback checkout")
        )

        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertEqual(result.deploy_id, deploy_id)
        self.assertEqual(result.event_id, event_id)
        self.assertEqual(result.action_id, action_id)
        self.assertEqual(result.transaction_id, transaction_id)
        transaction_sql = connection.stub_cursor.executions[1][0]
        self.assertIn("UPDATE services", transaction_sql)
        self.assertIn("SET health = 'recovering'", transaction_sql)
        self.assertIn("INSERT INTO episodic_events", transaction_sql)
        self.assertIn("INSERT INTO remediation_actions", transaction_sql)

    def test_missing_gate_result_rolls_back(self) -> None:
        connection = StubConnection(None)
        adapter = CockroachAtomicRemediationStore(StubProvider(connection))

        with self.assertRaises(ProvenanceError):
            adapter.remediate_and_record(
                command(), FakeEmbeddingAdapter().embed("rollback checkout")
            )

        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
