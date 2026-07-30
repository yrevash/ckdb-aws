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
        self.assertIn("CockroachWriterSecret", self.source)
        self.assertIn("bedrock:InvokeModel", self.source)

    def test_fargate_hosts_the_interactive_agent(self) -> None:
        self.assertIn("ApplicationLoadBalancedFargateService", self.source)
        self.assertIn('path="/healthz"', self.source)

    def test_runtime_configuration_matches_backend_contract(self) -> None:
        self.assertIn('"POSTMORTEM_RUNTIME_MODE": "aws"', self.source)
        self.assertIn('"POSTMORTEM_DATABASE_URL"', self.source)
        self.assertIn('"POSTMORTEM_MCP_TOKEN"', self.source)


if __name__ == "__main__":
    unittest.main()
