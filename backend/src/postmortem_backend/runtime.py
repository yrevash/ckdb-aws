"""Dependency composition for offline and AWS modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters.bedrock import (
    BedrockReasoningAdapter,
    StrandsReasoningAdapter,
    TitanEmbeddingAdapter,
)
from .adapters.cockroach import CockroachAtomicRemediationStore, PsycopgPoolProvider
from .adapters.fakes import (
    FakeAtomicRemediationStore,
    FakeEmbeddingAdapter,
    FakeReasoningAdapter,
    FakeRecallAdapter,
    phase_one_demo_memory,
)
from .adapters.mcp import ManagedMCPRecallAdapter, StreamableHttpMCPTransport
from .adapters.outcome import CockroachOutcomeStore
from .adapters.recall import CockroachRecallAdapter
from .config import Settings
from .events import EventBroker
from .service import OutcomeService, ResponderService
from .recall import ColdStartRecallAdapter


@dataclass(slots=True)
class Runtime:
    settings: Settings
    responder: ResponderService
    outcomes: OutcomeService
    events: EventBroker
    resources: tuple[Any, ...] = ()

    def close(self) -> None:
        for resource in self.resources:
            close = getattr(resource, "close", None)
            if close is not None:
                close()


def build_runtime(settings: Settings) -> Runtime:
    events = EventBroker()
    if settings.runtime_mode == "fake":
        recall = FakeRecallAdapter(phase_one_demo_memory())
        if settings.cold_start:
            recall = ColdStartRecallAdapter(recall)
        store = FakeAtomicRemediationStore(auto_seed=True)
        responder = ResponderService(
            embedder=FakeEmbeddingAdapter(),
            recall=recall,
            reasoner=FakeReasoningAdapter(),
            remediation=store,
            events=events,
        )
        outcomes = OutcomeService(
            embedder=FakeEmbeddingAdapter(),
            outcomes=store,
            events=events,
        )
        return Runtime(
            settings=settings,
            responder=responder,
            outcomes=outcomes,
            events=events,
        )

    embedder = TitanEmbeddingAdapter(
        region=settings.aws_region,
        model_id=settings.embedding_model_id,
    )
    if settings.reasoner == "strands":
        reasoner = StrandsReasoningAdapter(
            region=settings.aws_region,
            model_id=settings.reasoning_model_id,
        )
    else:
        reasoner = BedrockReasoningAdapter(
            region=settings.aws_region,
            model_id=settings.reasoning_model_id,
        )
    pool = PsycopgPoolProvider(settings.database_url or "")
    if settings.recall_backend == "sql":
        recall = CockroachRecallAdapter(pool)
    else:
        transport = StreamableHttpMCPTransport(
            settings.mcp_url or "",
            settings.mcp_token or "",
        )
        recall = ManagedMCPRecallAdapter(transport)
    if settings.cold_start:
        recall = ColdStartRecallAdapter(recall)
    responder = ResponderService(
        embedder=embedder,
        recall=recall,
        reasoner=reasoner,
        remediation=CockroachAtomicRemediationStore(pool),
        events=events,
    )
    outcomes = OutcomeService(
        embedder=embedder,
        outcomes=CockroachOutcomeStore(pool),
        events=events,
    )
    return Runtime(
        settings=settings,
        responder=responder,
        outcomes=outcomes,
        events=events,
        resources=(pool,),
    )
