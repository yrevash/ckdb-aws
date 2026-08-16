from pathlib import Path
import unittest


class ConsolidationInfrastructureSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        infra = Path(__file__).parents[1]
        cls.stack = (
            infra / "postmortem_infra" / "consolidation_stack.py"
        ).read_text(encoding="utf-8")
        cls.app = (infra / "app.py").read_text(encoding="utf-8")
        cls.shared = (
            infra / "postmortem_infra" / "stacks.py"
        ).read_text(encoding="utf-8")

    def test_app_synthesizes_consolidation_stack(self) -> None:
        self.assertIn("ConsolidationStack(", self.app)
        self.assertIn("PostmortemConsolidation", self.app)

    def test_fast_ack_queue_worker_and_dlq_are_wired(self) -> None:
        self.assertIn("ChangefeedReceiver", self.stack)
        self.assertIn("ConsolidationQueue", self.stack)
        self.assertIn("Consolidator", self.stack)
        self.assertIn("ConsolidationDeadLetterQueue", self.stack)
        self.assertIn("report_batch_item_failures=True", self.stack)

    def test_pipeline_has_nightly_fallback_and_dlq_alarm(self) -> None:
        self.assertIn("NightlyConsolidation", self.stack)
        self.assertIn("ConsolidationDlqDepthAlarm", self.stack)
        self.assertIn("ChangefeedWebhookUrl", self.stack)
        self.assertIn("CONSOLIDATION_EMBEDDING_MODEL_ID", self.stack)
        self.assertIn("RUNBOOK_REINFORCEMENTS_TO_ACTIVATE", self.stack)

    def test_consolidation_follows_the_shared_egress_mode_subnets(self) -> None:
        # audit B2: SharedStack owns the subnet type per crdb_egress_mode.
        # Hardcoding PRIVATE_ISOLATED here breaks `crdb_egress_mode=public`.
        self.assertIn("shared.compute_subnets", self.stack)
        self.assertNotIn("SubnetType.PRIVATE_ISOLATED", self.stack)

    def test_credentials_are_scoped_to_consolidation(self) -> None:
        self.assertIn("CockroachConsolidatorSecret", self.shared)
        self.assertIn("ChangefeedWebhookSecret", self.shared)
        self.assertIn("shared.consolidator_secret.grant_read(worker)", self.stack)
        self.assertIn(
            "shared.changefeed_webhook_secret.grant_read(receiver)", self.stack
        )


if __name__ == "__main__":
    unittest.main()
