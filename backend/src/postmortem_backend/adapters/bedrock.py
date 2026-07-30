"""Amazon Bedrock and optional Strands adapters."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from uuid import UUID

from ..domain import (
    ActionKind,
    AgentDecision,
    DecisionKind,
    IncidentSignal,
    MemoryKind,
    RecallBundle,
    RemediationCommand,
)
from ..errors import ReasoningError


class TitanEmbeddingAdapter:
    dimension = 1024

    def __init__(
        self,
        *,
        region: str,
        model_id: str = "amazon.titan-embed-text-v2:0",
        client: Any | None = None,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region)
        self._client = client
        self._model_id = model_id

    def embed(self, text: str) -> tuple[float, ...]:
        response = self._client.invoke_model(
            modelId=self._model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(
                {
                    "inputText": text,
                    "dimensions": self.dimension,
                    "normalize": True,
                }
            ),
        )
        body = response["body"].read()
        payload = json.loads(body)
        embedding = tuple(float(value) for value in payload["embedding"])
        if len(embedding) != self.dimension:
            raise ReasoningError(
                f"Titan returned {len(embedding)} dimensions; expected {self.dimension}."
            )
        return embedding


class BedrockReasoningAdapter:
    """Grounded JSON decision over Bedrock Converse."""

    def __init__(
        self,
        *,
        region: str,
        model_id: str,
        client: Any | None = None,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=region)
        self._client = client
        self._model_id = model_id

    def decide(self, signal: IncidentSignal, memory: RecallBundle) -> AgentDecision:
        response = self._client.converse(
            modelId=self._model_id,
            system=[{"text": _SYSTEM_PROMPT}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": _reasoning_input(signal, memory)}],
                }
            ],
            inferenceConfig={"maxTokens": 900, "temperature": 0.0},
        )
        blocks = response["output"]["message"]["content"]
        text = "".join(block.get("text", "") for block in blocks)
        return _parse_decision(text, signal, memory)


class StrandsReasoningAdapter:
    """Optional Strands integration; imported only when explicitly configured."""

    def __init__(self, *, region: str, model_id: str) -> None:
        try:
            from strands import Agent
            from strands.models import BedrockModel
        except ImportError as exc:  # pragma: no cover - optional deployment integration
            raise RuntimeError(
                "Install the backend's `strands` extra to select POSTMORTEM_REASONER=strands."
            ) from exc
        model = BedrockModel(model_id=model_id, region_name=region)
        self._agent = Agent(model=model, system_prompt=_SYSTEM_PROMPT)

    def decide(self, signal: IncidentSignal, memory: RecallBundle) -> AgentDecision:
        result = self._agent(_reasoning_input(signal, memory))
        return _parse_decision(str(result), signal, memory)


_SYSTEM_PROMPT = """
You are Postmortem, a cautious on-call SRE responder.
Use only the retrieved memory included in the request. Every remediation must cite one supplied
memory_id. Never invent an identifier. If evidence is insufficient, escalate.
Return one JSON object and no prose:
{
  "decision": "remediate_and_record|ask_human|escalate",
  "explanation": "short grounded explanation",
  "cited_memory_id": "uuid or null",
  "action": "rollback|restart|null",
  "target_version": "version or null",
  "requires_human_approval": true,
  "confidence": 0.0
}
""".strip()


def _reasoning_input(signal: IncidentSignal, memory: RecallBundle) -> str:
    items = []
    for candidate in memory.all_candidates:
        items.append(
            {
                "memory_id": str(candidate.memory_id),
                "kind": candidate.kind.value,
                "content": candidate.content,
                "similarity": candidate.similarity,
                "success_rate": candidate.success_rate,
                "confidence": candidate.confidence,
                "ranking_score": candidate.ranking_score,
                "provenance_ids": [str(item) for item in candidate.provenance_ids],
                "actionable": candidate.metadata.get("actionable", True),
                "runbook_id": str(candidate.runbook_id) if candidate.runbook_id else None,
                "steps": list(candidate.steps),
                "metadata": candidate.metadata,
            }
        )
    return json.dumps(
        {
            "incident": {
                "incident_id": str(signal.incident_id),
                "service_id": str(signal.service_id),
                "severity": signal.severity,
                "summary": signal.summary,
                "error_signature": signal.error_signature,
            },
            "retrieved_memory": items,
        },
        default=str,
    )


def _parse_decision(
    raw: str, signal: IncidentSignal, memory: RecallBundle
) -> AgentDecision:
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        payload = json.loads(raw[start:end])
        kind = DecisionKind(payload["decision"])
        confidence = float(payload.get("confidence", 0.0))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ReasoningError("Reasoner did not return the typed decision contract.") from exc

    explanation = str(payload.get("explanation", "")).strip()
    cited_raw = payload.get("cited_memory_id")
    cited_id = UUID(cited_raw) if cited_raw else None
    known = {candidate.memory_id: candidate for candidate in memory.all_candidates}

    if kind is not DecisionKind.REMEDIATE:
        return AgentDecision(
            kind=kind,
            explanation=explanation,
            cited_memory_id=cited_id,
            confidence=confidence,
        )
    if cited_id not in known:
        raise ReasoningError("Remediation citation was absent from recalled memory.")

    candidate = known[cited_id]
    if not candidate.metadata.get("actionable", True):
        raise ReasoningError(
            "Cited memory failed the recall provenance or confidence safety gate."
        )
    action = ActionKind(payload["action"])
    if action not in {ActionKind.ROLLBACK, ActionKind.RESTART}:
        raise ReasoningError("Reasoner selected an action outside the Phase 1 allowlist.")
    target_version = str(payload.get("target_version") or "").strip()
    if not target_version:
        raise ReasoningError("Reasoner omitted target_version for remediation.")

    runbook_id = (
        candidate.memory_id
        if candidate.kind is MemoryKind.PROCEDURAL
        else candidate.runbook_id
    )
    command = RemediationCommand(
        org_id=signal.org_id,
        agent_id=signal.agent_id,
        incident_id=signal.incident_id,
        session_id=signal.session_id,
        service_id=signal.service_id,
        action=action,
        target_version=target_version,
        cited_memory_id=cited_id,
        runbook_id=runbook_id,
        rationale=explanation,
        requires_human_approval=bool(payload.get("requires_human_approval", False)),
    )
    return AgentDecision(
        kind=kind,
        explanation=explanation,
        cited_memory_id=cited_id,
        command=command,
        confidence=confidence,
    )
