from pathlib import Path
import unittest


class InfrastructureSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = (
            Path(__file__).parents[1] / "postmortem_infra" / "stacks.py"
        ).read_text(encoding="utf-8")

    def test_reader_and_writer_credentials_are_separate(self) -> None:
        self.assertIn("CockroachReaderSecret", self.source)
        # audit B1: CockroachReaderSecret above holds the Managed-MCP URL/token,
        # not a SQL DSN. The read-only SQL identity is a secret of its own.
        self.assertIn("CockroachSqlReaderSecret", self.source)
        self.assertIn("CockroachWriterSecret", self.source)
        self.assertIn("bedrock:InvokeModel", self.source)

    def test_fargate_hosts_the_interactive_agent(self) -> None:
        self.assertIn("ApplicationLoadBalancedFargateService", self.source)
        self.assertIn('path="/healthz"', self.source)

    def test_runtime_configuration_matches_backend_contract(self) -> None:
        self.assertIn('"POSTMORTEM_RUNTIME_MODE": "aws"', self.source)
        # POSTMORTEM_DATABASE_URL stays required: config.py validate() still
        # hard-requires it in aws mode (audit B1).
        self.assertIn('"POSTMORTEM_DATABASE_URL"', self.source)
        self.assertIn('"POSTMORTEM_MCP_TOKEN"', self.source)
        # audit B1: build_runtime reads the two role-specific DSNs and fails
        # closed with RoleScopeViolation unless they are distinct principals.
        self.assertIn('"POSTMORTEM_READER_DATABASE_URL"', self.source)
        self.assertIn('"POSTMORTEM_WRITER_DATABASE_URL"', self.source)

    def test_deploy_blocking_context_is_not_silently_defaulted(self) -> None:
        # audit B4: agent_image_uri used to default to a stock python image --
        # the service deployed and never passed /healthz. audit B2: a missing
        # PrivateLink service name used to emit a PLACEHOLDER output and deploy
        # a VPC with no path to CockroachDB at all.
        self.assertNotIn("public.ecr.aws/docker/library/python", self.source)
        self.assertNotIn("PLACEHOLDER-set-crdb_privatelink_service_name", self.source)


if __name__ == "__main__":
    unittest.main()
