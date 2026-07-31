"""In-process event broker backing the Phase 1 SSE contract."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterator
from queue import Empty, Full, Queue
from threading import RLock
from uuid import UUID

from .domain import AgentEvent

# Default bounds (audit backend#6): with no cap, `_history` grows forever --
# one incident that stays open for a long time (or a long-running
# demo/soak) accumulates an unbounded per-incident event list, and a slow or
# stalled SSE subscriber's queue grows unbounded too if the responder keeps
# publishing faster than the subscriber drains it. Both are now bounded:
# history becomes a per-incident ring buffer (oldest events silently evicted
# once the cap is reached -- `/v1/incidents/{id}` and the SSE backlog replay
# see only the most recent window, which is the same tradeoff every
# bounded audit/event log makes), and a subscriber's queue is capped so a
# stalled consumer sheds new events (best-effort SSE) instead of holding
# memory the broker can never reclaim.
DEFAULT_HISTORY_LIMIT = 500
DEFAULT_SUBSCRIBER_QUEUE_LIMIT = 1_000


class EventBroker:
    def __init__(
        self,
        *,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        subscriber_queue_limit: int = DEFAULT_SUBSCRIBER_QUEUE_LIMIT,
    ) -> None:
        self._history_limit = history_limit
        self._subscriber_queue_limit = subscriber_queue_limit
        self._history: dict[UUID, deque[AgentEvent]] = defaultdict(
            lambda: deque(maxlen=history_limit)
        )
        self._subscribers: dict[UUID, set[Queue[AgentEvent]]] = defaultdict(set)
        self._lock = RLock()

    def publish(self, event: AgentEvent) -> None:
        with self._lock:
            # A deque with maxlen silently evicts the oldest entry once full
            # -- the ring-buffer behavior that caps `_history`'s growth.
            self._history[event.incident_id].append(event)
            subscribers = tuple(self._subscribers[event.incident_id])
        for subscriber in subscribers:
            try:
                subscriber.put_nowait(event)
            except Full:
                # A stalled/slow subscriber's bounded queue is full: drop
                # this event for that subscriber rather than growing its
                # queue without bound. SSE is inherently best-effort here --
                # `history()`/the REST incident view remain the durable
                # source of truth, and a reconnecting subscriber replays
                # from history on `stream()`'s next call.
                pass

    def history(self, incident_id: UUID) -> tuple[AgentEvent, ...]:
        with self._lock:
            return tuple(self._history.get(incident_id, ()))

    def stream(
        self, incident_id: UUID, *, heartbeat_seconds: float = 15.0
    ) -> Iterator[AgentEvent | None]:
        subscriber: Queue[AgentEvent] = Queue(maxsize=self._subscriber_queue_limit)
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
