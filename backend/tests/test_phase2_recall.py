from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from postmortem_backend.adapters.fakes import FakeEmbeddingAdapter
from postmortem_backend.adapters.fakes import FakeReasoningAdapter
from postmortem_backend.adapters.mcp import ManagedMCPRecallAdapter
from postmortem_backend.adapters.recall import CockroachRecallAdapter
from postmortem_backend.domain import (
    DecisionKind,
    IncidentSignal,
    MemoryCandidate,
    MemoryKind,
    RecallBundle,
    RecallQuery,
)
from postmortem_backend.recall import ColdStartRecallAdapter, RecallPolicy, RecallRanker


ORG_ID = UUID("40000000-0000-0000-0000-000000000001")
AGENT_ID = UUID("40000000-0000-0000-0000-000000000002")
SERVICE_ID = UUID("40000000-0000-0000-0000-000000000003")
INCIDENT_ID = UUID("40000000-0000-0000-0000-000000000004")
NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def query(**changes: object) -> RecallQuery:
    value = RecallQuery(
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        service_id=SERVICE_ID,
        current_incident_id=INCIDENT_ID,
        text="checkout 5xx after canary",
        embedding=FakeEmbeddingAdapter().embed("checkout 5xx after canary"),
        service_tags=("checkout", "critical-path"),
        error_signature="HTTP_5XX_POST_DEPLOY",
        as_of=NOW,
    )
    return replace(value, **changes)


def candidate(
    kind: MemoryKind,
    suffix: int,
    **changes: object,
) -> MemoryCandidate:
    value = MemoryCandidate(
        memory_id=UUID(f"40000000-0000-0000-0000-{suffix:012d}"),
        kind=kind,
        content=f"candidate {suffix}",
        similarity=0.90,
        success_rate=0.85,
        occurred_at=NOW - timedelta(days=2),
        service_id=SERVICE_ID,
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        confidence=0.90,
        valid_from=NOW - timedelta(days=30),
        recorded_at=NOW - timedelta(days=29),
        provenance_ids=(
            UUID(f"41000000-0000-0000-0000-{suffix:012d}"),
        ),
        metadata={},
    )
    return replace(value, **changes)


def runbook(suffix: int, **changes: object) -> MemoryCandidate:
    base = candidate(
        MemoryKind.PROCEDURAL,
        suffix,
        runbook_id=UUID(f"40000000-0000-0000-0000-{suffix:012d}"),
        service_id=None,
        metadata={
            "name": f"rollback-{suffix}",
            "usage_count": 4,
            "positive_provenance_count": 3,
            "counterexample_count": 0,
            "applicable_service_tags": ("checkout",),
            "applicable_error_signatures": ("HTTP_5XX_POST_DEPLOY",),
            "last_used_at": (NOW - timedelta(days=2)).isoformat(),
        },
    )
    if "metadata" in changes:
        merged = {**base.metadata, **changes.pop("metadata")}
        changes["metadata"] = merged
    return replace(base, **changes)


def test_temporal_scope_confidence_and_similarity_gates() -> None:
    ranker = RecallRanker()
    valid_episode = candidate(
        MemoryKind.EPISODIC,
        10,
        metadata={
            "source_case_id": "case-a",
            "outcome": "success",
            "action_id": "action-a",
        },
    )
    episodes = (
        valid_episode,
        candidate(
            MemoryKind.EPISODIC,
            11,
            org_id=UUID("ffffffff-0000-0000-0000-000000000001"),
        ),
        candidate(
            MemoryKind.EPISODIC,
            12,
            service_id=UUID("ffffffff-0000-0000-0000-000000000002"),
        ),
        candidate(
            MemoryKind.EPISODIC,
            13,
            occurred_at=NOW - timedelta(days=500),
        ),
        candidate(MemoryKind.EPISODIC, 14, similarity=0.40),
    )
    valid_fact = candidate(
        MemoryKind.SEMANTIC,
        20,
        metadata={"subject": "service:checkout", "predicate": "depends_on"},
    )
    facts = (
        valid_fact,
        candidate(MemoryKind.SEMANTIC, 21, confidence=0.40),
        candidate(
            MemoryKind.SEMANTIC,
            22,
            valid_to=NOW - timedelta(seconds=1),
        ),
        candidate(
            MemoryKind.SEMANTIC,
            23,
            recorded_at=NOW + timedelta(seconds=1),
        ),
    )

    result = ranker.rank(query(), episodes=episodes, facts=facts)

    assert len(result.episodes) == 1
    assert result.episodes[0].memory_id == valid_episode.memory_id
    assert result.episodes[0].metadata["actionable"] is True
    assert result.facts[0].memory_id == valid_fact.memory_id
    assert result.facts[0].metadata["actionable"] is False
    assert result.diagnostics["candidate_counts"] == {
        "episodic": 5,
        "semantic": 4,
        "procedural": 0,
    }
    assert result.diagnostics["eligible_counts"]["episodic"] == 1
    assert result.diagnostics["eligible_counts"]["semantic"] == 1


