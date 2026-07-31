"""Prompt-injection defenses, app layer (charter T1, R6 app side; doc 02 §1.4).

Alerts, logs, webhook bodies, and recalled memory text are **untrusted**: an
attacker who can influence a log line or a stored memory must not be able to
steer the agent. The structural defenses here do NOT rely on the model resisting
a clever prompt:

* **Free text can never select a tool or its arguments.** The reasoner returns a
  typed :class:`DecisionKind` + enum action + a citation id that must resolve to
  a recalled memory (see ``guardrails.provenance``); the tool and its args are
  built from typed domain objects in ``service.py`` / ``adapters``, never parsed
  out of alert/log prose. This module enforces the complementary rule: untrusted
  *fields* are screened before they ever reach the model.

* **Tool-call-looking content is denied or scrubbed.** Untrusted fields that try
  to smuggle an imperative ("ignore previous instructions", a fake
  ``tool_call``/``function_call`` block, a ``remediate_and_record(...)`` literal,
  role markers like ``system:``) are rejected (:func:`guard_untrusted_text`) or
  neutralized (:func:`sanitize_signal` scrubs control chars and truncates).

**Bedrock Guardrails boundary (deploy-time, owned by infra / doc 01).** These
app-layer checks are the inner ring of defense in depth. The outer ring is a
Bedrock Guardrail attached to the responder's Converse call and the
consolidator, screening model inputs/outputs for prompt-injection, PII, and
harmful content. The plug-in point is the single ``BedrockReasoningAdapter``
Converse call: the ``guardrailIdentifier`` / ``guardrailVersion`` are passed
there at deploy time. This module is intentionally independent of that so the
control holds even if the Guardrail is misconfigured or unavailable.
"""

from __future__ import annotations

import re
from dataclasses import replace

from ..domain import IncidentSignal
from ..errors import PromptInjectionDetected

# Maximum accepted length for a single untrusted free-text field. Anything longer
# is almost certainly an injection payload / context-stuffing attempt, not a real
# alert summary. The API layer also bounds these; this is the model-facing bound.
MAX_UNTRUSTED_FIELD_CHARS = 8_000

# Patterns that indicate an attempt to smuggle a tool call, an instruction
# override, or a role/turn boundary through untrusted content. Case-insensitive.
# These are deny signals for *untrusted* fields only -- never applied to the
# agent's own typed output.
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    # tool / function call syntax
    re.compile(r"\btool_call\b", re.IGNORECASE),
    re.compile(r"\bfunction_call\b", re.IGNORECASE),
    re.compile(r"<\s*/?\s*tool[_-]?use\b", re.IGNORECASE),
    re.compile(r"<\s*/?\s*function[_-]?calls?\b", re.IGNORECASE),
    re.compile(
        r"\b(remediate_and_record|scale_data_tier|update_incident_state|"
        r"record_episode)\s*\(",
        re.IGNORECASE,
    ),
    # instruction-override / jailbreak phrasing
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|the\s+above)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"new\s+system\s+prompt", re.IGNORECASE),
    # chat role / turn markers used to fake a system/assistant turn
    re.compile(r"(?m)^\s*(system|assistant|developer)\s*:", re.IGNORECASE),
    re.compile(r"<\s*/?\s*(system|assistant|user)\s*>", re.IGNORECASE),
    re.compile(r"\[/?INST\]", re.IGNORECASE),
    re.compile(r"<\|(im_start|im_end|system|assistant)\|>", re.IGNORECASE),
)

# Control characters (except tab/newline/carriage-return) are stripped from
# untrusted text: they are used to hide payloads from human reviewers and to
# confuse tokenizers.
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def scrub_control_characters(text: str) -> str:
    """Remove hidden control characters from untrusted text."""

    return _CONTROL_CHARS.sub("", text)


def contains_tool_call_injection(text: str) -> bool:
    """True if untrusted text contains tool-call/instruction-override markers."""

    if not text:
        return False
    return any(pattern.search(text) for pattern in _INJECTION_PATTERNS)


def guard_untrusted_text(text: str | None, *, field: str) -> str | None:
    """Validate a single untrusted free-text field, failing closed on injection.

    Returns the scrubbed text (control chars removed) when clean; raises
    :class:`PromptInjectionDetected` when it looks like a tool call or an
    instruction override, or :class:`PromptInjectionDetected` when it is
    implausibly long.
    """

    if text is None:
        return None
    scrubbed = scrub_control_characters(text)
    if len(scrubbed) > MAX_UNTRUSTED_FIELD_CHARS:
        raise PromptInjectionDetected(
            f"Untrusted field '{field}' exceeds {MAX_UNTRUSTED_FIELD_CHARS} chars; "
            f"refusing (context-stuffing guard, T1)."
        )
    if contains_tool_call_injection(scrubbed):
        raise PromptInjectionDetected(
            f"Untrusted field '{field}' contains tool-call-looking or "
            f"instruction-override content; refusing (T1/R6)."
        )
    return scrubbed


def sanitize_signal(signal: IncidentSignal) -> IncidentSignal:
    """Screen every untrusted field of an inbound incident signal.

    ``summary``, ``error_signature``, and ``service_tags`` originate from
    alerts/webhooks/operators and are untrusted. Each is scrubbed and screened;
    any injection attempt fails the whole turn closed (the signal is never
    partially sanitized into the model). ``metadata`` string values are screened
    too. Structural/UUID fields are typed and need no screening.
    """

    clean_summary = guard_untrusted_text(signal.summary, field="summary") or ""
    clean_signature = guard_untrusted_text(
        signal.error_signature, field="error_signature"
    )
    clean_tags = tuple(
        guard_untrusted_text(tag, field="service_tags") or ""
        for tag in signal.service_tags
    )
    for key, value in signal.metadata.items():
        if isinstance(value, str):
            guard_untrusted_text(value, field=f"metadata.{key}")
    return replace(
        signal,
        summary=clean_summary,
        error_signature=clean_signature,
        service_tags=clean_tags,
    )
