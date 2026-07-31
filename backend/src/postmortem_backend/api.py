"""FastAPI transport for responder turns and console event streaming."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .config import Settings
from .domain import IncidentSignal, OutcomeCommand, OutcomeKind
from .errors import (
    AtomicRemediationError,
    InputValidationError,
    OutcomeRecordingError,
    PostmortemError,
    PromptInjectionDetected,
    ReasoningError,
    RecallError,
    WebhookAuthenticationError,
)
from .guardrails.validation import WebhookAuthenticator, validate_changefeed_body
from .runtime import Runtime, build_runtime
from .transport import console_region, sse_message


class RespondRequest(BaseModel):
    session_id: UUID
    service_id: UUID
    severity: str = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=8_000)
    error_signature: str | None = Field(default=None, max_length=500)
    service_tags: list[str] = Field(default_factory=list, max_length=50)
    metadata: dict[str, object] = Field(default_factory=dict)
    approved: bool = False
    approved_by: str | None = Field(default=None, max_length=254)
    cold_start: bool = False


class OutcomeRequest(BaseModel):
    action_id: UUID
    service_id: UUID
    outcome: OutcomeKind
    summary: str = Field(min_length=1, max_length=8_000)
    error_signature: str | None = Field(default=None, max_length=500)
    observed_at: datetime | None = None


def create_app(
    settings: Settings | None = None, runtime: Runtime | None = None
) -> FastAPI:
    selected_settings = settings or Settings.from_env()
    selected_runtime = runtime or build_runtime(selected_settings)
    webhook_auth = WebhookAuthenticator(selected_settings.changefeed_webhook_secret)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        selected_runtime.close()

    app = FastAPI(
        title="Postmortem responder",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.runtime = selected_runtime
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(selected_settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["content-type"],
    )

    @app.exception_handler(WebhookAuthenticationError)
    async def webhook_auth_error(
        _: Request, exc: WebhookAuthenticationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    @app.exception_handler(InputValidationError)
    async def input_validation_error(
        _: Request, exc: InputValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    @app.exception_handler(PromptInjectionDetected)
    async def prompt_injection_error(
        _: Request, exc: PromptInjectionDetected
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    # Infra/upstream failures (audit backend#5): these are NOT the caller's
    # fault and are not a genuine data conflict -- they mean the reasoner or
    # CockroachDB itself failed, timed out, or exhausted its retries. Mapping
    # them to 409 (as the generic PostmortemError handler below does) told
    # callers/retriers "your request conflicted with existing state," which
    # is wrong and actively discourages the retry these failures usually
    # warrant. 502 (upstream/model returned something unusable) and 503
    # (CockroachDB/transaction infra unavailable) are both retryable-signal
    # codes; genuine conflicts (OutcomeConflict, ProvenanceError,
    # ApprovalRequired/DestructiveActionBlocked -- provenance/approval
    # rejections) are intentionally left on the generic 409 handler below.
    @app.exception_handler(ReasoningError)
    async def reasoning_error(_: Request, exc: ReasoningError) -> JSONResponse:
        return JSONResponse(
            status_code=502,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    @app.exception_handler(RecallError)
    async def recall_error(_: Request, exc: RecallError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    @app.exception_handler(AtomicRemediationError)
    async def atomic_remediation_error(
        _: Request, exc: AtomicRemediationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    @app.exception_handler(OutcomeRecordingError)
    async def outcome_recording_error(
        _: Request, exc: OutcomeRecordingError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    @app.exception_handler(PostmortemError)
    async def postmortem_error(_: Request, exc: PostmortemError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"error": type(exc).__name__, "message": str(exc)},
        )

    @app.get("/healthz")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "runtimeMode": selected_settings.runtime_mode,
            "recallBackend": selected_settings.recall_backend,
            "coldStart": selected_settings.cold_start,
            "components": {
                "responder": "ready",
                "eventStream": "ready",
                "cockroachdb": (
                    "configured" if selected_settings.runtime_mode == "aws" else "fake"
                ),
                "bedrock": (
                    "configured" if selected_settings.runtime_mode == "aws" else "fake"
                ),
            },
        }

    app.add_api_route("/health", health, methods=["GET"], include_in_schema=False)

    @app.post("/v1/incidents/{incident_id}/respond")
    def respond(incident_id: UUID, body: RespondRequest) -> object:
        signal = IncidentSignal(
            incident_id=incident_id,
            session_id=body.session_id,
            org_id=selected_settings.org_id,
            agent_id=selected_settings.agent_id,
            service_id=body.service_id,
            severity=body.severity,
            summary=body.summary,
            error_signature=body.error_signature,
            service_tags=tuple(body.service_tags),
            metadata={**body.metadata, "cold_start": body.cold_start},
        )
        result = selected_runtime.responder.handle(
            signal, approved=body.approved, approver=body.approved_by
        )
        return jsonable_encoder(asdict(result))

    @app.post("/v1/changefeed")
    async def changefeed(request: Request) -> object:
        # Authenticated changefeed ingest (R9): CockroachDB's webhook sink signs
        # the raw body with a shared secret; verify HMAC-SHA256 before parsing,
        # then structurally validate the envelope. Fails closed on a missing/bad
        # signature (401) or a malformed body (400).
        raw = await request.body()
        signature = request.headers.get(
            "X-Postmortem-Signature"
        ) or request.headers.get("X-Signature")
        webhook_auth.authenticate(raw, signature)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise InputValidationError("Changefeed body was not valid JSON.") from exc
        if not isinstance(data, dict):
            raise InputValidationError("Changefeed body must be a JSON object.")
        envelope = validate_changefeed_body(data)
        return {"accepted": len(envelope.payload)}

    @app.get("/v1/incidents/{incident_id}")
    def incident(incident_id: UUID) -> object:
        history = selected_runtime.events.history(incident_id)
        view = selected_runtime.responder.registry.view(incident_id, len(history))
        if view is None:
            raise HTTPException(status_code=404, detail="Incident not found.")
        return view.to_dict()

    @app.post("/v1/incidents/{incident_id}/outcomes")
    def record_outcome(incident_id: UUID, body: OutcomeRequest) -> object:
        result = selected_runtime.outcomes.record(
            OutcomeCommand(
                org_id=selected_settings.org_id,
                agent_id=selected_settings.agent_id,
                incident_id=incident_id,
                service_id=body.service_id,
                action_id=body.action_id,
                outcome=body.outcome,
                summary=body.summary,
                error_signature=body.error_signature,
                observed_at=body.observed_at or datetime.now(UTC),
            )
        )
        return jsonable_encoder(asdict(result))

    @app.get("/v1/incidents/{incident_id}/events")
    def events(incident_id: UUID) -> StreamingResponse:
        def event_stream() -> Iterator[str]:
            sequence = 0
            for event in selected_runtime.events.stream(incident_id):
                if event is None:
                    yield ": heartbeat\n\n"
                    continue
                sequence += 1
                yield sse_message(
                    event,
                    sequence=sequence,
                    agent_id=str(selected_settings.agent_id),
                    agent_region=console_region(selected_settings.aws_region),
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app
