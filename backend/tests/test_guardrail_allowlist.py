"""Tool allowlist + destructive-action gate (charter R3, R5)."""

from __future__ import annotations

import unittest
from dataclasses import replace
from uuid import UUID

from postmortem_backend.adapters.fakes import (
    FakeAtomicRemediationStore,
    FakeEmbeddingAdapter,
    FakeReasoningAdapter,
    FakeRecallAdapter,
)
from postmortem_backend.domain import (
    ActionKind,
    DecisionKind,
    IncidentSignal,
    MemoryCandidate,
    MemoryKind,
    RecallBundle,
    RemediationCommand,
)
from postmortem_backend.errors import DestructiveActionBlocked, ToolNotAllowed
from postmortem_backend.events import EventBroker
from postmortem_backend.guardrails.allowlist import (
    ALLOWED_TOOLS,
    ApprovalRecord,
    authorize_action,
    enforce_tool_allowlist,
    is_critical_tier_service,
    is_high_blast_radius,
    resolve_decision_tool,
)
from postmortem_backend.service import ResponderService


ORG_ID = UUID("20000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("20000000-0000-0000-0000-000000000002")
INCIDENT_ID = UUID("20000000-0000-0000-0000-000000000003")
SESSION_ID = UUID("20000000-0000-0000-0000-000000000004")
SERVICE_ID = UUID("20000000-0000-0000-0000-000000000005")
MEMORY_ID = UUID("20000000-0000-0000-0000-000000000006")


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


class AllowlistTests(unittest.TestCase):
    def test_allowlist_admits_known_tools(self) -> None:
        self.assertEqual(enforce_tool_allowlist("remediate_and_record"), "remediate_and_record")
        self.assertIn("recall_memory", ALLOWED_TOOLS)
        self.assertIn("scale_data_tier", ALLOWED_TOOLS)

    def test_allowlist_denies_unknown_or_destructive_tool(self) -> None:
        for name in ("drop_table", "exec", "delete_all_incidents", ""):
            with self.assertRaises(ToolNotAllowed):
                enforce_tool_allowlist(name)

    def test_every_decision_maps_to_an_allowlisted_tool(self) -> None:
        for kind in DecisionKind:
            self.assertIn(resolve_decision_tool(kind), ALLOWED_TOOLS)


class DestructiveGateTests(unittest.TestCase):
    def test_reversible_action_auto_approves_without_a_human(self) -> None:
        record = authorize_action(command(), human_approved=False, approver=None)
        self.assertIsInstance(record, ApprovalRecord)
        self.assertFalse(record.high_blast_radius)
        self.assertTrue(record.approved)

    def test_inherently_destructive_action_blocked_without_approval(self) -> None:
        self.assertTrue(is_high_blast_radius(command(action=ActionKind.SCALE)))
        with self.assertRaises(DestructiveActionBlocked):
            authorize_action(
                command(action=ActionKind.SCALE), human_approved=False
            )

    def test_policy_flagged_action_blocked_without_approval(self) -> None:
        with self.assertRaises(DestructiveActionBlocked):
            authorize_action(
                command(requires_human_approval=True), human_approved=False
            )

    def test_approval_requires_a_named_approver(self) -> None:
        with self.assertRaises(DestructiveActionBlocked):
            authorize_action(
                command(action=ActionKind.SCALE),
                human_approved=True,
                approver="   ",
            )

    def test_named_human_approval_is_recorded(self) -> None:
        record = authorize_action(
            command(action=ActionKind.SCALE),
            human_approved=True,
            approver="oncall@granthvani.com",
        )
        self.assertTrue(record.high_blast_radius)
        self.assertEqual(record.approver, "oncall@granthvani.com")
        self.assertIn("approver", record.to_dict())


class CriticalServiceGateTests(unittest.TestCase):
    """C2 (HIGH): destructiveness is a server-side decision keyed on
    service_tags, not on the reasoner's own requires_human_approval flag.
    """

    def test_is_critical_tier_service_matches_known_tags_case_insensitively(self) -> None:
        self.assertTrue(is_critical_tier_service(("Checkout",)))
        self.assertTrue(is_critical_tier_service(("payments", "other")))
        self.assertTrue(is_critical_tier_service((" BILLING ",)))
        self.assertFalse(is_critical_tier_service(("inventory", "search")))
        self.assertFalse(is_critical_tier_service(()))

    def test_rollback_on_critical_service_is_high_blast_radius_even_if_model_says_no(
        self,
    ) -> None:
        # requires_human_approval=False is exactly what a lying/wrong model
        # would report to skip the gate -- the server-side check must still
        # flag it because the service is critical-tier.
        cmd = command(action=ActionKind.ROLLBACK, requires_human_approval=False)
        self.assertTrue(is_high_blast_radius(cmd, service_tags=("checkout",)))
        with self.assertRaises(DestructiveActionBlocked):
            authorize_action(
                cmd, human_approved=False, service_tags=("checkout", "web")
            )

    def test_restart_on_payments_tier_also_requires_approval(self) -> None:
        cmd = command(action=ActionKind.RESTART, requires_human_approval=False)
        with self.assertRaises(DestructiveActionBlocked):
            authorize_action(cmd, human_approved=False, service_tags=("payments",))

    def test_named_approval_still_permits_a_critical_service_rollback(self) -> None:
        cmd = command(action=ActionKind.ROLLBACK, requires_human_approval=False)
        record = authorize_action(
            cmd,
            human_approved=True,
            approver="sre@granthvani.com",
            service_tags=("checkout",),
        )
        self.assertTrue(record.high_blast_radius)
        self.assertEqual(record.approver, "sre@granthvani.com")

    def test_rollback_on_a_non_critical_service_stays_auto_approved(self) -> None:
        cmd = command(action=ActionKind.ROLLBACK, requires_human_approval=False)
        record = authorize_action(
            cmd, human_approved=False, service_tags=("inventory-search",)
        )
        self.assertFalse(record.high_blast_radius)
        self.assertTrue(record.approved)

    def test_model_flag_can_only_add_caution_never_remove_it(self) -> None:
        # The model asking for approval on a NON-critical service is still
        # honored (advisory upgrade, never a downgrade of the server check).
        cmd = command(action=ActionKind.ROLLBACK, requires_human_approval=True)
        with self.assertRaises(DestructiveActionBlocked):
            authorize_action(cmd, human_approved=False, service_tags=("inventory",))


class ServiceDestructiveGateTests(unittest.TestCase):
    """The gate holds end-to-end through the responder, not just in isolation."""

    def _service(self, candidate: MemoryCandidate):
        events = EventBroker()
        store = FakeAtomicRemediationStore(auto_seed=True)
        service = ResponderService(
            embedder=FakeEmbeddingAdapter(),
            recall=FakeRecallAdapter(RecallBundle(runbooks=(candidate,))),
            reasoner=FakeReasoningAdapter(),
            remediation=store,
            events=events,
        )
        return service, store

    def _signal(self) -> IncidentSignal:
        return IncidentSignal(
            incident_id=INCIDENT_ID,
            session_id=SESSION_ID,
            org_id=ORG_ID,
            agent_id=AGENT_ID,
            service_id=SERVICE_ID,
            severity="SEV-1",
            summary="Checkout 5xx after canary deploy",
            error_signature="HTTP_5XX_POST_DEPLOY",
            service_tags=("checkout",),
        )

    def _runbook(self, **metadata: object) -> MemoryCandidate:
        return MemoryCandidate(
            memory_id=MEMORY_ID,
            kind=MemoryKind.PROCEDURAL,
            content="Rollback checkout canary after correlated 5xx spike.",
            similarity=0.94,
            success_rate=0.9,
            runbook_id=MEMORY_ID,
            metadata={
                "action": "rollback",
                "target_version": "1.4.2",
                **metadata,
            },
        )

    def test_scale_action_is_refused_and_nothing_commits(self) -> None:
        service, store = self._service(
            self._runbook(action="scale", target_version="8-nodes")
        )
        with self.assertRaises(DestructiveActionBlocked):
            service.handle(self._signal())
        self.assertEqual(store.commit_count, 0)
        self.assertEqual(len(store.deploys), 0)


if __name__ == "__main__":
    unittest.main()
