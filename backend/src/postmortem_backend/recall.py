"""Three-stage memory recall policy: ANN candidates → safety filters → rerank."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import exp
from typing import Any

from .domain import MemoryCandidate, MemoryKind, RecallBundle, RecallQuery
from .ports import RecallPort


@dataclass(frozen=True, slots=True)
class RecallPolicy:
    """Tunable retrieval policy; defaults favor abstention over unsafe reuse."""

    candidate_multiplier: int = 4
    max_candidate_limit: int = 40
    max_episodes: int = 3
    max_facts: int = 10
    max_runbooks: int = 3
    min_episode_similarity: float = 0.65
    min_fact_similarity: float = 0.60
    min_runbook_similarity: float = 0.72
    min_fact_confidence: float = 0.65
    min_runbook_success_rate: float = 0.60
    min_runbook_usage_count: int = 1
    min_positive_provenance: int = 1
    max_counterexample_ratio: float = 0.50
    max_episode_age: timedelta = timedelta(days=365)
    recency_half_life: timedelta = timedelta(days=90)

    def candidate_limit(self, requested_k: int) -> int:
        return min(
            max(max(requested_k, 1) * self.candidate_multiplier, 20),
            self.max_candidate_limit,
        )


class RecallRanker:
    """Applies structured eligibility gates and deterministic diversity-aware ranking."""

    def __init__(self, policy: RecallPolicy | None = None) -> None:
        self.policy = policy or RecallPolicy()

    def rank(
        self,
        query: RecallQuery,
        *,
        episodes: tuple[MemoryCandidate, ...] = (),
        facts: tuple[MemoryCandidate, ...] = (),
        runbooks: tuple[MemoryCandidate, ...] = (),
    ) -> RecallBundle:
        if query.cold_start:
            return RecallBundle(
                cold_start=True,
                diagnostics={"mode": "cold_start", "database_queries": 0},
            )

        accepted_episodes = [
            self._score_episode(query, item)
            for item in episodes
            if self._episode_eligible(query, item)
        ]
        accepted_facts = [
            self._score_fact(query, item)
            for item in facts
            if self._fact_eligible(query, item)
        ]
        accepted_runbooks = [
            self._score_runbook(query, item)
            for item in runbooks
            if self._runbook_eligible(query, item)
        ]

        ranked_episodes = self._diverse(
            accepted_episodes,
            limit=min(query.k, self.policy.max_episodes),
            key=lambda item: str(
                item.metadata.get("source_case_id")
                or item.metadata.get("incident_id")
                or item.memory_id
            ),
        )
        ranked_facts = self._diverse(
            accepted_facts,
            limit=self.policy.max_facts,
            key=lambda item: (
                str(item.metadata.get("subject", "")),
                str(item.metadata.get("predicate", "")),
            ),
        )
        ranked_runbooks = self._diverse(
            accepted_runbooks,
            limit=self.policy.max_runbooks,
            key=lambda item: str(item.metadata.get("name") or item.memory_id),
        )
        return RecallBundle(
            episodes=tuple(ranked_episodes),
            facts=tuple(ranked_facts),
            runbooks=tuple(ranked_runbooks),
            diagnostics={
                "mode": "memory",
                "candidate_counts": {
                    "episodic": len(episodes),
                    "semantic": len(facts),
                    "procedural": len(runbooks),
                },
                "eligible_counts": {
                    "episodic": len(accepted_episodes),
                    "semantic": len(accepted_facts),
                    "procedural": len(accepted_runbooks),
                },
                "thresholds": {
                    "episode_similarity": self.policy.min_episode_similarity,
                    "fact_similarity": self.policy.min_fact_similarity,
                    "runbook_similarity": self.policy.min_runbook_similarity,
                    "fact_confidence": self.policy.min_fact_confidence,
                    "runbook_success_rate": self.policy.min_runbook_success_rate,
                    "positive_provenance": self.policy.min_positive_provenance,
                },
            },
        )

    def _scope_valid(self, query: RecallQuery, item: MemoryCandidate) -> bool:
        if item.org_id is not None and item.org_id != query.org_id:
            return False
        if item.agent_id is not None and item.agent_id != query.agent_id:
            return False
        return item.service_id is None or item.service_id == query.service_id

    def _temporally_valid(self, query: RecallQuery, item: MemoryCandidate) -> bool:
        as_of = _aware(query.as_of)
        if item.valid_from is not None and _aware(item.valid_from) > as_of:
            return False
        if item.valid_to is not None and _aware(item.valid_to) <= as_of:
            return False
        if item.recorded_at is not None and _aware(item.recorded_at) > as_of:
            return False
        return item.occurred_at is None or _aware(item.occurred_at) <= as_of

    def _episode_eligible(self, query: RecallQuery, item: MemoryCandidate) -> bool:
        if item.kind is not MemoryKind.EPISODIC:
            return False
        if not self._scope_valid(query, item) or not self._temporally_valid(query, item):
            return False
        if item.similarity < self.policy.min_episode_similarity:
            return False
        if item.occurred_at is not None:
            age = _aware(query.as_of) - _aware(item.occurred_at)
            if age > self.policy.max_episode_age:
                return False
        return True

    def _fact_eligible(self, query: RecallQuery, item: MemoryCandidate) -> bool:
        return (
            item.kind is MemoryKind.SEMANTIC
            and self._scope_valid(query, item)
            and self._temporally_valid(query, item)
            and item.similarity >= self.policy.min_fact_similarity
            and item.confidence >= self.policy.min_fact_confidence
        )

    def _runbook_eligible(self, query: RecallQuery, item: MemoryCandidate) -> bool:
        if (
            item.kind is not MemoryKind.PROCEDURAL
            or not self._scope_valid(query, item)
            or not self._temporally_valid(query, item)
            or item.similarity < self.policy.min_runbook_similarity
            or item.success_rate < self.policy.min_runbook_success_rate
        ):
            return False
        usage_count = int(item.metadata.get("usage_count") or 0)
        positive = int(item.metadata.get("positive_provenance_count") or 0)
        counterexamples = int(item.metadata.get("counterexample_count") or 0)
        if usage_count < self.policy.min_runbook_usage_count:
            return False
        if positive < self.policy.min_positive_provenance:
            return False
        if counterexamples / max(positive + counterexamples, 1) > (
            self.policy.max_counterexample_ratio
        ):
            return False

        applicable_tags = {
            str(value) for value in item.metadata.get("applicable_service_tags") or ()
        }
        if applicable_tags and not applicable_tags.intersection(query.service_tags):
            return False
        signatures = {
            str(value)
            for value in item.metadata.get("applicable_error_signatures") or ()
        }
        if signatures and query.error_signature not in signatures:
            return False
        return True

    def _score_episode(
        self, query: RecallQuery, item: MemoryCandidate
    ) -> MemoryCandidate:
        recency = self._recency(query, item.occurred_at)
        successful = 1.0 if item.metadata.get("outcome") == "success" else 0.0
        scope = 1.0 if item.service_id == query.service_id else 0.5
        score = (0.65 * item.similarity) + (0.20 * recency) + (
            0.10 * successful
        ) + (0.05 * scope)
        actionable = bool(
            successful
            and (
                item.runbook_id
                or item.metadata.get("action_id")
                or item.metadata.get("successful_action")
            )
        )
        return self._ranked(
            item,
            score,
            {
                "actionable": actionable,
                "rank_components": {
                    "similarity": item.similarity,
                    "recency": recency,
                    "successful_outcome": successful,
                    "scope": scope,
                },
            },
        )

    def _score_fact(self, query: RecallQuery, item: MemoryCandidate) -> MemoryCandidate:
        recency = self._recency(query, item.recorded_at)
        provenance = min(len(item.provenance_ids), 3) / 3
        score = (
            (0.60 * item.similarity)
            + (0.25 * item.confidence)
            + (0.10 * recency)
            + (0.05 * provenance)
        )
        return self._ranked(
            item,
            score,
            {
                "actionable": False,
                "rank_components": {
                    "similarity": item.similarity,
                    "confidence": item.confidence,
                    "recency": recency,
                    "provenance": provenance,
                },
            },
        )

    def _score_runbook(
        self, query: RecallQuery, item: MemoryCandidate
    ) -> MemoryCandidate:
        recency = self._recency(
            query,
            _datetime(item.metadata.get("last_used_at")) or item.recorded_at,
        )
        positive = int(item.metadata.get("positive_provenance_count") or 0)
        provenance = min(positive, 3) / 3
        scope = self._runbook_scope_score(query, item)
        score = (
            (0.55 * item.similarity)
            + (0.25 * item.success_rate)
            + (0.10 * recency)
            + (0.05 * provenance)
            + (0.05 * scope)
        )
        return self._ranked(
            item,
            score,
            {
                "actionable": True,
                "provenance_verified": True,
                "rank_components": {
                    "similarity": item.similarity,
                    "success_rate": item.success_rate,
                    "recency": recency,
                    "provenance": provenance,
                    "scope": scope,
                },
            },
        )

    def _recency(self, query: RecallQuery, timestamp: datetime | None) -> float:
        if timestamp is None:
            return 0.0
        age_seconds = max(
            (_aware(query.as_of) - _aware(timestamp)).total_seconds(),
            0.0,
        )
        half_life_seconds = self.policy.recency_half_life.total_seconds()
        return exp(-0.69314718056 * age_seconds / half_life_seconds)

    @staticmethod
    def _runbook_scope_score(query: RecallQuery, item: MemoryCandidate) -> float:
        tags = {
            str(value) for value in item.metadata.get("applicable_service_tags") or ()
        }
        signatures = {
            str(value)
            for value in item.metadata.get("applicable_error_signatures") or ()
        }
        tag_score = 1.0 if tags.intersection(query.service_tags) else (0.5 if not tags else 0)
        signature_score = (
            1.0
            if query.error_signature in signatures
            else (0.5 if not signatures else 0)
        )
        return (tag_score + signature_score) / 2

    @staticmethod
    def _ranked(
        item: MemoryCandidate, score: float, metadata: dict[str, Any]
    ) -> MemoryCandidate:
        return replace(
            item,
            ranking_score=score,
            metadata={**item.metadata, **metadata},
        )

    @staticmethod
    def _diverse(
        items: list[MemoryCandidate],
        *,
        limit: int,
        key: Any,
    ) -> list[MemoryCandidate]:
        selected: list[MemoryCandidate] = []
        seen: set[Any] = set()
        for item in sorted(
            items,
            key=lambda candidate: (
                candidate.ranking_score,
                candidate.similarity,
                str(candidate.memory_id),
            ),
            reverse=True,
        ):
            diversity_key = key(item)
            if diversity_key in seen:
                continue
            seen.add(diversity_key)
            selected.append(item)
            if len(selected) >= limit:
                break
        return selected


class ColdStartRecallAdapter:
    """A/B control that deliberately bypasses every persistent-memory read."""

    def __init__(self, delegate: RecallPort | None = None) -> None:
        self._delegate = delegate

    def recall(self, query: RecallQuery) -> RecallBundle:
        return RecallBundle(
            cold_start=True,
            diagnostics={
                "mode": "cold_start",
                "database_queries": 0,
                "delegate_configured": self._delegate is not None,
            },
        )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value:
        return datetime.fromisoformat(str(value))
    return None
