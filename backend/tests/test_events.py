"""EventBroker bounding: history is a ring buffer per incident and a slow
subscriber's queue is capped rather than growing forever (audit backend#6).
"""

from __future__ import annotations

import unittest
from queue import Empty
from uuid import UUID

from postmortem_backend.domain import AgentEvent, EventType
from postmortem_backend.events import EventBroker


INCIDENT_ID = UUID("80000000-0000-0000-0000-000000000001")


def event(sequence: int) -> AgentEvent:
    return AgentEvent(
        incident_id=INCIDENT_ID,
        type=EventType.RESPONSE_COMPLETED,
        stage="record",
        message=f"event {sequence}",
        data={"sequence": sequence},
    )


class HistoryBoundTests(unittest.TestCase):
    def test_history_is_capped_and_evicts_oldest_first(self) -> None:
        broker = EventBroker(history_limit=5)
        for sequence in range(12):
            broker.publish(event(sequence))

        history = broker.history(INCIDENT_ID)

        self.assertEqual(len(history), 5)
        # The oldest 7 were evicted; only the most recent 5 remain, in order.
        self.assertEqual(
            [item.data["sequence"] for item in history], [7, 8, 9, 10, 11]
        )

    def test_unbounded_publishing_never_exceeds_the_configured_limit(self) -> None:
        broker = EventBroker(history_limit=10)
        for sequence in range(10_000):
            broker.publish(event(sequence))

        self.assertEqual(len(broker.history(INCIDENT_ID)), 10)


class SubscriberQueueBoundTests(unittest.TestCase):
    def test_slow_subscriber_queue_does_not_grow_without_bound(self) -> None:
        broker = EventBroker(history_limit=1_000, subscriber_queue_limit=3)
        stream = broker.stream(INCIDENT_ID, heartbeat_seconds=0.01)
        # Prime the generator so its Queue is registered as a subscriber
        # before any events are published (mirrors the SSE handler pulling
        # the first item off the stream).
        next(stream)  # heartbeat/history replay boundary (empty history)

        # Publish far more events than the queue can hold; a slow/absent
        # consumer must not block publish() or grow the queue unbounded.
        for sequence in range(50):
            broker.publish(event(sequence))

        subscriber_queues = broker._subscribers[INCIDENT_ID]  # type: ignore[attr-defined]
        self.assertEqual(len(subscriber_queues), 1)
        (queue,) = tuple(subscriber_queues)
        self.assertLessEqual(queue.qsize(), 3)

    def test_stream_still_delivers_events_up_to_the_queue_bound(self) -> None:
        broker = EventBroker(subscriber_queue_limit=10)
        stream = broker.stream(INCIDENT_ID, heartbeat_seconds=0.01)
        first = next(stream)
        self.assertIsNone(first)  # no history yet, heartbeat fires

        broker.publish(event(1))
        received = next(stream)
        self.assertIsNotNone(received)
        self.assertEqual(received.data["sequence"], 1)

    def test_history_replay_is_unaffected_by_the_subscriber_queue_bound(self) -> None:
        broker = EventBroker(history_limit=1_000, subscriber_queue_limit=2)
        for sequence in range(5):
            broker.publish(event(sequence))

        stream = broker.stream(INCIDENT_ID, heartbeat_seconds=0.01)
        replayed = [next(stream) for _ in range(5)]

        self.assertEqual([item.data["sequence"] for item in replayed], [0, 1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
