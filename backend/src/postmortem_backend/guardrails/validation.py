"""External-input validation + authenticated changefeed webhook (charter R9).

Everything that crosses the trust boundary from outside the process -- alert
payloads, the CockroachDB changefeed webhook body, API request bodies -- is
validated and typed before use, and rejected when malformed. The changefeed
webhook is additionally *authenticated* with a shared secret (HMAC-SHA256 over
the raw body, constant-time compared), because an unauthenticated webhook is an
open door to inject fabricated "episode committed" events into the consolidation
path (a memory-poisoning vector, charter T3).

Typing is done with pydantic (already a FastAPI dependency), so the same models
validate whether the input arrives over HTTP or is handed in programmatically.
Free-text fields on these models are additionally screened for prompt injection
via ``guardrails.injection``.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator

from ..errors import InputValidationError, WebhookAuthenticationError
from .injection import guard_untrusted_text

_SIGNATURE_PREFIXES = ("sha256=", "sha256:")


class AlertPayload(BaseModel):
    """A validated inbound alert (EventBridge / monitoring webhook).

    Deny-by-default: unknown fields are rejected, lengths are bounded, and the
    free-text ``summary``/``error_signature`` are injection-screened.
    """

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    service_id: UUID
    severity: str = Field(min_length=1, max_length=20)
    summary: str = Field(min_length=1, max_length=8_000)
    error_signature: str | None = Field(default=None, max_length=500)
    service_tags: tuple[str, ...] = Field(default=(), max_length=50)
    source: str = Field(default="alert", max_length=64)

    @field_validator("summary")
    @classmethod
    def _screen_summary(cls, value: str) -> str:
        return guard_untrusted_text(value, field="summary") or value

    @field_validator("error_signature")
    @classmethod
    def _screen_signature(cls, value: str | None) -> str | None:
        return guard_untrusted_text(value, field="error_signature")

    @field_validator("service_tags")
    @classmethod
    def _screen_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            guard_untrusted_text(tag, field="service_tags") or "" for tag in value
        )


class ChangefeedRow(BaseModel):
    """One row envelope from a CockroachDB changefeed webhook payload."""

    model_config = {"extra": "allow"}

    key: list[Any] | None = None
    after: dict[str, Any] | None = None
    updated: str | None = None
    topic: str | None = None


class ChangefeedEnvelope(BaseModel):
    """The batched body CockroachDB POSTs to a webhook changefeed sink.

    CockroachDB's ``webhook-https`` sink posts ``{"payload": [ {row}, ... ],
    "length": N}``. We validate that shape and cap the batch size so a malformed
    or hostile body is rejected before any row is processed.
    """

    model_config = {"extra": "forbid"}

    payload: list[ChangefeedRow] = Field(max_length=10_000)
    length: int | None = None

    @field_validator("length")
    @classmethod
    def _length_consistent(cls, value: int | None, info: Any) -> int | None:
        if value is not None and value < 0:
            raise ValueError("length cannot be negative")
        return value


def validate_alert(data: dict[str, Any]) -> AlertPayload:
    """Validate a raw alert dict, mapping any failure to :class:`InputValidationError`."""

    try:
        return AlertPayload.model_validate(data)
    except ValidationError as exc:
        raise InputValidationError(f"Malformed alert payload: {exc.errors()}") from exc


def validate_changefeed_body(data: dict[str, Any]) -> ChangefeedEnvelope:
    """Validate a raw changefeed webhook body."""

    try:
        return ChangefeedEnvelope.model_validate(data)
    except ValidationError as exc:
        raise InputValidationError(
            f"Malformed changefeed webhook body: {exc.errors()}"
        ) from exc


def compute_hmac_signature(secret: str, body: bytes) -> str:
    """Return the ``sha256=<hex>`` HMAC of ``body`` under ``secret``."""

    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_hmac_signature(secret: str, body: bytes, provided: str | None) -> None:
    """Authenticate a webhook body against its HMAC signature header.

    Constant-time comparison (``hmac.compare_digest``) to avoid timing oracles.
    A missing secret is a *server misconfiguration* and fails closed; a missing
    or mismatched signature fails closed too. Accepts an optional ``sha256=`` /
    ``sha256:`` prefix on the provided header.
    """

    if not secret:
        raise WebhookAuthenticationError(
            "Changefeed webhook secret is not configured; refusing unauthenticated "
            "ingest (fail closed, R9)."
        )
    if not provided:
        raise WebhookAuthenticationError("Missing changefeed webhook signature.")
    candidate = provided.strip()
    for prefix in _SIGNATURE_PREFIXES:
        if candidate.lower().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    expected = compute_hmac_signature(secret, body).split("=", 1)[1]
    if not hmac.compare_digest(expected, candidate):
        raise WebhookAuthenticationError("Changefeed webhook signature mismatch.")


class WebhookAuthenticator:
    """Reusable authenticator binding a shared secret to the verify + validate step."""

    __slots__ = ("_secret",)

    def __init__(self, secret: str | None) -> None:
        self._secret = secret or ""

    @property
    def configured(self) -> bool:
        return bool(self._secret)

    def authenticate(self, body: bytes, signature: str | None) -> None:
        verify_hmac_signature(self._secret, body, signature)
