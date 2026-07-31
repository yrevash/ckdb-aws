"""Dependency composition for offline and AWS modes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .adapters.bedrock import (
    BedrockReasoningAdapter,
    StrandsReasoningAdapter,
    TitanEmbeddingAdapter,
)
from .adapters.cockroach import (
    CockroachAtomicRemediationStore,
    ConnectionProvider,
    PsycopgPoolProvider,
)
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
from .errors import RoleScopeViolation
from .guardrails.provenance import ProvenanceGuardedRemediation
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
            # Wrap the act path in the provenance guard (audit C5): the
            # wrapper was defined in guardrails/provenance.py but never
            # actually wired into either runtime mode, so the "every act
            # path is forced through the same citation check" guarantee its
            # own docstring advertises was dead code. `recalled_ids=None`
            # here means it enforces only the baseline "non-nil citation"
            # check (service.py's own call to require_grounded_action still
            # does the stronger per-turn "citation must be among what THIS
            # turn recalled" check, since only it knows the turn's recalled
            # ids) -- a structural backstop against any future/alternate act
            # path that doesn't go through service.py's own gate.
            remediation=ProvenanceGuardedRemediation(store),
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
    # scoped writer, the recall path dials the read-only reader.
    #
    # Audit C3 ("role-scoping cosmetic under single DSN"): the pre-fix code
    # let reader_dsn == writer_dsn silently fall back to sharing ONE pool
    # for both roles. guardrails.roles' in-process guard only refuses a
    # provider explicitly tagged with the wrong DatabaseRole -- it cannot
    # catch "both roles point at the same underlying DB principal," because
    # in that fallback both RoleScopedProvider wrappers legitimately wrap
    # the identical pool. That made the reader/writer split cosmetic
    # whenever the two DSN env vars weren't both set to genuinely distinct
    # service-account DSNs. Fail closed instead: AWS runtime mode now
    # REQUIRES two DSNs that are both textually distinct AND (so far as a
    # DSN can say without opening a connection) authenticate as distinct SQL
    # users. See db/migrations/0007_audit_logging.sql for the
    # postmortem_agent_reader / postmortem_agent_writer service accounts
    # this is meant to enforce use of.
    writer_dsn = settings.writer_database_url or settings.database_url or ""
    reader_dsn = settings.reader_database_url or settings.database_url or ""
    if not writer_dsn or not reader_dsn:
        raise RoleScopeViolation(
            "AWS runtime mode requires a resolvable reader and writer "
            "database URL (R7/T2)."
        )
    writer_user = urlsplit(writer_dsn).username
    reader_user = urlsplit(reader_dsn).username
    if reader_dsn == writer_dsn or (
        reader_user is not None and reader_user == writer_user
    ):
        raise RoleScopeViolation(
            "AWS runtime mode refuses identical or same-principal reader/"
            "writer database URLs (audit C3): role separation is cosmetic "
            "if the recall path and the act path dial the same DB user. "
            "Set distinct POSTMORTEM_READER_DATABASE_URL / "
            "POSTMORTEM_WRITER_DATABASE_URL (postmortem_agent_reader / "
            "postmortem_agent_writer -- db/migrations/0007_audit_logging.sql)."
        )
    writer_pool = PsycopgPoolProvider(writer_dsn)
    reader_raw = PsycopgPoolProvider(reader_dsn)
    resources: tuple[Any, ...] = (writer_pool, reader_raw)
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
        # See the fake-mode branch above (audit C5) for why this wrapping
        # matters even though service.py already runs its own,
        # turn-scoped provenance check.
        remediation=ProvenanceGuardedRemediation(
            CockroachAtomicRemediationStore(writer_provider)
        ),
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


def verify_distinct_database_identities(
    reader_provider: ConnectionProvider, writer_provider: ConnectionProvider
) -> None:
    """Best-effort, CONNECTION-level confirmation that the reader and writer
    pools actually authenticate as two different SQL principals (audit C3,
    "ideally verify current_user at pool init").

    ``build_runtime``'s DSN-string check above (identical DSN, or the same
    embedded username) catches the common misconfiguration without opening a
    connection. This function catches the residual case a string comparison
    cannot: two DIFFERENT DSNs that nonetheless resolve to the same
    underlying role once actually connected (a connection pooler/proxy that
    maps both to one shared service account, a DSN alias, etc).

    DEPLOY-TIME ONLY. This opens two real connections and is intentionally
    NOT called from ``build_runtime`` -- doing so would make every process
    startup (including the offline test suite) depend on live network
    access to CockroachDB. Call it once after ``build_runtime`` in AWS mode,
    e.g. from a startup health check / smoke test script, not on the
    request path.
    """

    def _current_user(provider: ConnectionProvider) -> str:
        with provider() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_user")
                row = cursor.fetchone()
        return str(row[0]) if row else ""

    reader_identity = _current_user(reader_provider)
    writer_identity = _current_user(writer_provider)
    if reader_identity and reader_identity == writer_identity:
        raise RoleScopeViolation(
            f"Reader and writer pools both authenticate as "
            f"'{reader_identity}' (audit C3): role separation is cosmetic "
            f"when both roles share one DB principal, even with distinct "
            f"DSNs."
        )
