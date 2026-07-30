from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Protocol

from .contracts import ChangeEvent, ModelResult, change_timestamp_datetime


class WindowStore(Protocol):
    def put(self, event: ChangeEvent) -> None: ...

    def read_through(self, watermark: Decimal) -> list[ChangeEvent]: ...

    def mark_processed(self, events: Iterable[ChangeEvent], watermark: Decimal) -> None: ...

    def archive(self, result: ModelResult, idempotency_key: str) -> None: ...


class InMemoryWindowStore:
    def __init__(self) -> None:
        self.pending: dict[str, ChangeEvent] = {}
        self.processed: set[str] = set()
        self.archives: dict[str, ModelResult] = {}
        self.watermark: Decimal | None = None

    def put(self, event: ChangeEvent) -> None:
        if event.deduplication_id not in self.processed:
            self.pending[event.deduplication_id] = event

    def read_through(self, watermark: Decimal) -> list[ChangeEvent]:
        return sorted(
            (
                event
                for event in self.pending.values()
                if event.updated_at <= watermark
            ),
            key=lambda event: (event.updated_at, event.event_id),
        )

    def mark_processed(
        self, events: Iterable[ChangeEvent], watermark: Decimal
    ) -> None:
        for event in events:
            self.processed.add(event.deduplication_id)
            self.pending.pop(event.deduplication_id, None)
        self.watermark = max(self.watermark or watermark, watermark)

    def archive(self, result: ModelResult, idempotency_key: str) -> None:
        self.archives.setdefault(idempotency_key, result)


class S3WindowStore:
    """S3-backed durable buffer; one FIFO consumer preserves watermark order."""

    def __init__(self, *, client: Any, bucket: str, prefix: str = "consolidation") -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.rstrip("/")

    def _pending_key(self, event: ChangeEvent) -> str:
        updated = change_timestamp_datetime(event.updated_at)
        return (
            f"{self._prefix}/pending/{updated.strftime('%Y/%m/%d/%H')}/"
            f"{event.deduplication_id}.json"
        )

    def put(self, event: ChangeEvent) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._pending_key(event),
            Body=json.dumps(event.to_dict(), sort_keys=True).encode("utf-8"),
            ContentType="application/json",
            Metadata={"mvcc-updated": str(event.updated_at)},
        )

    def read_through(self, watermark: Decimal) -> list[ChangeEvent]:
        prefix = f"{self._prefix}/pending/"
        token: str | None = None
        events: list[ChangeEvent] = []
        while True:
            request: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                request["ContinuationToken"] = token
            page = self._client.list_objects_v2(**request)
            for item in page.get("Contents", []):
                response = self._client.get_object(
                    Bucket=self._bucket, Key=item["Key"]
                )
                payload = json.loads(response["Body"].read())
                event = ChangeEvent.from_dict(payload)
                if event.updated_at <= watermark:
                    events.append(event)
            if not page.get("IsTruncated"):
                break
            token = page["NextContinuationToken"]
        return sorted(events, key=lambda event: (event.updated_at, event.event_id))

    def mark_processed(
        self, events: Iterable[ChangeEvent], watermark: Decimal
    ) -> None:
        event_list = list(events)
        for start in range(0, len(event_list), 1_000):
            chunk = event_list[start : start + 1_000]
            self._client.delete_objects(
                Bucket=self._bucket,
                Delete={
                    "Objects": [{"Key": self._pending_key(event)} for event in chunk],
                    "Quiet": True,
                },
            )
        self._client.put_object(
            Bucket=self._bucket,
            Key=f"{self._prefix}/state/watermark.json",
            Body=json.dumps({"watermark": str(watermark)}).encode("utf-8"),
            ContentType="application/json",
        )

    def archive(self, result: ModelResult, idempotency_key: str) -> None:
        payload = {
            "idempotency_key": idempotency_key,
            "model_id": result.model_id,
            "prompt": result.prompt,
            "raw_response": result.raw_response,
            "candidate": {
                "name": result.candidate.name,
                "incident_id": result.candidate.incident_id,
                "outcome": result.candidate.outcome,
                "steps_hash": result.candidate.steps_hash,
                "embedding_dimensions": len(result.candidate.embedding),
                "source_event_ids": result.candidate.source_event_ids,
            },
            "archived_at": datetime.now(timezone.utc).isoformat(),
        }
        self._client.put_object(
            Bucket=self._bucket,
            Key=f"{self._prefix}/archive/{idempotency_key}.json",
            Body=json.dumps(payload, sort_keys=True).encode("utf-8"),
            ContentType="application/json",
        )
