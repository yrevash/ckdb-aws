from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .contracts import CandidateRunbook


@dataclass(frozen=True)
class RunbookMutation:
    operation: str
    runbook_id: str
    version: int
    success_count: int
    failure_count: int
    status: str
    idempotent_replay: bool = False


class RunbookRepository(Protocol):
    def apply(
        self, candidate: CandidateRunbook, idempotency_key: str
    ) -> RunbookMutation: ...


@dataclass
class _LocalRunbook:
    runbook_id: str
    version: int
    steps_hash: str
    success_count: int
    failure_count: int
    status: str
    embedding: tuple[float, ...]


class InMemoryRunbookRepository:
    """Behavioral model of create/reinforce/weaken for deterministic tests."""

    def __init__(self, *, reinforcements_to_activate: int = 2) -> None:
        if reinforcements_to_activate < 1:
            raise ValueError("reinforcements_to_activate must be at least 1")
        self.reinforcements_to_activate = reinforcements_to_activate
        self.runbooks: dict[tuple[str, str, str], list[_LocalRunbook]] = {}
        self.processed: dict[str, RunbookMutation] = {}

    def apply(
        self, candidate: CandidateRunbook, idempotency_key: str
    ) -> RunbookMutation:
        if idempotency_key in self.processed:
            previous = self.processed[idempotency_key]
            return RunbookMutation(
                **{
                    **previous.__dict__,
                    "operation": "noop",
                    "idempotent_replay": True,
                }
            )

        key = (candidate.org_id, candidate.agent_id, candidate.name)
        versions = self.runbooks.setdefault(key, [])
        latest = versions[-1] if versions else None

        if candidate.outcome != "success":
            # A failed / no-effect episode must NEVER create a runbook or
            # create-and-deprecate the incumbent (memory-poisoning guard, audit
            # C1). It can only weaken an existing matching runbook, or be ignored
            # outright when there is nothing to weaken.
            if latest is None:
                mutation = RunbookMutation(
                    "ignored_counterexample", "", 0, 0, 0, "absent"
                )
            else:
                latest.failure_count += 1
                if latest.failure_count >= latest.success_count:
                    latest.status = "deprecated"
                mutation = RunbookMutation(
                    "weaken",
                    latest.runbook_id,
                    latest.version,
                    latest.success_count,
                    latest.failure_count,
                    latest.status,
                )
        elif latest is None or latest.steps_hash != candidate.steps_hash:
            # Only a successful episode reaches here, so a create is always
            # sourced from a proven outcome.
            if len(candidate.embedding) != 1024:
                raise ValueError("candidate runbook must have a VECTOR(1024) embedding")
            runbook = _LocalRunbook(
                runbook_id=f"rb-{len(self.runbooks)}-{len(versions) + 1}",
                version=(latest.version + 1) if latest else 1,
                steps_hash=candidate.steps_hash,
                success_count=1,
                failure_count=0,
                status="draft",
                embedding=candidate.embedding,
            )
            versions.append(runbook)
            mutation = RunbookMutation(
                "create",
                runbook.runbook_id,
                runbook.version,
                runbook.success_count,
                runbook.failure_count,
                runbook.status,
            )
        else:
            # outcome == "success" and steps match the incumbent → reinforce.
            latest.success_count += 1
            if (
                latest.status == "draft"
                and latest.success_count >= self.reinforcements_to_activate + 1
            ):
                latest.status = "active"
            mutation = RunbookMutation(
                "reinforce",
                latest.runbook_id,
                latest.version,
                latest.success_count,
                latest.failure_count,
                latest.status,
            )

        self.processed[idempotency_key] = mutation
        return mutation


