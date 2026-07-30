from __future__ import annotations

import json

from postmortem_consolidation.handlers import receive_changefeed


class FakeQueue:
    def __init__(self) -> None:
        self.entries: list[dict[str, object]] = []

    def send_message_batch(
        self, *, QueueUrl: str, Entries: list[dict[str, object]]
    ) -> dict[str, object]:
        assert QueueUrl == "queue-url"
        self.entries.extend(Entries)
        return {"Successful": Entries}


def webhook_event(secret: str) -> dict[str, object]:
    return {
        "headers": {"X-Postmortem-Webhook-Secret": secret},
        "body": json.dumps(
            {
                "payload": [
                    {
                        "updated": "2026-07-30T03:02:11.470Z",
                        "after": {
                            "event_id": "event-1",
                            "org_id": "org-1",
                            "agent_id": "agent-1",
                            "incident_id": "incident-1",
                            "service_id": "service-1",
                            "event_type": "alert",
                            "content": "checkout p99",
                            "occurred_at": "2026-07-30T03:01:00Z",
                            "metadata": {},
                        },
                    },
                    {"resolved": "2026-07-30T03:03:00Z"},
                ]
            }
        ),
    }


def test_receiver_authenticates_and_enqueues_typed_fifo_messages() -> None:
    queue = FakeQueue()
    response = receive_changefeed(
        webhook_event("correct"),
        queue_client=queue,
        queue_url="queue-url",
        expected_secret="correct",
    )

    assert response["statusCode"] == 202
    assert json.loads(response["body"]) == {"accepted": 2}
    assert [json.loads(entry["MessageBody"])["kind"] for entry in queue.entries] == [
        "row",
        "window_closed",
    ]
    assert all(entry["MessageGroupId"] for entry in queue.entries)
    assert all(entry["MessageDeduplicationId"] for entry in queue.entries)


def test_receiver_rejects_bad_secret_without_enqueuing() -> None:
    queue = FakeQueue()
    response = receive_changefeed(
        webhook_event("wrong"),
        queue_client=queue,
        queue_url="queue-url",
        expected_secret="correct",
    )

    assert response["statusCode"] == 401
    assert queue.entries == []
