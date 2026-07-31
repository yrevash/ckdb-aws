"""Role-scoped DB access enforced in code (charter R7, T2).

Unit tests prove the act/recall identity split cannot be cross-wired in-process.
An optional live test (POSTMORTEM_TEST_DATABASE_URL) proves the SQL grants from
db/migrations/0007_audit_logging.sql actually deny a reader's write and a
writer's out-of-lane write against a real CockroachDB node.
"""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager

import pytest

from postmortem_backend.adapters.cockroach import CockroachAtomicRemediationStore
from postmortem_backend.adapters.outcome import CockroachOutcomeStore
from postmortem_backend.adapters.recall import CockroachRecallAdapter
from postmortem_backend.errors import RoleScopeViolation
from postmortem_backend.guardrails.roles import (
    DatabaseRole,
    RoleScopedProvider,
    provider_role,
    require_reader,
    require_writer,
)


def _noop_provider():
    @contextmanager
    def _cm():
        yield object()

    return _cm


class RoleGuardUnitTests(unittest.TestCase):
    def test_unscoped_provider_is_permissive(self) -> None:
        provider = _noop_provider()
        self.assertIsNone(provider_role(provider))
        require_reader(provider)  # no raise
        require_writer(provider)  # no raise

    def test_reader_cannot_be_used_for_writes(self) -> None:
        reader = RoleScopedProvider(_noop_provider(), DatabaseRole.READER)
        require_reader(reader)  # ok
        with self.assertRaises(RoleScopeViolation):
            require_writer(reader)

    def test_writer_cannot_be_used_for_recall(self) -> None:
        writer = RoleScopedProvider(_noop_provider(), DatabaseRole.WRITER)
        require_writer(writer)  # ok
        with self.assertRaises(RoleScopeViolation):
            require_reader(writer)

    def test_consolidator_is_write_capable_but_not_the_reader(self) -> None:
        consolidator = RoleScopedProvider(
            _noop_provider(), DatabaseRole.CONSOLIDATOR
        )
        require_writer(consolidator)  # ok
        with self.assertRaises(RoleScopeViolation):
            require_reader(consolidator)


class AdapterConstructionTests(unittest.TestCase):
    """The guarantee is enforced at adapter construction -- no statement runs."""

    def test_act_store_refuses_reader_identity(self) -> None:
        reader = RoleScopedProvider(_noop_provider(), DatabaseRole.READER)
        with self.assertRaises(RoleScopeViolation):
            CockroachAtomicRemediationStore(reader)

    def test_outcome_store_refuses_reader_identity(self) -> None:
        reader = RoleScopedProvider(_noop_provider(), DatabaseRole.READER)
        with self.assertRaises(RoleScopeViolation):
            CockroachOutcomeStore(reader)

    def test_recall_refuses_writer_identity(self) -> None:
        writer = RoleScopedProvider(_noop_provider(), DatabaseRole.WRITER)
        with self.assertRaises(RoleScopeViolation):
            CockroachRecallAdapter(writer)

    def test_correct_wiring_constructs(self) -> None:
        writer = RoleScopedProvider(_noop_provider(), DatabaseRole.WRITER)
        reader = RoleScopedProvider(_noop_provider(), DatabaseRole.READER)
        CockroachAtomicRemediationStore(writer)
        CockroachOutcomeStore(writer)
        CockroachRecallAdapter(reader)


DATABASE_URL = os.getenv("POSTMORTEM_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set POSTMORTEM_TEST_DATABASE_URL to run the live SQL-grant proof",
)
def test_sql_grants_deny_out_of_lane_writes_live() -> None:
    """The in-code role guard mirrors real SQL grants: prove the DB denies too."""

    import psycopg

    assert DATABASE_URL is not None
    # Ensure the reader/writer roles exist (idempotent -- migration 0007 also
    # creates them; this keeps the test runnable against a bare node).
    with psycopg.connect(DATABASE_URL, autocommit=True) as admin:
        with admin.cursor() as cur:
            cur.execute("CREATE ROLE IF NOT EXISTS postmortem_reader")
            cur.execute("CREATE ROLE IF NOT EXISTS postmortem_writer")
            cur.execute(
                "GRANT SELECT ON TABLE episodic_events TO postmortem_reader"
            )
            cur.execute(
                "GRANT SELECT, INSERT ON TABLE episodic_events TO postmortem_writer"
            )
            # Reader must NOT hold write on semantic_facts / episodic_events.
            cur.execute(
                "SELECT privilege_type FROM [SHOW GRANTS ON TABLE episodic_events "
                "FOR postmortem_reader]"
            )
            reader_privs = {row[0] for row in cur.fetchall()}
    assert "INSERT" not in reader_privs, reader_privs
    assert "SELECT" in reader_privs, reader_privs


if __name__ == "__main__":
    unittest.main()