class CockroachRunbookRepository:
    """Direct-SQL writer for idempotent, provenance-backed procedural memory."""

    def __init__(
        self, database_url: str, *, reinforcements_to_activate: int = 2
    ) -> None:
        if reinforcements_to_activate < 1:
            raise ValueError("reinforcements_to_activate must be at least 1")
        self._database_url = database_url
        self._reinforcements_to_activate = reinforcements_to_activate

    def apply(
        self, candidate: CandidateRunbook, idempotency_key: str
    ) -> RunbookMutation:
        # Imported lazily so the deterministic local package has zero dependencies.
        import psycopg

        with psycopg.connect(self._database_url) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT pm.runbook_id, pm.version, pm.success_count,
                               pm.failure_count, pm.status
                        FROM runbook_provenance AS rp
                        JOIN procedural_memory AS pm
                          ON pm.runbook_id = rp.runbook_id
                        WHERE pm.org_id = %s
                          AND pm.agent_id = %s
                          AND pm.name = %s
                          AND rp.incident_id = %s
                        LIMIT 1
                        """,
                        (
                            candidate.org_id,
                            candidate.agent_id,
                            candidate.name,
                            candidate.incident_id,
                        ),
                    )
                    replay = cursor.fetchone()
                    if replay:
                        return RunbookMutation(
                            "noop",
                            str(replay[0]),
                            int(replay[1]),
                            int(replay[2]),
                            int(replay[3]),
                            str(replay[4]),
                            idempotent_replay=True,
                        )

                    cursor.execute(
                        """
                        SELECT runbook_id, version, steps, success_count,
                               failure_count, status
                        FROM procedural_memory
                        WHERE org_id = %s AND agent_id = %s AND name = %s
                        ORDER BY version DESC
                        LIMIT 1
                        FOR UPDATE
                        """,
                        (candidate.org_id, candidate.agent_id, candidate.name),
                    )
                    latest = cursor.fetchone()

                    serialized_steps = json.dumps(candidate.steps, sort_keys=True)
                    existing_steps = None
                    if latest:
                        existing_steps = latest[2]
                        if isinstance(existing_steps, str):
                            existing_steps = json.loads(existing_steps)

                    if candidate.outcome != "success":
                        # A failed / no-effect episode must NEVER create a runbook
                        # or create-and-deprecate the incumbent (memory-poisoning
                        # guard, audit C1). It can only weaken an existing runbook,
                        # or be ignored when there is nothing to weaken.
                        if latest is None:
                            return RunbookMutation(
                                "ignored_counterexample", "", 0, 0, 0, "absent"
                            )
                        runbook_id = str(latest[0])
                        version = int(latest[1])
                        cursor.execute(
                            """
                            UPDATE procedural_memory
                            SET usage_count = usage_count + 1,
                                failure_count = failure_count + 1,
                                success_rate =
                                  success_count::FLOAT8
                                  / (usage_count::FLOAT8 + 1.0),
                                status = CASE
                                  WHEN failure_count + 1 >= success_count
                                  THEN 'deprecated'
                                  ELSE status
                                END,
                                last_used_at = now()
                            WHERE runbook_id = %s
                            RETURNING success_count, failure_count, status
                            """,
                            (runbook_id,),
                        )
                        role, operation = "counterexample", "weaken"
                        counts = cursor.fetchone()
                        success_count, failure_count = int(counts[0]), int(counts[1])
                        status = str(counts[2])
                    elif latest is None or existing_steps != list(candidate.steps):
                        # Only a successful episode reaches here, so a create (and
                        # the deprecation of the prior version) is always sourced
                        # from a proven outcome.
                        version = int(latest[1]) + 1 if latest else 1
                        if latest:
                            cursor.execute(
                                """
                                UPDATE procedural_memory
                                SET status = 'deprecated'
                                WHERE runbook_id = %s
                                """,
                                (latest[0],),
                            )
                        cursor.execute(
                            """
                            INSERT INTO procedural_memory (
                                org_id, agent_id, name, version, status,
                                trigger_desc, embedding, applicable_service_tags,
                                applicable_error_signatures, preconditions, steps,
                                postconditions, usage_count, success_count,
                                failure_count, success_rate, created_by
                            )
                            VALUES (
                                %s, %s, %s, %s, 'draft',
                                %s, %s::VECTOR, %s, %s, %s::JSONB, %s::JSONB,
                                %s::JSONB, 1, 1, 0, 1.0,
                                'consolidation_job'
                            )
                            RETURNING runbook_id
                            """,
                            (
                                candidate.org_id,
                                candidate.agent_id,
                                candidate.name,
                                version,
                                candidate.trigger_desc,
                                _vector_literal(candidate.embedding),
                                list(candidate.service_tags),
                                list(candidate.error_signatures),
                                json.dumps(candidate.preconditions),
                                serialized_steps,
                                json.dumps(candidate.postconditions),
                            ),
                        )
                        runbook_id = str(cursor.fetchone()[0])
                        role = "source"
                        operation = "create"
                        success_count, failure_count = 1, 0
                        status = "draft"
                    else:
                        # outcome == "success" and steps match the incumbent → reinforce.
                        runbook_id = str(latest[0])
                        version = int(latest[1])
                        cursor.execute(
                            """
                            UPDATE procedural_memory
                            SET usage_count = usage_count + 1,
                                success_count = success_count + 1,
                                success_rate =
                                  (success_count::FLOAT8 + 1.0)
                                  / (usage_count::FLOAT8 + 1.0),
                                status = CASE
                                  WHEN status = 'draft'
                                   AND success_count + 1 >= %s
                                  THEN 'active'
                                  ELSE status
                                END,
                                last_used_at = now()
                            WHERE runbook_id = %s
                            RETURNING success_count, failure_count, status
                            """,
                            (
                                self._reinforcements_to_activate + 1,
                                runbook_id,
                            ),
                        )
                        role, operation = "reinforcement", "reinforce"
                        counts = cursor.fetchone()
                        success_count, failure_count = int(counts[0]), int(counts[1])
                        status = str(counts[2])

                    cursor.execute(
                        """
                        INSERT INTO runbook_provenance (
                            runbook_id, incident_id, episodic_event_id, role
                        )
                        VALUES (%s, %s, %s, %s)
                        """,
                        (
                            runbook_id,
                            candidate.incident_id,
                            candidate.source_event_ids[-1],
                            role,
                        ),
                    )
                    return RunbookMutation(
                        operation,
                        runbook_id,
                        version,
                        success_count,
                        failure_count,
                        status,
                    )


def _vector_literal(embedding: tuple[float, ...]) -> str:
    if len(embedding) != 1024:
        raise ValueError("candidate runbook must have a VECTOR(1024) embedding")
    return "[" + ",".join(f"{value:.12g}" for value in embedding) + "]"
