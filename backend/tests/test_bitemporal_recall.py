"""Phase 3 / Track B: bitemporal semantic-fact recall, transition, and
belief-history unit tests. Stub-based (no live database) -- mirrors the
StubCursor/StubConnection/StubProvider pattern in test_phase2_recall.py.
backend/tests/test_bitemporal_live.py exercises the real SQL against a
running CockroachDB instance.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from postmortem_backend.adapters.recall import (
    CLOSE_SUPERSEDED_FACT_SQL,
    INSERT_TRANSITIONED_FACT_SQL,
    CockroachRecallAdapter,
    FactHistoryEntry,
    FactTransitionResult,
)
from postmortem_backend.domain import RecallQuery
from postmortem_backend.recall import RecallRanker


ORG_ID = UUID("50000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("50000000-0000-0000-0000-000000000002")
SERVICE_ID = UUID("50000000-0000-0000-0000-000000000003")
NOW = datetime(2026, 7, 31, 12, tzinfo=UTC)


def query(**changes: object) -> RecallQuery:
    from dataclasses import replace

    from postmortem_backend.adapters.fakes import FakeEmbeddingAdapter

    value = RecallQuery(
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        service_id=SERVICE_ID,
        text="checkout depends on fraud scoring",
        embedding=FakeEmbeddingAdapter().embed("checkout depends on fraud scoring"),
        as_of=NOW,
    )
    return replace(value, **changes)


class StubCursor:
    def __init__(self, rows_by_table: dict[str, list[dict[str, object]]]) -> None:
        self.rows_by_table = rows_by_table
        self.current: list[dict[str, object]] | None = None
        self.executions: list[tuple[str, dict[str, object] | None]] = []
        self.description = ()

    def __enter__(self) -> "StubCursor":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        return None

    def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
        self.executions.append((sql, params))
        self.current = next(
            (rows for table, rows in self.rows_by_table.items() if table in sql),
            [],
        )

    def fetchall(self) -> list[dict[str, object]]:
        return self.current or []

    def fetchone(self) -> tuple[Any, ...] | None:
        rows = self.current or []
        if not rows:
            return None
        row = rows[0]
        if isinstance(row, tuple):
            return row
        return tuple(row.values())


class StubConnection:
    def __init__(self, cursor: StubCursor) -> None:
        self.stub_cursor = cursor
        self.commits = 0

    def cursor(self) -> StubCursor:
        return self.stub_cursor

    @contextmanager
    def transaction(self):
        yield
        self.commits += 1


class StubProvider:
    def __init__(self, connection: StubConnection) -> None:
        self.connection = connection
        self.calls = 0

    @contextmanager
    def __call__(self):
        self.calls += 1
        yield self.connection


def test_semantic_recall_sql_gates_on_bitemporal_validity_window() -> None:
    from postmortem_backend.adapters.recall import SEMANTIC_RECALL_SQL

    assert "valid_from <= %(as_of)s" in SEMANTIC_RECALL_SQL
    assert "valid_to IS NULL OR valid_to > %(as_of)s" in SEMANTIC_RECALL_SQL
    assert "recorded_at <= %(as_of)s" in SEMANTIC_RECALL_SQL
    assert "superseded_by" in SEMANTIC_RECALL_SQL
    assert "AS predecessor" in SEMANTIC_RECALL_SQL


def test_recall_surfaces_validity_window_and_superseded_predecessor() -> None:
    fact_id = UUID("50000000-0000-0000-0000-000000000070")
    predecessor_fact_id = UUID("50000000-0000-0000-0000-000000000071")
    cursor = StubCursor(
        {
            "FROM episodic_events": [],
            "FROM semantic_facts": [
                {
                    "fact_id": fact_id,
                    "org_id": ORG_ID,
                    "agent_id": AGENT_ID,
                    "subject": "service:checkout",
                    "predicate": "depends_on",
                    "object": '{"service": "fraud-scoring-v2"}',
                    "confidence": 0.95,
                    "source": "consolidation_job",
                    "valid_from": NOW - timedelta(days=1),
                    "valid_to": None,
                    "recorded_at": NOW - timedelta(days=1),
                    "superseded_by": None,
                    "similarity": 0.92,
                    "scoped_service_id": SERVICE_ID,
                    "provenance_ids": [],
                    "predecessor": (
                        '{"fact_id": "%s", "object": {"service": "fraud-scoring-v1"}, '
                        '"confidence": 0.9, "valid_from": "2026-07-01T00:00:00+00:00", '
                        '"valid_to": "2026-07-30T12:00:00+00:00", '
                        '"recorded_at": "2026-07-01T00:00:00+00:00", "source": "human_stated"}'
                    )
                    % predecessor_fact_id,
                }
            ],
            "FROM procedural_memory": [],
        }
    )
    provider = StubProvider(StubConnection(cursor))

    result = CockroachRecallAdapter(provider).recall(query())

    assert [item.memory_id for item in result.facts] == [fact_id]
    fact = result.facts[0]
    # Validity window is exposed on the typed MemoryCandidate fields (not
    # buried in metadata) -- this is what recall.py's temporal-validity gate
    # (_temporally_valid) reasons over, and what the console renders as the
    # fact's belief window.
    assert fact.valid_from == NOW - timedelta(days=1)
    assert fact.valid_to is None
    assert fact.metadata["superseded_by"] is None
    predecessor = fact.metadata["superseded_predecessor"]
    assert predecessor["fact_id"] == str(predecessor_fact_id)
    assert predecessor["object"] == {"service": "fraud-scoring-v1"}
    # And the ranking diagnostics roll this up for the audit/UI surface.
    assert result.diagnostics["bitemporal"]["facts_with_predecessor"] == 1
    assert result.diagnostics["bitemporal"]["stale_facts_returned"] == 0


def test_ranker_never_treats_a_returned_stale_fact_as_current() -> None:
    """Defense-in-depth: even if a superseded fact somehow reached the ranker
    (it shouldn't -- SEMANTIC_RECALL_SQL's valid_to filter excludes it), the
    ranker's own diagnostics would flag it as stale, not hide it.
    """

    from postmortem_backend.domain import MemoryCandidate, MemoryKind

    stale = MemoryCandidate(
        memory_id=UUID("50000000-0000-0000-0000-000000000080"),
        kind=MemoryKind.SEMANTIC,
        content="service:checkout depends_on: fraud-scoring-v1",
        similarity=0.9,
        confidence=0.9,
        valid_from=NOW - timedelta(days=30),
        valid_to=NOW - timedelta(days=1),
        recorded_at=NOW - timedelta(days=30),
        metadata={"superseded_by": "50000000-0000-0000-0000-000000000081"},
    )
    current = MemoryCandidate(
        memory_id=UUID("50000000-0000-0000-0000-000000000081"),
        kind=MemoryKind.SEMANTIC,
        content="service:checkout depends_on: fraud-scoring-v2",
        similarity=0.9,
        confidence=0.9,
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        recorded_at=NOW - timedelta(days=1),
        metadata={
            "superseded_by": None,
            "superseded_predecessor": {"object": "fraud-scoring-v1"},
        },
    )

    result = RecallRanker().rank(query(), facts=(stale, current))

    # The stale fact fails the temporal-validity gate outright -- it is
    # never eligible, let alone returned as "the" fact.
    assert [item.memory_id for item in result.facts] == [current.memory_id]
    assert result.diagnostics["bitemporal"]["facts_with_predecessor"] == 1
    assert result.diagnostics["bitemporal"]["stale_facts_returned"] == 0


def test_transition_inserts_new_fact_then_closes_and_supersedes_old_one() -> None:
    old_fact_id = UUID("50000000-0000-0000-0000-000000000091")
    close_cursor_rows = [(old_fact_id,)]

    class TransitionCursor(StubCursor):
        def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
            self.executions.append((sql, params))
            if "INSERT INTO semantic_facts" in sql:
                # The row CockroachDB would actually return: the client-
                # generated fact_id echoed back, plus server-assigned times.
                self.current = [(params["new_fact_id"], NOW, NOW)]
            elif "UPDATE semantic_facts" in sql:
                self.current = close_cursor_rows
            else:
                self.current = []

        def fetchone(self):
            rows = self.current or []
            return rows[0] if rows else None

    cursor = TransitionCursor({})
    provider = StubProvider(StubConnection(cursor))

    result = CockroachRecallAdapter(provider).transition_fact(
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        subject="service:checkout",
        predicate="depends_on",
        object_value={"service": "fraud-scoring-v3"},
        embedding=tuple([1.0] + [0.0] * 1023),
        source="consolidation_job",
        transition_at=NOW,
    )

    assert isinstance(result, FactTransitionResult)
    assert result.superseded_fact_id == old_fact_id
    # Insert must run before close/supersede: the FK on superseded_by is
    # checked per-statement (not deferred), so the new row has to exist
    # before the old row can point at it.
    assert cursor.executions[0][0].strip() == "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"
    assert cursor.executions[1][0].strip().startswith("INSERT INTO semantic_facts")
    assert INSERT_TRANSITIONED_FACT_SQL.strip().startswith("INSERT INTO semantic_facts")
    assert cursor.executions[2][0].strip().startswith("UPDATE semantic_facts")
    assert CLOSE_SUPERSEDED_FACT_SQL.strip().startswith("UPDATE semantic_facts")
    # Both statements share the same client-generated fact id, and that id
    # is exactly what the caller gets back.
    assert cursor.executions[1][1]["new_fact_id"] == cursor.executions[2][1]["new_fact_id"]
    assert result.new_fact_id == cursor.executions[1][1]["new_fact_id"]
    assert provider.connection.commits == 1


def test_transition_with_no_prior_fact_is_a_pure_insert() -> None:
    class TransitionCursor(StubCursor):
        def execute(self, sql: str, params: dict[str, object] | None = None) -> None:
            self.executions.append((sql, params))
            if "INSERT INTO semantic_facts" in sql:
                self.current = [(params["new_fact_id"], NOW, NOW)]
            else:
                self.current = []  # no currently-valid fact to close

        def fetchone(self):
            rows = self.current or []
            return rows[0] if rows else None

    cursor = TransitionCursor({})
    provider = StubProvider(StubConnection(cursor))

    result = CockroachRecallAdapter(provider).transition_fact(
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        subject="service:new-service",
        predicate="owner_team",
        object_value="platform",
        embedding=tuple([1.0] + [0.0] * 1023),
        transition_at=NOW,
    )

    assert result.new_fact_id == cursor.executions[1][1]["new_fact_id"]
    assert result.superseded_fact_id is None


def test_fact_history_query_orders_oldest_first_and_is_read_only() -> None:
    older = UUID("50000000-0000-0000-0000-0000000000a0")
    newer = UUID("50000000-0000-0000-0000-0000000000a1")
    cursor = StubCursor(
        {
            "FROM semantic_facts": [
                {
                    "fact_id": older,
                    "object": '{"service": "fraud-scoring-v1"}',
                    "confidence": 0.9,
                    "source": "human_stated",
                    "valid_from": NOW - timedelta(days=30),
                    "valid_to": NOW - timedelta(days=1),
                    "recorded_at": NOW - timedelta(days=30),
                    "superseded_by": newer,
                },
                {
                    "fact_id": newer,
                    "object": '{"service": "fraud-scoring-v2"}',
                    "confidence": 0.95,
                    "source": "consolidation_job",
                    "valid_from": NOW - timedelta(days=1),
                    "valid_to": None,
                    "recorded_at": NOW - timedelta(days=1),
                    "superseded_by": None,
                },
            ]
        }
    )
    provider = StubProvider(StubConnection(cursor))

    history = CockroachRecallAdapter(provider).fact_history(
        org_id=ORG_ID, subject="service:checkout", predicate="depends_on"
    )

    assert [entry.fact_id for entry in history] == [older, newer]
    assert isinstance(history[0], FactHistoryEntry)
    assert history[0].superseded_by == newer
    assert history[1].superseded_by is None
    assert cursor.executions[0][0] == "SET TRANSACTION READ ONLY"
