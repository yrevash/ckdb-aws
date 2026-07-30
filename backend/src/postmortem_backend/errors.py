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
