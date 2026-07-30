from __future__ import annotations

import unittest
from dataclasses import replace
from uuid import UUID

from postmortem_backend.adapters.fakes import (
    FakeAtomicRemediationStore,
    FakeEmbeddingAdapter,
)
from postmortem_backend.domain import ActionKind, RemediationCommand
from postmortem_backend.errors import ApprovalRequired, AtomicRemediationError, ProvenanceError


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("00000000-0000-0000-0000-000000000002")
INCIDENT_ID = UUID("00000000-0000-0000-0000-000000000003")
SESSION_ID = UUID("00000000-0000-0000-0000-000000000004")
SERVICE_ID = UUID("00000000-0000-0000-0000-000000000005")
MEMORY_ID = UUID("00000000-0000-0000-0000-000000000006")


def command(**changes: object) -> RemediationCommand:
    base = RemediationCommand(
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        incident_id=INCIDENT_ID,
        session_id=SESSION_ID,
        service_id=SERVICE_ID,
        action=ActionKind.ROLLBACK,
        target_version="checkout-2026.07.29.1",
        cited_memory_id=MEMORY_ID,
        runbook_id=MEMORY_ID,
        rationale="A matching successful rollback exists.",
    )
    return replace(base, **changes)


class AtomicRemediationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = FakeAtomicRemediationStore()
        self.store.seed(
            service_id=SERVICE_ID,
            incident_id=INCIDENT_ID,
            cited_memory_id=MEMORY_ID,
            org_id=ORG_ID,
        )
        self.embedding = FakeEmbeddingAdapter().embed("rollback checkout")

    def test_commit_changes_operation_and_memory_together(self) -> None:
        result = self.store.remediate_and_record(command(), self.embedding)

        self.assertEqual(self.store.commit_count, 1)
        self.assertEqual(self.store.rollback_count, 0)
        self.assertEqual(self.store.services[SERVICE_ID]["status"], "recovering")
        self.assertEqual(
            self.store.services[SERVICE_ID]["current_deploy_id"], result.deploy_id
        )
        self.assertEqual(self.store.incidents[INCIDENT_ID]["status"], "mitigating")
        self.assertEqual(len(self.store.deploys), 1)
        self.assertEqual(len(self.store.episodes), 1)
        self.assertEqual(len(self.store.remediation_actions), 1)
        episode = self.store.episodes[result.event_id]
        self.assertEqual(episode["metadata"]["deploy_id"], str(result.deploy_id))
        action = self.store.remediation_actions[result.action_id]
        self.assertEqual(action["memory_ref"], result.event_id)
        self.assertEqual(action["transaction_id"], result.transaction_id)
        self.assertNotEqual(result.action_id, result.deploy_id)
        self.assertEqual(self.store.runbook_usage[MEMORY_ID], 1)

    def test_failure_after_operational_write_rolls_back_everything(self) -> None:
        before = self.store.snapshot()
        self.store.fail_at = "after_operational_write"

        with self.assertRaises(AtomicRemediationError):
            self.store.remediate_and_record(command(), self.embedding)

        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.commit_count, 0)
        self.assertEqual(self.store.rollback_count, 1)

    def test_failure_during_memory_write_rolls_back_operational_mutation(self) -> None:
        before = self.store.snapshot()
        self.store.fail_at = "during_memory_write"

        with self.assertRaises(AtomicRemediationError):
            self.store.remediate_and_record(command(), self.embedding)

        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.commit_count, 0)
        self.assertEqual(self.store.rollback_count, 1)

    def test_failure_during_action_audit_rolls_back_operation_and_memory(self) -> None:
        before = self.store.snapshot()
        self.store.fail_at = "during_audit_write"

        with self.assertRaises(AtomicRemediationError):
            self.store.remediate_and_record(command(), self.embedding)

        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.store.commit_count, 0)
        self.assertEqual(self.store.rollback_count, 1)

    def test_missing_provenance_rejects_entire_transaction(self) -> None:
        before = self.store.snapshot()

        with self.assertRaises(ProvenanceError):
            self.store.remediate_and_record(
                command(
                    cited_memory_id=UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
                ),
                self.embedding,
            )

        self.assertEqual(self.store.snapshot(), before)

    def test_human_gate_runs_before_transaction(self) -> None:
        with self.assertRaises(ApprovalRequired):
            self.store.remediate_and_record(
                command(requires_human_approval=True),
                self.embedding,
            )
        self.assertEqual(self.store.commit_count, 0)
        self.assertEqual(self.store.rollback_count, 0)


if __name__ == "__main__":
    unittest.main()
