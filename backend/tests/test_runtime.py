"""Runtime composition: the provenance guard is actually wired (audit C5),
and AWS mode fails closed on a cosmetic reader/writer role split (audit C3).
"""

from __future__ import annotations

import unittest
from contextlib import ExitStack, contextmanager
from unittest import mock
from uuid import UUID

from postmortem_backend import runtime as runtime_module
from postmortem_backend.adapters.fakes import FakeAtomicRemediationStore
from postmortem_backend.config import Settings
from postmortem_backend.errors import RoleScopeViolation
from postmortem_backend.guardrails.provenance import ProvenanceGuardedRemediation
from postmortem_backend.runtime import build_runtime, verify_distinct_database_identities


ORG_ID = UUID("90000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("90000000-0000-0000-0000-000000000002")


def _fake_settings() -> Settings:
    return Settings(
        runtime_mode="fake",
        host="127.0.0.1",
        port=8080,
        log_level="INFO",
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        aws_region="us-east-1",
        reasoning_model_id="offline",
        embedding_model_id="offline",
        reasoner="bedrock",
        cors_origins=("http://localhost:3000",),
        database_url=None,
        mcp_url=None,
        mcp_token=None,
    )


def _aws_settings(**overrides: object) -> Settings:
    base = dict(
        runtime_mode="aws",
        host="127.0.0.1",
        port=8080,
        log_level="INFO",
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        aws_region="us-east-1",
        reasoning_model_id="offline",
        embedding_model_id="offline",
        reasoner="bedrock",
        cors_origins=("http://localhost:3000",),
        database_url="postgresql://shared_user@db.example:26257/postmortem",
        mcp_url=None,
        mcp_token=None,
        recall_backend="sql",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class ProvenanceGuardWiringTests(unittest.TestCase):
    """C5: ProvenanceGuardedRemediation must actually wrap the act path in
    both runtime modes, not sit unused as dead code.
    """

    def test_fake_mode_wraps_the_remediation_store_in_the_provenance_guard(self) -> None:
        runtime = build_runtime(_fake_settings())

        self.assertIsInstance(
            runtime.responder._remediation, ProvenanceGuardedRemediation
        )
        # The OUTCOME path is untouched: ProvenanceGuardedRemediation only
        # implements remediate_and_record, not record_outcome, and the raw
        # store is what OutcomeService needs.
        self.assertIsInstance(runtime.outcomes._outcomes, FakeAtomicRemediationStore)

    def test_wrapped_store_still_delegates_to_the_real_one(self) -> None:
        runtime = build_runtime(_fake_settings())
        # ProvenanceGuardedRemediation._inner is the real fake store the
        # responder ultimately commits through -- the guard is a wrapper,
        # not a replacement.
        self.assertIsInstance(
            runtime.responder._remediation._inner, FakeAtomicRemediationStore
        )


class RoleSeparationFailClosedTests(unittest.TestCase):
    """C3: AWS mode must refuse a cosmetic reader/writer split before any
    connection is opened.
    """

    def test_missing_distinct_reader_and_writer_dsns_falls_back_and_is_refused(
        self,
    ) -> None:
        # database_url set, but no distinct reader/writer DSNs -- both
        # resolve to the same DSN, which is exactly the cosmetic-split bug.
        with self.assertRaises(RoleScopeViolation):
            build_runtime(_aws_settings())

    def test_identical_explicit_reader_and_writer_dsns_are_refused(self) -> None:
        same = "postgresql://agent@db.example:26257/postmortem"
        with self.assertRaises(RoleScopeViolation):
            build_runtime(
                _aws_settings(
                    database_url=None,
                    reader_database_url=same,
                    writer_database_url=same,
                )
            )

    def test_distinct_dsns_with_the_same_embedded_username_are_refused(self) -> None:
        # Different hosts, same principal -- the DSN-string check must catch
        # the shared-username case too, not just byte-identical DSNs.
        with self.assertRaises(RoleScopeViolation):
            build_runtime(
                _aws_settings(
                    database_url=None,
                    reader_database_url="postgresql://agent@reader.example:26257/postmortem",
                    writer_database_url="postgresql://agent@writer.example:26257/postmortem",
                )
            )

    def test_missing_reader_or_writer_dsn_entirely_is_refused(self) -> None:
        with self.assertRaises(RoleScopeViolation):
            build_runtime(_aws_settings(database_url=None, writer_database_url=None))


class _StubPoolProvider:
    """Stand-in for PsycopgPoolProvider that opens no socket."""

    def __init__(self, database_url: str, **_kwargs: object) -> None:
        self.database_url = database_url

    def __call__(self) -> object:  # pragma: no cover - never dialled here
        raise AssertionError("the stub pool must not be used for real queries")

    def close(self) -> None:
        return None


class _StubReasoner:
    def __init__(self, **_kwargs: object) -> None:
        return None


class _StubMCPTransport:
    """The real StreamableHttpMCPTransport performs its MCP `initialize`
    handshake inside ``__init__``, so constructing one offline raises
    RecallError. Composition, not the wire protocol, is what is under test.
    """

    def __init__(self, endpoint: str, token: str, **_kwargs: object) -> None:
        self.endpoint = endpoint
        self.token = token

    def call_tool(self, name: str, arguments: dict) -> object:  # pragma: no cover
        raise AssertionError("the stub transport must not be called")


# The environment the deployed task definition actually receives
# (infra/postmortem_infra/stacks.py, AppStack container `environment`/`secrets`).
# Kept as a literal dict so a drift between what infra injects and what
# config.from_env reads shows up HERE rather than as a Fargate crashloop
# (audit B1: infra used to inject POSTMORTEM_DATABASE_URL and nothing else,
# so both DSNs resolved to one value and startup raised RoleScopeViolation
# before /healthz could ever answer).
def _app_stack_environment(recall_backend: str = "sql") -> dict[str, str]:
    return {
        "POSTMORTEM_RUNTIME_MODE": "aws",
        "POSTMORTEM_HOST": "0.0.0.0",
        "POSTMORTEM_PORT": "8000",
        "POSTMORTEM_AWS_REGION": "us-east-1",
        "POSTMORTEM_REASONING_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
        "POSTMORTEM_EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
        "POSTMORTEM_REASONER": "strands",
        "POSTMORTEM_CORS_ORIGINS": "https://console.example.com",
        "POSTMORTEM_MCP_URL": "https://cockroachlabs.cloud/mcp",
        "POSTMORTEM_RECALL_BACKEND": recall_backend,
        "POSTMORTEM_DB_SSLMODE": "verify-full",
        # ---- Secrets Manager -> container secrets ------------------------
        "POSTMORTEM_MCP_TOKEN": "mcp-token-from-CockroachReaderSecret",
        # POSTMORTEM_DATABASE_URL is the writer DSN; config.validate() still
        # hard-requires it in aws mode.
        "POSTMORTEM_DATABASE_URL": (
            "postgresql://postmortem_agent_writer@db.example:26257/postmortem"
        ),
        "POSTMORTEM_WRITER_DATABASE_URL": (
            "postgresql://postmortem_agent_writer@db.example:26257/postmortem"
        ),
        "POSTMORTEM_READER_DATABASE_URL": (
            "postgresql://postmortem_agent_reader@db.example:26257/postmortem"
        ),
    }


class DeployedEnvironmentContractTests(unittest.TestCase):
    """B1 regression lock: the environment AppStack injects must actually
    compose a runtime, and the pre-fix environment must still be refused.

    psycopg and strands are deploy-only dependencies, so both adapters are
    stubbed -- what is under test is the CREDENTIAL WIRING, not the drivers.
    """

    @contextmanager
    def _offline_deploy_adapters(self, environment: dict[str, str]):
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.dict("os.environ", environment, clear=True)
            )
            stack.enter_context(
                mock.patch.object(
                    runtime_module, "PsycopgPoolProvider", _StubPoolProvider
                )
            )
            stack.enter_context(
                mock.patch.object(
                    runtime_module, "StrandsReasoningAdapter", _StubReasoner
                )
            )
            stack.enter_context(
                mock.patch.object(
                    runtime_module,
                    "StreamableHttpMCPTransport",
                    _StubMCPTransport,
                )
            )
            yield

    def _build(self, environment: dict[str, str]):
        with self._offline_deploy_adapters(environment):
            return build_runtime(Settings.from_env())

    def test_the_environment_app_stack_injects_builds_a_runtime(self) -> None:
        runtime = self._build(_app_stack_environment("sql"))
        dsns = [
            resource.database_url
            for resource in runtime.resources
            if isinstance(resource, _StubPoolProvider)
        ]
        self.assertEqual(len(dsns), 2)
        self.assertNotEqual(
            dsns[0],
            dsns[1],
            "the reader and writer pools were opened against one DSN",
        )

    def test_the_public_egress_mode_environment_also_builds(self) -> None:
        # crdb_egress_mode=public flips POSTMORTEM_RECALL_BACKEND to mcp, which
        # takes a different branch through build_runtime -- both must start.
        runtime = self._build(_app_stack_environment("mcp"))
        self.assertIsNotNone(runtime.responder)

    def test_injecting_only_postmortem_database_url_is_still_refused(self) -> None:
        # Exactly what infra shipped before audit B1: one secret, so reader and
        # writer both fall back to it. This MUST keep failing closed.
        environment = _app_stack_environment("sql")
        del environment["POSTMORTEM_READER_DATABASE_URL"]
        del environment["POSTMORTEM_WRITER_DATABASE_URL"]
        with self.assertRaises(RoleScopeViolation):
            self._build(environment)

    def test_pointing_both_dsn_vars_at_one_secret_is_still_refused(self) -> None:
        # The other way to reintroduce B1: two env vars, one Secrets Manager
        # secret behind both.
        environment = _app_stack_environment("sql")
        environment["POSTMORTEM_READER_DATABASE_URL"] = environment[
            "POSTMORTEM_WRITER_DATABASE_URL"
        ]
        with self.assertRaises(RoleScopeViolation):
            self._build(environment)


class LiveIdentityVerificationTests(unittest.TestCase):
    """The deploy-time-only connection-level check (best-effort backstop
    beyond the DSN-string comparison above).
    """

    @staticmethod
    def _provider(identity: str):
        class _Cursor:
            def __enter__(self) -> "_Cursor":
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def execute(self, _sql: str) -> None:
                return None

            def fetchone(self) -> tuple[str]:
                return (identity,)

        class _Connection:
            def cursor(self) -> _Cursor:
                return _Cursor()

        @contextmanager
        def _cm():
            yield _Connection()

        return _cm

    def test_same_current_user_across_both_pools_is_refused(self) -> None:
        with self.assertRaises(RoleScopeViolation):
            verify_distinct_database_identities(
                self._provider("postmortem_shared"), self._provider("postmortem_shared")
            )

    def test_distinct_current_user_passes(self) -> None:
        verify_distinct_database_identities(
            self._provider("postmortem_agent_reader"),
            self._provider("postmortem_agent_writer"),
        )  # no raise


if __name__ == "__main__":
    unittest.main()
