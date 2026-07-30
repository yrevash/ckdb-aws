from __future__ import annotations

import json
import random
import re
import time
from typing import Any, Mapping, Protocol

from .contracts import CandidateRunbook, EpisodeGroup, ModelResult


class ConsolidationModel(Protocol):
    def consolidate(self, group: EpisodeGroup) -> ModelResult: ...


def build_prompt(group: EpisodeGroup) -> str:
    episodes = [
        {
            "event_id": item.event_id,
            "type": item.event_type,
            "content": item.content,
            "metadata": dict(item.metadata),
            "occurred_at": item.occurred_at.isoformat(),
        }
        for item in group.episodes
    ]
    return (
        "You are Postmortem's sleep-time memory consolidator. Distill the completed "
        "incident into one safe procedural runbook. Return JSON only with keys: "
        "name, trigger_desc, steps, preconditions, postconditions, service_tags, "
        "error_signatures. Do not invent actions not present in the evidence.\n"
        f"OUTCOME={group.outcome}\nEPISODES={json.dumps(episodes, sort_keys=True)}"
    )


class DeterministicConsolidationModel:
    """Local oracle used by tests and credential-free demos."""

    model_id = "deterministic-local-v1"

    def consolidate(self, group: EpisodeGroup) -> ModelResult:
        alert = next(
            (event for event in group.episodes if event.event_type == "alert"),
            group.episodes[0],
        )
        action = next(
            (
                event
                for event in reversed(group.episodes)
                if event.event_type == "action"
            ),
            group.episodes[-1],
        )
        service = str(
            alert.metadata.get("service_name")
            or action.metadata.get("service_name")
            or group.service_id
        )
        family = str(
            alert.metadata.get("family_id")
            or action.metadata.get("family_id")
            or "incident"
        )
        signature = str(
            alert.metadata.get("error_signature")
            or alert.metadata.get("signal")
            or alert.content
            or family
        )
        action_type = str(action.metadata.get("action_type") or "escalate")
        action_params = action.metadata.get("params") or {}
        if not isinstance(action_params, Mapping):
            action_params = {}

        name_token = re.sub(r"[^a-z0-9]+", "-", f"{service}-{family}".lower()).strip(
            "-"
        )
        payload: dict[str, Any] = {
            "name": name_token or "incident-response",
            "trigger_desc": signature,
            "steps": (
                {
                    "order": 1,
                    "tool": "get_operational_state",
                    "action": "verify_signature",
                    "expected": signature,
                },
                {
                    "order": 2,
                    "tool": "remediate_and_record",
                    "action": action_type,
                    "params": dict(action_params),
                },
                {
                    "order": 3,
                    "tool": "get_operational_state",
                    "action": "verify_slo_recovery",
                },
            ),
            "preconditions": (
                {"field": "service", "operator": "eq", "value": service},
                {"field": "signature", "operator": "contains", "value": signature},
            ),
            "postconditions": (
                {"field": "slo_status", "operator": "eq", "value": "healthy"},
            ),
            "service_tags": (service,),
            "error_signatures": (signature,),
        }
        prompt = build_prompt(group)
        raw_response = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return ModelResult(
            candidate=_candidate_from_payload(group, payload),
            model_id=self.model_id,
            prompt=prompt,
            raw_response=raw_response,
        )


def _candidate_from_payload(
    group: EpisodeGroup, payload: Mapping[str, Any]
) -> CandidateRunbook:
    steps = payload.get("steps")
    if not isinstance(steps, (list, tuple)) or not steps:
        raise ValueError("consolidation model returned no runbook steps")

    def objects(key: str) -> tuple[Mapping[str, Any], ...]:
        raw = payload.get(key) or ()
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"model field {key!r} must be an array")
        if not all(isinstance(value, Mapping) for value in raw):
            raise ValueError(f"model field {key!r} must contain objects")
        return tuple(dict(value) for value in raw)

    def strings(key: str) -> tuple[str, ...]:
        raw = payload.get(key) or ()
        if not isinstance(raw, (list, tuple)):
            raise ValueError(f"model field {key!r} must be an array")
        return tuple(str(value) for value in raw if str(value))

    return CandidateRunbook(
        org_id=group.org_id,
        agent_id=group.agent_id,
        incident_id=group.incident_id,
        service_id=group.service_id,
        name=str(payload.get("name") or "").strip(),
        trigger_desc=str(payload.get("trigger_desc") or "").strip(),
        steps=objects("steps"),
        preconditions=objects("preconditions"),
        postconditions=objects("postconditions"),
        service_tags=strings("service_tags"),
        error_signatures=strings("error_signatures"),
        outcome=group.outcome,
        source_event_ids=tuple(event.event_id for event in group.episodes),
    )


class BedrockConsolidationModel:
    """Bedrock Converse adapter with bounded throttling retries."""

    def __init__(
        self,
        *,
        client: Any,
        model_id: str,
        guardrail_id: str | None = None,
        guardrail_version: str | None = None,
        max_attempts: int = 4,
    ) -> None:
        self._client = client
        self.model_id = model_id
        self._guardrail_id = guardrail_id
        self._guardrail_version = guardrail_version
        self._max_attempts = max_attempts

    def consolidate(self, group: EpisodeGroup) -> ModelResult:
        prompt = build_prompt(group)
        request: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": 1600, "temperature": 0},
        }
        if self._guardrail_id and self._guardrail_version:
            request["guardrailConfig"] = {
                "guardrailIdentifier": self._guardrail_id,
                "guardrailVersion": self._guardrail_version,
                "trace": "enabled",
            }

        response: Mapping[str, Any] | None = None
        for attempt in range(self._max_attempts):
            try:
                response = self._client.converse(**request)
                break
            except Exception as error:
                code = getattr(error, "response", {}).get("Error", {}).get("Code")
                if code not in {"ThrottlingException", "TooManyRequestsException"}:
                    raise
                if attempt + 1 == self._max_attempts:
                    raise
                delay = min(8.0, (2**attempt) * 0.25) + random.uniform(0, 0.15)
                time.sleep(delay)
        if response is None:
            raise RuntimeError("Bedrock consolidation produced no response")

        content = response["output"]["message"]["content"]
        raw_response = next(
            item["text"] for item in content if isinstance(item, Mapping) and "text" in item
        )
        normalized = _extract_json(raw_response)
        payload = json.loads(normalized)
        if not isinstance(payload, Mapping):
            raise ValueError("Bedrock response must decode to a JSON object")
        return ModelResult(
            candidate=_candidate_from_payload(group, payload),
            model_id=self.model_id,
            prompt=prompt,
            raw_response=raw_response,
        )


def _extract_json(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped
