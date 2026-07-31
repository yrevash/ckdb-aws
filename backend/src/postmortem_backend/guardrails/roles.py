"""Role-scoped DB access enforced in code (charter R7, T2).

The Track C migration (``db/migrations/0007_audit_logging.sql``) creates two
least-privilege SQL identities -- ``postmortem_reader`` (SELECT-only, the recall
path) and ``postmortem_writer`` (scoped INSERT/UPDATE, the act path) -- plus a
future ``postmortem_consolidator``. Those grants are the *authoritative* boundary
at the database.

This module makes the same split structural **in the application code** so a
wiring mistake fails at construction, in-process, before a single statement
reaches CockroachDB -- defense in depth. A connection provider is tagged with the
identity it dials, and the recall adapter demands a read-capable provider while
the act/outcome adapters demand a write-capable one. It is therefore impossible
to hand the act path a reader-scoped pool (or the recall path a writer-scoped
pool) without raising :class:`RoleScopeViolation`.

Unscoped providers (a bare ``PsycopgPoolProvider`` or a test stub) are treated as
"role unknown" and pass through untouched, so existing local/CI wiring and the
fake runtime are unaffected. Production wiring (``runtime.py`` AWS mode) always
wraps its pools, so the guarantee holds where it matters.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from enum import StrEnum
from typing import Any

from ..errors import RoleScopeViolation


class DatabaseRole(StrEnum):
    """The three least-privilege SQL identities from doc 04 §4 / HARDENING.md §2."""

    READER = "postmortem_reader"
    WRITER = "postmortem_writer"
    CONSOLIDATOR = "postmortem_consolidator"


# A writer or consolidator may read what it needs to join/FK-check; a reader may
# never write. These sets encode exactly that capability lattice.
_READ_CAPABLE = frozenset(
    {DatabaseRole.READER, DatabaseRole.WRITER, DatabaseRole.CONSOLIDATOR}
)
_WRITE_CAPABLE = frozenset({DatabaseRole.WRITER, DatabaseRole.CONSOLIDATOR})


class RoleScopedProvider:
    """Binds a connection provider to a single SQL identity/role.

    Wrapping a provider is what turns the recall/act identity split from an
    app-level convention into an enforced invariant: the role travels with the
    pool, and the adapters assert it at construction time.
    """

    __slots__ = ("_provider", "role", "identity")

    def __init__(
        self,
        provider: Callable[[], AbstractContextManager[Any]],
        role: DatabaseRole,
        *,
        identity: str | None = None,
    ) -> None:
        self._provider = provider
        self.role = role
        # The concrete service-account principal (e.g. ``postmortem_agent_writer``)
        # for audit/log correlation; the *role* is what authorization keys on.
        self.identity = identity or role.value

    def __call__(self) -> AbstractContextManager[Any]:
        return self._provider()

    def close(self) -> None:
        close = getattr(self._provider, "close", None)
        if close is not None:
            close()


def provider_role(provider: Any) -> DatabaseRole | None:
    """Return the bound role, or ``None`` for an unscoped/legacy provider."""

    role = getattr(provider, "role", None)
    return role if isinstance(role, DatabaseRole) else None


def require_reader(provider: Any) -> None:
    """Assert the recall path is not wired to a write-capable identity.

    Recall must run as the reader. Handing it the writer (the "vice-versa" the
    charter calls out) is refused: least privilege means the read path cannot
    hold write grants even by accident.
    """

    role = provider_role(provider)
    if role is None:
        return
    if role is not DatabaseRole.READER:
        raise RoleScopeViolation(
            f"Recall path requires the {DatabaseRole.READER.value} identity; "
            f"refusing a {role.value}-scoped connection (least privilege, R7)."
        )


def require_writer(provider: Any) -> None:
    """Assert the act/record path is not wired to the read-only reader identity."""

    role = provider_role(provider)
    if role is None:
        return
    if role not in _WRITE_CAPABLE:
        raise RoleScopeViolation(
            f"Act path requires a write-capable identity "
            f"({', '.join(sorted(r.value for r in _WRITE_CAPABLE))}); "
            f"refusing a {role.value}-scoped connection (R7, T2)."
        )


def require_read_capable(provider: Any) -> None:
    """Assert a provider can at least read (any of the three roles)."""

    role = provider_role(provider)
    if role is None:
        return
    if role not in _READ_CAPABLE:
        raise RoleScopeViolation(
            f"Read requires one of {sorted(r.value for r in _READ_CAPABLE)}; "
            f"got {role.value}."
        )
