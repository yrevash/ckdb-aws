"""Provenance gate: no ungrounded action (charter R4, R7; doc 02 §1.4/§1.5).

The one-transaction wedge already runs a provenance gate *inside* the SQL
(``REMEDIATE_AND_RECORD_SQL``: the CTE returns no row unless the cited memory id
resolves to a real ``episodic_events`` / ``procedural_memory`` row, and the
memory write commits in the same transaction). This module hardens that in two
ways:

1. **Fail fast, before execution.** :func:`require_grounded_action` rejects a
   command whose citation is missing *before* the act path runs, and -- when the
   set of ids actually surfaced by Recall is known -- asserts the citation
   resolves to one of them. This catches an ungrounded or hallucinated citation
   at the application boundary, cheaply, without a DB round trip.

2. **No bypass.** :class:`ProvenanceGuardedRemediation` wraps *any*
   :class:`AtomicRemediationPort` so every act path -- real or fake, now or
   future -- is forced through the same citation check. A wired-in test
   (``test_guardrail_provenance``) asserts the wrapper cannot be bypassed and
   that the underlying DB gate is still the ultimate authority.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from ..domain import RemediationCommand, RemediationResult
from ..errors import ProvenanceError
from ..ports import AtomicRemediationPort


def require_grounded_action(
    command: RemediationCommand,
    *,
    recalled_ids: Iterable[UUID] | None = None,
) -> None:
    """Reject an action with no cited memory, before it executes.

    * The command must carry a ``cited_memory_id`` (a non-nil UUID).
    * If ``recalled_ids`` is supplied (the ids Recall actually surfaced this
      turn), the citation must be one of them -- a citation to something the
      agent never retrieved is treated as ungrounded/hallucinated and refused.
    """

    cited = getattr(command, "cited_memory_id", None)
    if cited is None or cited == UUID(int=0):
        raise ProvenanceError(
            "Ungrounded action refused: no cited memory/runbook id (R4)."
        )
    if recalled_ids is not None:
        allowed = {rid for rid in recalled_ids}
        if cited not in allowed:
            raise ProvenanceError(
                f"Cited memory {cited} was not among the recalled ids this turn; "
                f"the citation does not resolve to retrieved memory (R4)."
            )


def verify_citation_resolves(cited: UUID | None, recalled_ids: Iterable[UUID]) -> bool:
    """Return True iff the citation is a non-nil id present in ``recalled_ids``."""

    if cited is None or cited == UUID(int=0):
        return False
    return cited in {rid for rid in recalled_ids}


class ProvenanceGuardedRemediation:
    """Wrap an act port so no code path can execute an ungrounded action.

    Delegates to the wrapped port only after :func:`require_grounded_action`
    passes. The wrapped store's own gate (in-SQL for CockroachDB, in-memory for
    the fake) remains the final authority; this wrapper guarantees the
    application never even *asks* the store to run an un-cited action.
    """

    def __init__(
        self,
        inner: AtomicRemediationPort,
        *,
        recalled_ids: Iterable[UUID] | None = None,
    ) -> None:
        self._inner = inner
        self._recalled_ids = (
            None if recalled_ids is None else tuple(recalled_ids)
        )

    def remediate_and_record(
        self, command: RemediationCommand, embedding: tuple[float, ...]
    ) -> RemediationResult:
        require_grounded_action(command, recalled_ids=self._recalled_ids)
        return self._inner.remediate_and_record(command, embedding)
