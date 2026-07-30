from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from .contracts import ClosedWindow, parse_changefeed_body, parse_queue_message
from .embedding import BedrockTitanEmbeddingModel, DeterministicEmbeddingModel
from .model import BedrockConsolidationModel, DeterministicConsolidationModel
from .pipeline import ConsolidationProcessor
from .repository import CockroachRunbookRepository
from .storage import S3WindowStore

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

_processor: ConsolidationProcessor | None = None
_webhook_secret: str | None = None


def _secret_value(secret_id: str) -> str:
    import boto3

    response = boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)
    value = response.get("SecretString")
    if not isinstance(value, str):
        raise RuntimeError(f"secret {secret_id!r} has no SecretString")
    return value


def _database_url() -> str:
    direct = os.getenv("CONSOLIDATION_DATABASE_URL")
    if direct:
        return direct
    value = _secret_value(os.environ["CONSOLIDATION_DATABASE_SECRET_ARN"])
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    if isinstance(decoded, Mapping):
        for key in ("url", "database_url", "POSTMORTEM_DATABASE_URL"):
            if isinstance(decoded.get(key), str):
                return decoded[key]
    raise RuntimeError("database secret must be a URL or contain a database URL field")


def _expected_webhook_secret() -> str:
    global _webhook_secret
    if _webhook_secret is None:
        _webhook_secret = os.getenv("CHANGEFEED_WEBHOOK_SECRET") or _secret_value(
            os.environ["CHANGEFEED_WEBHOOK_SECRET_ARN"]
        )
    return _webhook_secret


def _request_secret(headers: Mapping[str, Any]) -> str:
    normalized = {str(key).lower(): str(value) for key, value in headers.items()}
    explicit = normalized.get("x-postmortem-webhook-secret")
    if explicit:
        return explicit
    authorization = normalized.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:]
    return ""


def _response(status: int, body: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, sort_keys=True),
    }


def receive_changefeed(
    event: Mapping[str, Any],
    *,
    queue_client: Any,
    queue_url: str,
    expected_secret: str,
) -> dict[str, Any]:
    supplied = _request_secret(event.get("headers") or {})
    if not supplied or not hmac.compare_digest(supplied, expected_secret):
        return _response(401, {"error": "invalid webhook credential"})

    encoded = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        encoded = base64.b64decode(encoded).decode("utf-8")
    try:
        payload = json.loads(encoded)
        messages = parse_changefeed_body(payload)
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        return _response(400, {"error": str(error)})

    accepted = 0
    for start in range(0, len(messages), 10):
        chunk = messages[start : start + 10]
        entries = []
        for index, message in enumerate(chunk):
            deduplication_id = message.deduplication_id
            entries.append(
                {
                    "Id": f"m{start + index}-{deduplication_id[:8]}",
                    "MessageBody": json.dumps(message.to_dict(), sort_keys=True),
                    "MessageGroupId": "postmortem-consolidation",
                    "MessageDeduplicationId": deduplication_id,
                }
            )
        result = queue_client.send_message_batch(QueueUrl=queue_url, Entries=entries)
        failed = result.get("Failed", [])
        if failed:
            raise RuntimeError(f"SQS rejected {len(failed)} changefeed messages")
        accepted += len(entries)
    return _response(202, {"accepted": accepted})


def receiver_handler(event: Mapping[str, Any], _context: Any) -> dict[str, Any]:
    import boto3

    return receive_changefeed(
        event,
        queue_client=boto3.client("sqs"),
        queue_url=os.environ["CONSOLIDATION_QUEUE_URL"],
        expected_secret=_expected_webhook_secret(),
    )


def _build_processor() -> ConsolidationProcessor:
    import boto3

    store = S3WindowStore(
        client=boto3.client("s3"),
        bucket=os.environ["ARTIFACTS_BUCKET"],
    )
    if os.getenv("CONSOLIDATION_MODEL_MODE", "bedrock") == "deterministic":
        model = DeterministicConsolidationModel()
        embedder = DeterministicEmbeddingModel()
    else:
        bedrock = boto3.client("bedrock-runtime")
        model = BedrockConsolidationModel(
            client=bedrock,
            model_id=os.getenv(
                "CONSOLIDATION_MODEL_ID",
                "us.anthropic.claude-3-5-haiku-20241022-v1:0",
            ),
            guardrail_id=os.getenv("CONSOLIDATION_GUARDRAIL_ID"),
            guardrail_version=os.getenv("CONSOLIDATION_GUARDRAIL_VERSION"),
        )
        embedder = BedrockTitanEmbeddingModel(
            client=bedrock,
            model_id=os.getenv(
                "CONSOLIDATION_EMBEDDING_MODEL_ID",
                "amazon.titan-embed-text-v2:0",
            ),
        )
    return ConsolidationProcessor(
        store=store,
        model=model,
        embedder=embedder,
        repository=CockroachRunbookRepository(
            _database_url(),
            reinforcements_to_activate=int(
                os.getenv("RUNBOOK_REINFORCEMENTS_TO_ACTIVATE", "2")
            ),
        ),
    )


def consolidator_handler(event: Mapping[str, Any], _context: Any) -> dict[str, Any]:
    global _processor
    _processor = _processor or _build_processor()

    records = event.get("Records")
    if not isinstance(records, list):
        now = datetime.now(timezone.utc)
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        elapsed = now - epoch
        watermark = Decimal(
            elapsed.days * 86_400_000_000_000
            + elapsed.seconds * 1_000_000_000
            + elapsed.microseconds * 1_000
        )
        result = _processor.process(ClosedWindow(watermark))
        return {
            "completed_groups": result.completed_groups,
            "mutations": [mutation.operation for mutation in result.mutations],
        }

    failures: list[dict[str, str]] = []
    for index, record in enumerate(records):
        identifier = str(record.get("messageId") or index)
        try:
            body = json.loads(record["body"])
            message = parse_queue_message(body)
            result = _processor.process(message)
            logger.info(
                json.dumps(
                    {
                        "message_id": identifier,
                        "buffered_events": result.buffered_events,
                        "completed_groups": result.completed_groups,
                        "mutations": [
                            mutation.operation for mutation in result.mutations
                        ],
                    }
                )
            )
        except Exception:
            logger.exception("consolidation record failed", extra={"message_id": identifier})
            # FIFO ordering: fail this and every later record in the batch.
            failures.extend(
                {
                    "itemIdentifier": str(
                        remaining.get("messageId") or remaining_index
                    )
                }
                for remaining_index, remaining in enumerate(
                    records[index:], start=index
                )
            )
            break
    return {"batchItemFailures": failures}
