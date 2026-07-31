"""Domain failures mapped to safe API responses."""


class PostmortemError(Exception):
    """Base class for expected responder failures."""


class ConfigurationError(PostmortemError):
    pass


class ProvenanceError(PostmortemError):
    pass


class ApprovalRequired(PostmortemError):
    pass


class UnsupportedAction(PostmortemError):
    pass


class RecallError(PostmortemError):
    pass


class ReasoningError(PostmortemError):
    pass


class AtomicRemediationError(PostmortemError):
    pass


class OutcomeConflict(PostmortemError):
    pass


class OutcomeRecordingError(PostmortemError):
    pass


# --- Agentic & application guardrail failures (docs/security/02) --------------


class ToolNotAllowed(PostmortemError):
    """The agent tried to invoke a tool outside the explicit allowlist (R3)."""


class DestructiveActionBlocked(PostmortemError):
    """A high-blast-radius/irreversible action was refused without human approval (R5)."""


class PromptInjectionDetected(PostmortemError):
    """Untrusted free text attempted to smuggle a tool call or directive (T1/R6)."""


class InputValidationError(PostmortemError):
    """An external input (alert/webhook/body) failed structural/type validation (R9)."""


class WebhookAuthenticationError(PostmortemError):
    """The changefeed webhook shared-secret/HMAC verification failed (R9)."""


class RoleScopeViolation(PostmortemError):
    """A code path was wired to the wrong SQL identity (reader vs writer) (R7/T2)."""
