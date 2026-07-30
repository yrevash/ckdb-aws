from __future__ import annotations

import io
import json
import math

import pytest

from postmortem_consolidation.embedding import (
    BedrockTitanEmbeddingModel,
    DeterministicEmbeddingModel,
)


def test_local_embedding_is_deterministic_normalized_vector_1024() -> None:
    model = DeterministicEmbeddingModel()
    first = model.embed("checkout p99 after canary")
    second = model.embed("checkout p99 after canary")

    assert first == second
    assert len(first) == 1024
    assert math.isclose(
        math.sqrt(sum(value * value for value in first)), 1.0, rel_tol=1e-12
    )


class FakeBedrock:
    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions
        self.request: dict[str, object] | None = None

    def invoke_model(self, **request: object) -> dict[str, object]:
        self.request = request
        return {
            "body": io.BytesIO(
                json.dumps({"embedding": [0.01] * self.dimensions}).encode()
            )
        }


def test_titan_boundary_requests_normalized_1024_dimensions() -> None:
    client = FakeBedrock()
    embedding = BedrockTitanEmbeddingModel(client=client).embed("trigger")
    assert client.request is not None
    body = json.loads(client.request["body"])

    assert len(embedding) == 1024
    assert body == {
        "inputText": "trigger",
        "dimensions": 1024,
        "normalize": True,
    }


def test_titan_boundary_rejects_wrong_dimension() -> None:
    with pytest.raises(ValueError, match="expected 1024"):
        BedrockTitanEmbeddingModel(client=FakeBedrock(512)).embed("trigger")
