"""Provenance gate: no ungrounded action reaches the act path (charter R4, R7)."""

from __future__ import annotations

import unittest
from dataclasses import replace
from uuid import UUID

from postmortem_backend.adapters.fakes import (
    FakeAtomicRemediationStore,
    FakeEmbeddingAdapter,
)
from postmortem_backend.domain import ActionKind, RemediationCommand
from postmortem_backend.errors import ProvenanceError
from postmortem_backend.guardrails.provenance import (
    ProvenanceGuardedRemediation,
    require_grounded_action,
    verify_citation_resolves,
)


ORG_ID = UUID("40000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("40000000-0000-0000-0000-000000000002")
INCIDENT_ID = UUID("40000000-0000-0000-0000-000000000003")
SESSION_ID = UUID("40000000-0000-0000-0000-000000000004")
SERVICE_ID = UUID("40000000-0000-0000-0000-000000000005")
MEMORY_ID = UUID("40000000-0000-0000-0000-000000000006")
OTHER_ID = UUID("40000000-0000-0000-0000-0000000000ff")


def command(**changes: object) -> RemediationCommand:
    base = RemediationCommand(
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        incident_id=INCIDENT_ID,
        session_id=SESSION_ID,
        service_id=SERVICE_ID,
        action=ActionKind.ROLLBACK,
        target_version="1.4.2",
        cited_memory_id=MEMORY_ID,
        runbook_id=MEMORY_ID,
        rationale="Prior successful rollback matched.",
    )
    return replace(base, **changes)


class GroundedActionTests(unittest.TestCase):
    def test_nil_citation_is_ungrounded(self) -> None:
        with self.assertRaises(ProvenanceError):
            require_grounded_action(command(cited_memory_id=UUID(int=0)))

    def test_citation_absent_from_recall_is_rejected(self) -> None:
        with self.assertRaises(ProvenanceError):
            require_grounded_action(command(), recalled_ids=[OTHER_ID])

    def test_citation_resolving_to_recalled_id_passes(self) -> None:
        require_grounded_action(command(), recalled_ids=[MEMORY_ID, OTHER_ID])

    def test_verify_citation_resolves(self) -> None:
        self.assertTrue(verify_citation_resolves(MEMORY_ID, [MEMORY_ID]))
        self.assertFalse(verify_citation_resolves(MEMORY_ID, [OTHER_ID]))
        self.assertFalse(verify_citation_resolves(None, [MEMORY_ID]))
        self.assertFalse(verify_citation_resolves(UUID(int=0), [MEMORY_ID]))


class ProvenanceGuardTests(unittest.TestCase):
    """The wrapper cannot be bypassed; the store's own DB gate stays authoritative."""

    def setUp(self) -> None:
        self.store = FakeAtomicRemediationStore()
        self.store.seed(
            service_id=SERVICE_ID,
            incident_id=INCIDENT_ID,
            cited_memory_id=MEMORY_ID,
            org_id=ORG_ID,
        )
        self.embedding = FakeEmbeddingAdapter().embed("rollback checkout")

    def test_guard_blocks_ungrounded_before_store_is_touched(self) -> None:
        guarded = ProvenanceGuardedRemediation(
            self.store, recalled_ids=[OTHER_ID]
        )
        with self.assertRaises(ProvenanceError):
            guarded.remediate_and_record(command(), self.embedding)
        # The store never ran: no commit, no rollback -- the guard fired first.
        self.assertEqual(self.store.commit_count, 0)
        self.assertEqual(self.store.rollback_count, 0)

    def test_guard_delegates_a_grounded_action(self) -> None:
        guarded = ProvenanceGuardedRemediation(
            self.store, recalled_ids=[MEMORY_ID]
        )
        result = guarded.remediate_and_record(command(), self.embedding)
        self.assertIsNotNone(result.action_id)
        self.assertEqual(self.store.commit_count, 1)

    def test_store_gate_still_rejects_citation_absent_from_db(self) -> None:
        # Even if the app-layer recalled-id check is unset, the store's own
        # provenance gate (in-SQL for CockroachDB) rejects a citation with no
        # backing row -- defense in depth, no single point of trust.
        guarded = ProvenanceGuardedRemediation(self.store)
        with self.assertRaises(ProvenanceError):
            guarded.remediate_and_record(
                command(cited_memory_id=OTHER_ID), self.embedding
            )


if __name__ == "__main__":
    unittest.main()
