from __future__ import annotations

from decimal import Decimal

import pytest

from postmortem_consolidation.contracts import (
    ChangeEvent,
    ClosedWindow,
    parse_changefeed_body,
    parse_queue_message,
)


def row(event_id: str = "event-1") -> dict[str, object]:
    return {
        "updated": "2026-07-30T03:02:11.470Z",
        "after": {
            "event_id": event_id,
            "org_id": "org-1",
            "agent_id": "agent-1",
            "incident_id": "incident-1",
            "service_id": "service-1",
            "event_type": "alert",
            "content": "checkout p99 4.2s",
            "occurred_at": "2026-07-30T03:01:00Z",
            "metadata": {"signal": "latency"},
        },
    }


def test_changefeed_batch_normalizes_rows_and_watermarks() -> None:
    messages = parse_changefeed_body(
        {
            "payload": [
                row(),
                {"resolved": "2026-07-30T03:03:00Z"},
                {"after": None, "key": ["deleted"]},
            ]
        }
    )

    assert len(messages) == 2
    assert isinstance(messages[0], ChangeEvent)
    assert isinstance(messages[1], ClosedWindow)
    assert messages[0].metadata == {"signal": "latency"}
    assert messages[1].watermark == Decimal("1785380580000000000")


def test_native_cockroach_hlc_is_preserved_exactly() -> None:
    item = row()
    item["updated"] = "1629813621680097993.0000000000"
    messages = parse_changefeed_body(
        {"payload": [item, {"resolved": "1629813621680097994.0000000000"}]}
    )

    assert messages[0].updated_at == Decimal("1629813621680097993.0000000000")
    assert messages[1].watermark > messages[0].updated_at


def test_queue_contract_round_trips() -> None:
    message = parse_changefeed_body(row())[0]
    assert parse_queue_message(message.to_dict()) == message


def test_malformed_row_is_rejected() -> None:
    invalid = row()
    invalid["after"] = {"event_id": "missing-fields"}
    with pytest.raises(ValueError, match="after.org_id"):
        parse_changefeed_body(invalid)
