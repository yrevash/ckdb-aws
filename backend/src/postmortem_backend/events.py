"""In-process event broker backing the Phase 1 SSE contract."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from queue import Empty, Queue
from threading import RLock
from uuid import UUID

from .domain import AgentEvent


class EventBroker:
    def __init__(self) -> None:
        self._history: dict[UUID, list[AgentEvent]] = defaultdict(list)
        self._subscribers: dict[UUID, set[Queue[AgentEvent]]] = defaultdict(set)
        self._lock = RLock()

    def publish(self, event: AgentEvent) -> None:
        with self._lock:
            self._history[event.incident_id].append(event)
            subscribers = tuple(self._subscribers[event.incident_id])
        for subscriber in subscribers:
            subscriber.put_nowait(event)

    def history(self, incident_id: UUID) -> tuple[AgentEvent, ...]:
        with self._lock:
            return tuple(self._history.get(incident_id, ()))

    def stream(
        self, incident_id: UUID, *, heartbeat_seconds: float = 15.0
    ) -> Iterator[AgentEvent | None]:
        subscriber: Queue[AgentEvent] = Queue()
        with self._lock:
            self._subscribers[incident_id].add(subscriber)
            history = tuple(self._history.get(incident_id, ()))
        try:
            yield from history
            while True:
                try:
                    yield subscriber.get(timeout=heartbeat_seconds)
                except Empty:
                    yield None
        finally:
            with self._lock:
                self._subscribers[incident_id].discard(subscriber)
                if not self._subscribers[incident_id]:
                    self._subscribers.pop(incident_id, None)
