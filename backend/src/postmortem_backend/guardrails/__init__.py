"""Agentic & application-layer security guardrails (docs/security/02).

These modules implement the structural (not prompt-based) controls the security
charter assigns to the "Agentic & application guardrails" owner:

* ``allowlist``   -- tool allowlist + destructive/high-blast-radius action gate (R3, R5)
* ``provenance``  -- ungrounded-action rejection + citation resolution gate (R4, R7)
* ``injection``   -- prompt-injection defenses for untrusted free text (T1, R6 app side)
* ``validation``  -- typed input validation + authenticated changefeed webhook (R9)
* ``roles``       -- reader/writer/consolidator SQL-identity separation in code (R7, T2)

Every control here is deny-by-default and fails closed: an ambiguous or
malformed input is rejected, never passed through.
"""

from __future__ import annotations

from .allowlist import (
    ALLOWED_TOOLS,
    HIGH_BLAST_RADIUS_ACTIONS,
    ApprovalRecord,
    authorize_action,
    enforce_tool_allowlist,
    is_high_blast_radius,
)
from .injection import (
    contains_tool_call_injection,
    guard_untrusted_text,
    sanitize_signal,
    scrub_control_characters,
)
from .provenance import (
    ProvenanceGuardedRemediation,
    require_grounded_action,
    verify_citation_resolves,
)
from .roles import (
    DatabaseRole,
    RoleScopedProvider,
    require_reader,
    require_writer,
)
from .validation import (
    AlertPayload,
    ChangefeedEnvelope,
    WebhookAuthenticator,
    verify_hmac_signature,
)

__all__ = [
    "ALLOWED_TOOLS",
    "HIGH_BLAST_RADIUS_ACTIONS",
    "ApprovalRecord",
    "authorize_action",
    "enforce_tool_allowlist",
    "is_high_blast_radius",
    "contains_tool_call_injection",
    "guard_untrusted_text",
    "sanitize_signal",
    "scrub_control_characters",
    "ProvenanceGuardedRemediation",
    "require_grounded_action",
    "verify_citation_resolves",
    "DatabaseRole",
    "RoleScopedProvider",
    "require_reader",
    "require_writer",
    "AlertPayload",
    "ChangefeedEnvelope",
    "WebhookAuthenticator",
    "verify_hmac_signature",
]
