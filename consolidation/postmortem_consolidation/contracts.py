from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Mapping, Sequence

RunbookOutcome = Literal["success", "failed", "no_effect"]


def _parse_timestamp(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty ISO-8601 string")
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_change_timestamp(value: object, field_name: str) -> Decimal:
    """Parse CockroachDB HLC (`wall_time.logical`) or an ISO fixture timestamp."""

    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a CockroachDB HLC or ISO-8601 string")
    try:
        return Decimal(value)
    except InvalidOperation:
        parsed = _parse_timestamp(value, field_name)
        since_epoch = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
        wall_time_ns = (
            since_epoch.days * 86_400_000_000_000
            + since_epoch.seconds * 1_000_000_000
            + since_epoch.microseconds * 1_000
        )
        return Decimal(wall_time_ns)


def change_timestamp_datetime(value: Decimal) -> datetime:
    wall_time_ns = int(value)
    seconds, remainder_ns = divmod(wall_time_ns, 1_000_000_000)
    return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
        microsecond=remainder_ns // 1_000
    )


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class ChangeEvent:
    """Normalized episodic row emitted by a CockroachDB changefeed."""

    event_id: str
    org_id: str
    agent_id: str
    incident_id: str
    service_id: str
    event_type: str
    content: str
    occurred_at: datetime
    updated_at: Decimal
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_changefeed(cls, envelope: Mapping[str, Any]) -> ChangeEvent:
        after = envelope.get("after")
        if not isinstance(after, Mapping):
            raise ValueError("changefeed row must include an object-valued 'after'")

        updated = envelope.get("updated") or after.get("created_at")
        metadata = after.get("metadata") or {}
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        if not isinstance(metadata, Mapping):
            raise ValueError("after.metadata must be an object")

        return cls(
            event_id=_required_text(after.get("event_id"), "after.event_id"),
            org_id=_required_text(after.get("org_id"), "after.org_id"),
            agent_id=_required_text(after.get("agent_id"), "after.agent_id"),
            incident_id=_required_text(after.get("incident_id"), "after.incident_id"),
            service_id=_required_text(after.get("service_id"), "after.service_id"),
            event_type=_required_text(after.get("event_type"), "after.event_type"),
            content=str(after.get("content") or ""),
            occurred_at=_parse_timestamp(
                after.get("occurred_at"), "after.occurred_at"
            ),
            updated_at=_parse_change_timestamp(updated, "updated"),
            metadata=dict(metadata),
        )

    @property
    def deduplication_id(self) -> str:
        value = f"row:{self.event_id}:{self.updated_at}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["occurred_at"] = self.occurred_at.isoformat()
        result["updated_at"] = str(self.updated_at)
        result["kind"] = "row"
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ChangeEvent:
        return cls(
            event_id=_required_text(value.get("event_id"), "event_id"),
            org_id=_required_text(value.get("org_id"), "org_id"),
            agent_id=_required_text(value.get("agent_id"), "agent_id"),
            incident_id=_required_text(value.get("incident_id"), "incident_id"),
            service_id=_required_text(value.get("service_id"), "service_id"),
            event_type=_required_text(value.get("event_type"), "event_type"),
            content=str(value.get("content") or ""),
            occurred_at=_parse_timestamp(value.get("occurred_at"), "occurred_at"),
            updated_at=_parse_change_timestamp(value.get("updated_at"), "updated_at"),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ClosedWindow:
    """Resolved timestamp: all changes at or before this time were delivered."""

    watermark: Decimal

    @classmethod
    def from_changefeed(cls, envelope: Mapping[str, Any]) -> ClosedWindow:
        return cls(_parse_change_timestamp(envelope.get("resolved"), "resolved"))

    @property
    def deduplication_id(self) -> str:
        value = f"window:{self.watermark}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, str]:
        return {"kind": "window_closed", "watermark": str(self.watermark)}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ClosedWindow:
        return cls(_parse_change_timestamp(value.get("watermark"), "watermark"))


QueueMessage = ChangeEvent | ClosedWindow


def parse_changefeed_body(value: object) -> list[QueueMessage]:
    """Accept CockroachDB webhook batches and individual resolved messages."""

    if not isinstance(value, Mapping):
        raise ValueError("changefeed request body must be a JSON object")

    raw_items = value.get("payload", value)
    if isinstance(raw_items, Mapping):
        items: Sequence[object] = [raw_items]
    elif isinstance(raw_items, Sequence) and not isinstance(
        raw_items, (str, bytes, bytearray)
    ):
        items = raw_items
    else:
        raise ValueError("changefeed payload must be an object or array")

    messages: list[QueueMessage] = []
    for raw in items:
        if not isinstance(raw, Mapping):
            raise ValueError("each changefeed payload item must be an object")
        if raw.get("resolved") is not None:
            messages.append(ClosedWindow.from_changefeed(raw))
        elif raw.get("after") is not None:
            messages.append(ChangeEvent.from_changefeed(raw))
        # Tombstones and metadata-only records are deliberately ignored.
    return messages


def parse_queue_message(value: Mapping[str, Any]) -> QueueMessage:
    kind = value.get("kind")
    if kind == "row":
        return ChangeEvent.from_dict(value)
    if kind == "window_closed":
        return ClosedWindow.from_dict(value)
    raise ValueError(f"unsupported queue message kind: {kind!r}")


@dataclass(frozen=True)
class EpisodeGroup:
    org_id: str
    agent_id: str
    incident_id: str
    service_id: str
    episodes: tuple[ChangeEvent, ...]
    outcome: RunbookOutcome

    @property
    def idempotency_key(self) -> str:
        source = "|".join(sorted(event.event_id for event in self.episodes))
        return hashlib.sha256(
            f"{self.org_id}:{self.incident_id}:{source}".encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True)
class CandidateRunbook:
    org_id: str
    agent_id: str
    incident_id: str
    service_id: str
    name: str
    trigger_desc: str
    steps: tuple[Mapping[str, Any], ...]
    preconditions: tuple[Mapping[str, Any], ...]
    postconditions: tuple[Mapping[str, Any], ...]
    service_tags: tuple[str, ...]
    error_signatures: tuple[str, ...]
    outcome: RunbookOutcome
    source_event_ids: tuple[str, ...]
    embedding: tuple[float, ...] = ()

    @property
    def steps_hash(self) -> str:
        serialized = json.dumps(self.steps, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ModelResult:
    candidate: CandidateRunbook
    model_id: str
    prompt: str
    raw_response: str
