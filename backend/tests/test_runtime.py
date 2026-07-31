"""Runtime composition: the provenance guard is actually wired (audit C5),
and AWS mode fails closed on a cosmetic reader/writer role split (audit C3).
"""

from __future__ import annotations

import unittest
from contextlib import contextmanager
from uuid import UUID

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
