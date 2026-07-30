from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Protocol

EMBEDDING_DIMENSIONS = 1024


class EmbeddingModel(Protocol):
    def embed(self, text: str) -> tuple[float, ...]: ...


def _validate(values: list[object]) -> tuple[float, ...]:
    if len(values) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"expected {EMBEDDING_DIMENSIONS} embedding dimensions, got {len(values)}"
        )
    embedding = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in embedding):
        raise ValueError("embedding contains a non-finite value")
    return embedding


class DeterministicEmbeddingModel:
    """Stable normalized 1024-vector for local execution and contract tests."""

    model_id = "deterministic-embedding-v1"

    def embed(self, text: str) -> tuple[float, ...]:
        values = []
        for index in range(EMBEDDING_DIMENSIONS):
            digest = hashlib.sha256(f"{index}:{text}".encode("utf-8")).digest()
            unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
            values.append((unit * 2.0) - 1.0)
        norm = math.sqrt(sum(value * value for value in values))
        return _validate([value / norm for value in values])


class BedrockTitanEmbeddingModel:
    """Amazon Titan Text Embeddings V2 adapter locked to normalized VECTOR(1024)."""

    def __init__(
        self,
        *,
        client: Any,
        model_id: str = "amazon.titan-embed-text-v2:0",
    ) -> None:
        self._client = client
        self.model_id = model_id

    def embed(self, text: str) -> tuple[float, ...]:
        response = self._client.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "inputText": text,
                    "dimensions": EMBEDDING_DIMENSIONS,
                    "normalize": True,
                }
            ),
        )
        payload = json.loads(response["body"].read())
        values = payload.get("embedding")
        if not isinstance(values, list):
            raise ValueError("Titan response did not contain an embedding array")
        return _validate(values)