def test_runbook_requires_track_record_provenance_and_applicability() -> None:
    valid = runbook(30)
    candidates = (
        valid,
        runbook(31, success_rate=0.30),
        runbook(32, metadata={"usage_count": 0}),
        runbook(33, metadata={"positive_provenance_count": 0}),
        runbook(
            34,
            metadata={
                "positive_provenance_count": 1,
                "counterexample_count": 2,
            },
        ),
        runbook(35, metadata={"applicable_service_tags": ("search",)}),
        runbook(
            36,
            metadata={"applicable_error_signatures": ("POOL_EXHAUSTED",)},
        ),
        runbook(37, similarity=0.50),
    )

    result = RecallRanker().rank(query(), runbooks=candidates)

    assert [item.memory_id for item in result.runbooks] == [valid.memory_id]
    assert result.runbooks[0].metadata["actionable"] is True
    assert result.runbooks[0].metadata["provenance_verified"] is True
    assert result.diagnostics["eligible_counts"]["procedural"] == 1


def test_reasoner_cannot_act_from_memory_that_failed_safety_gate() -> None:
    unsafe = runbook(38, metadata={"actionable": False})
    signal = IncidentSignal(
        incident_id=INCIDENT_ID,
        session_id=UUID("40000000-0000-0000-0000-000000000099"),
        org_id=ORG_ID,
        agent_id=AGENT_ID,
        service_id=SERVICE_ID,
        severity="SEV-1",
        summary="checkout 5xx after canary",
    )

    decision = FakeReasoningAdapter().decide(
        signal,
        RecallBundle(runbooks=(unsafe,)),
    )

    assert decision.kind is DecisionKind.ESCALATE
    assert decision.command is None


def test_reranking_rewards_outcome_and_diversifies_source_cases_and_versions() -> None:
    episodes = (
        candidate(
            MemoryKind.EPISODIC,
            40,
            similarity=0.92,
            metadata={"source_case_id": "case-a", "outcome": "failed"},
        ),
        candidate(
            MemoryKind.EPISODIC,
            41,
            similarity=0.90,
            metadata={
                "source_case_id": "case-a",
                "outcome": "success",
                "action_id": "action-a",
            },
        ),
        candidate(
            MemoryKind.EPISODIC,
            42,
            similarity=0.88,
            metadata={
                "source_case_id": "case-b",
                "outcome": "success",
                "action_id": "action-b",
            },
        ),
    )
    runbooks = (
        runbook(50, metadata={"name": "rollback-checkout"}),
        runbook(
            51,
            similarity=0.89,
            metadata={"name": "rollback-checkout", "version": 2},
        ),
        runbook(52, similarity=0.86, metadata={"name": "restart-checkout"}),
    )

    result = RecallRanker().rank(query(), episodes=episodes, runbooks=runbooks)

    assert len(result.episodes) == 2
    assert {item.metadata["source_case_id"] for item in result.episodes} == {
        "case-a",
        "case-b",
    }
    assert result.episodes[0].metadata["outcome"] == "success"
    assert len(result.runbooks) == 2
    assert {item.metadata["name"] for item in result.runbooks} == {
        "rollback-checkout",
        "restart-checkout",
    }
    assert all(
        result.runbooks[index].ranking_score
        >= result.runbooks[index + 1].ranking_score
        for index in range(len(result.runbooks) - 1)
    )


