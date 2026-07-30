"""Opt-in proof that the bitemporal transition + point-in-time recall run
against real CockroachDB. Mirrors test_cockroach_live.py's skip-if-not-set
pattern; point POSTMORTEM_TEST_DATABASE_URL at your own instance (Track B was
verified against a standalone container on :26261, never the shared :26257
node)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest

from postmortem_backend.adapters.cockroach import PsycopgPoolProvider
from postmortem_backend.adapters.recall import CockroachRecallAdapter
from postmortem_backend.domain import RecallQuery


DATABASE_URL = os.getenv("POSTMORTEM_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="set POSTMORTEM_TEST_DATABASE_URL to run the live CockroachDB proof",
)
def test_transition_closes_old_fact_and_recall_returns_only_the_current_one() -> None:
    assert DATABASE_URL is not None
    org_id, agent_id, service_id = uuid4(), uuid4(), uuid4()
    embedding = tuple([1.0] + [0.0] * 1023)
    service_name = f"checkout-{service_id}"
    subject = f"service:{service_name}"

    with psycopg.connect(DATABASE_URL, autocommit=False) as connection:
        with connection.transaction():
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO organizations (org_id, slug, display_name) "
                    "VALUES (%s, %s, %s)",
                    (org_id, f"track-b-{org_id}", "Track B bitemporal live proof"),
                )
                cursor.execute(
                    """
                    INSERT INTO services (
                        service_id, org_id, name, tier, health, current_version
                    )
                    VALUES (%s, %s, %s, 'critical-path', 'healthy', '1.0.0')
                    """,
                    (service_id, org_id, service_name),
                )

    adapter = CockroachRecallAdapter(PsycopgPoolProvider(DATABASE_URL))

    # First assertion: no prior fact for this (subject, predicate) -- a pure
    # insert, nothing to supersede.
    first = adapter.transition_fact(
        org_id=org_id,
        agent_id=agent_id,
        subject=subject,
        predicate="depends_on",
        object_value={"service": "fraud-scoring-v1"},
        embedding=embedding,
        source="human_stated",
    )
    assert first.superseded_fact_id is None

    # Recall immediately after the first assertion: the only belief in the
    # world is fraud-scoring-v1, and it must come back as current.
    immediately_after_first = adapter.recall(
        RecallQuery(
            org_id=org_id,
            agent_id=agent_id,
            service_id=service_id,
            text=f"{subject} depends_on",
            embedding=embedding,
            as_of=datetime.now(UTC),
        )
    )
    assert [item.memory_id for item in immediately_after_first.facts] == [
        first.new_fact_id
    ]
    assert immediately_after_first.facts[0].valid_to is None
    assert immediately_after_first.facts[0].metadata["superseded_predecessor"] is None

    # Second assertion: the environment changed (last week's deploy added a
    # fraud-scoring-v2 call) -- transition, don't overwrite.
    second = adapter.transition_fact(
        org_id=org_id,
        agent_id=agent_id,
        subject=subject,
        predicate="depends_on",
        object_value={"service": "fraud-scoring-v2"},
        embedding=embedding,
        source="consolidation_job",
    )
    assert second.superseded_fact_id == first.new_fact_id

    # Recall "as of now": only the currently-valid fact (v2) comes back, with
    # its superseded predecessor (v1) exposed for the audit/UI trail.
    now_query = RecallQuery(
        org_id=org_id,
        agent_id=agent_id,
        service_id=service_id,
        text=f"{subject} depends_on",
        embedding=embedding,
        as_of=datetime.now(UTC),
    )
    current = adapter.recall(now_query)
    assert [item.memory_id for item in current.facts] == [second.new_fact_id]
    current_fact = current.facts[0]
    assert current_fact.valid_to is None
    predecessor = current_fact.metadata["superseded_predecessor"]
    assert predecessor is not None
    assert predecessor["fact_id"] == str(first.new_fact_id)
    assert predecessor["object"] == {"service": "fraud-scoring-v1"}
    assert current.diagnostics["bitemporal"]["facts_with_predecessor"] == 1
    assert current.diagnostics["bitemporal"]["stale_facts_returned"] == 0

    # Point-in-time recall "as of" a moment before the transition: the fact
    # that was true *then* (v1) comes back, not the current belief -- this is
    # the whole point of bitemporal facts over a plain UPDATE.
    before_query = RecallQuery(
        org_id=org_id,
        agent_id=agent_id,
        service_id=service_id,
        text=f"{subject} depends_on",
        embedding=embedding,
        as_of=first.recorded_at + timedelta(milliseconds=1),
    )
    as_of_first = adapter.recall(before_query)
    assert [item.memory_id for item in as_of_first.facts] == [first.new_fact_id]

    # Full belief history: both facts, oldest first, chained by
    # superseded_by.
    history = adapter.fact_history(
        org_id=org_id, subject=subject, predicate="depends_on"
    )
    assert [entry.fact_id for entry in history] == [
        first.new_fact_id,
        second.new_fact_id,
    ]
    assert history[0].superseded_by == second.new_fact_id
    assert history[0].valid_to == second.valid_from
    assert history[1].superseded_by is None
    assert history[1].valid_to is None
