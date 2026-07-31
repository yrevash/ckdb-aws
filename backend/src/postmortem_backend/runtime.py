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
from .guardrails.roles import DatabaseRole, RoleScopedProvider
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
    # Role-scoped SQL identities (charter R7/T2): the act/outcome path dials the
    # scoped writer, the recall path dials the read-only reader. When a single
    # DSN is configured both wrap the same underlying pool, but each carries its
    # role so the adapters refuse a cross-wiring in-process (guardrails.roles).
    writer_dsn = settings.writer_database_url or settings.database_url or ""
    reader_dsn = settings.reader_database_url or settings.database_url or ""
    writer_pool = PsycopgPoolProvider(writer_dsn)
    if reader_dsn == writer_dsn:
        reader_raw = writer_pool
        resources: tuple[Any, ...] = (writer_pool,)
    else:
        reader_raw = PsycopgPoolProvider(reader_dsn)
        resources = (writer_pool, reader_raw)
    writer_provider = RoleScopedProvider(
        writer_pool, DatabaseRole.WRITER, identity="postmortem_agent_writer"
    )
    reader_provider = RoleScopedProvider(
        reader_raw, DatabaseRole.READER, identity="postmortem_agent_reader"
    )

    if settings.recall_backend == "sql":
        recall = CockroachRecallAdapter(reader_provider)
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
        remediation=CockroachAtomicRemediationStore(writer_provider),
        events=events,
    )
    outcomes = OutcomeService(
        embedder=embedder,
        outcomes=CockroachOutcomeStore(writer_provider),
        events=events,
    )
    return Runtime(
        settings=settings,
        responder=responder,
        outcomes=outcomes,
        events=events,
        resources=resources,
    )