class CountingRecall:
    def __init__(self) -> None:
        self.calls = 0

    def recall(self, _: RecallQuery):
        self.calls += 1
        raise AssertionError("cold-start adapter must not call its delegate")


def test_cold_start_bypasses_memory_provider() -> None:
    delegate = CountingRecall()

    result = ColdStartRecallAdapter(delegate).recall(query())

    assert result.cold_start is True
    assert result.all_candidates == ()
    assert result.diagnostics["database_queries"] == 0
    assert delegate.calls == 0


class StubCursor:
    def __init__(self, rows_by_table: dict[str, list[dict[str, object]]]) -> None:
        self.rows_by_table = rows_by_table
        self.current: list[dict[str, object]] = []
        self.executions: list[tuple[str, dict[str, object] | None]] = []
        self.description = ()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None

    def execute(self, sql: str, params=None) -> None:
        self.executions.append((sql, params))
        self.current = next(
            (rows for table, rows in self.rows_by_table.items() if table in sql),
            [],
        )

    def fetchall(self):
        return self.current


class StubConnection:
    def __init__(self, cursor: StubCursor) -> None:
        self.stub_cursor = cursor
        self.commits = 0

    def cursor(self):
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


def test_direct_cockroach_adapter_fetches_three_stages_and_ranks() -> None:
    runbook_id = UUID("40000000-0000-0000-0000-000000000070")
    provenance_id = UUID("40000000-0000-0000-0000-000000000071")
    cursor = StubCursor(
        {
            "FROM episodic_events": [],
            "FROM semantic_facts": [],
            "FROM procedural_memory": [
                {
                    "runbook_id": runbook_id,
                    "org_id": ORG_ID,
                    "agent_id": AGENT_ID,
                    "name": "rollback-checkout",
                    "version": 1,
                    "trigger_desc": "5xx after checkout canary",
                    "applicable_service_tags": ["checkout"],
                    "applicable_error_signatures": ["HTTP_5XX_POST_DEPLOY"],
                    "preconditions": [],
                    "steps": [{"step": 1, "tool": "rollback_deploy"}],
                    "postconditions": [],
                    "usage_count": 4,
                    "success_count": 4,
                    "failure_count": 0,
                    "success_rate": 1.0,
                    "last_used_at": NOW - timedelta(days=1),
                    "created_at": NOW - timedelta(days=20),
                    "created_by": "consolidation_job",
                    "similarity": 0.95,
                    "positive_provenance_count": 2,
                    "counterexample_count": 0,
                    "provenance_ids": [provenance_id],
                }
            ],
        }
    )
    connection = StubConnection(cursor)
    provider = StubProvider(connection)

    result = CockroachRecallAdapter(provider).recall(query())

    assert provider.calls == 1
    assert connection.commits == 1
    assert len(cursor.executions) == 4
    assert cursor.executions[0][0] == "SET TRANSACTION READ ONLY"
    assert [item.memory_id for item in result.runbooks] == [runbook_id]
    assert result.runbooks[0].provenance_ids == (provenance_id,)
    for sql, params in cursor.executions[1:]:
        assert "<=>" in sql
        assert "org_id = %(org_id)s" in sql
        assert params is not None
        assert params["org_id"] == ORG_ID
        assert params["agent_id"] == AGENT_ID
        assert params["service_id"] == SERVICE_ID
        assert params["candidate_limit"] >= 20


def test_direct_and_mcp_cold_start_issue_no_queries() -> None:
    cursor = StubCursor({})
    provider = StubProvider(StubConnection(cursor))
    direct = CockroachRecallAdapter(provider)
    cold_query = query(cold_start=True)

    direct_result = direct.recall(cold_query)

    class NoCallMCP:
        def call_tool(self, name, arguments):
            raise AssertionError((name, arguments))

    mcp_result = ManagedMCPRecallAdapter(NoCallMCP()).recall(cold_query)
    assert direct_result.cold_start is True
    assert mcp_result.cold_start is True
    assert provider.calls == 0
