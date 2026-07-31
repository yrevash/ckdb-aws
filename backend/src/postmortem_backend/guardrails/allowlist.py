"""Tool allowlist + destructive-action gate (charter R3, R5; doc 02 §2.3).

Two structural controls, both deny-by-default:

1. **Tool allowlist.** The agent may only invoke tools on an explicit allowlist.
   The reasoner emits a typed :class:`DecisionKind`, never a free-text tool name,
   but this is the enforcement point that makes "only these tools, ever" a hard
   invariant rather than a property of the prompt. Anything off-list is refused.

2. **Destructive / high-blast-radius gate.** Actions that are irreversible or
   high blast radius (data-tier scaling / prod topology, mass writes, or any
   action the policy tier flags) are refused unless ``human_approved=True``, and
   the approval decision -- who approved, when, for what -- is recorded as an
   :class:`ApprovalRecord`. This is enforced in code, so even a bug elsewhere
   that let such a command reach the act path cannot execute it un-approved.

Neither control trusts model output: the allowlist keys on an enum, and the gate
keys on the action *kind* plus an explicit policy flag, not on anything the model
wrote in prose.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..domain import ActionKind, DecisionKind, RemediationCommand
from ..errors import DestructiveActionBlocked, ToolNotAllowed

# The complete set of tool identifiers the agent is permitted to invoke, mirroring
# the §2.2 catalog in research/postmortem/02-agent-orchestration.md. Read tools,
# the single atomic write tool, and control tools -- nothing else. This is the
# whole namespace; a name not in this frozenset is unreachable.
ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        # read / recall surface (MCP, read-only by default)
        "recall_memory",
        "get_operational_state",
        # the one explicit transactional write path (direct SQL)
        "remediate_and_record",
        "record_episode",
        "update_incident_state",
        # out-of-band, human-gated control-plane action
        "scale_data_tier",
        # pure control, no side effects
        "propose_action",
        "escalate",
        "ask_human",
    }
)

# The map from a typed agent decision to the concrete tool it drives. Used to
# resolve a decision to a tool name for allowlist enforcement without ever
# parsing free text.
DECISION_TOOL: dict[DecisionKind, str] = {
    DecisionKind.REMEDIATE: "remediate_and_record",
    DecisionKind.PROPOSE: "propose_action",
    DecisionKind.ASK_HUMAN: "ask_human",
    DecisionKind.ESCALATE: "escalate",
}

# Actions that are irreversible or high blast radius by their nature. ``SCALE``
# and ``FEATURE_FLAG`` reach production topology / broad traffic and are never
# auto-executed; ``ROLLBACK``/``RESTART`` are reversible, recorded, single-target
# operations and are gated per-incident by the policy flag below instead.
HIGH_BLAST_RADIUS_ACTIONS: frozenset[ActionKind] = frozenset(
    {ActionKind.SCALE, ActionKind.FEATURE_FLAG}
)

# Server-side critical-service policy (audit C2, HIGH). Before this fix, the
# ONLY thing that made a ROLLBACK/RESTART "high blast radius" was
# ``command.requires_human_approval`` -- a field the reasoner (Bedrock's raw
# JSON output, see adapters/bedrock.py) sets. A compromised or simply wrong
# model could therefore set that flag to ``false`` and drive an unapproved
# rollback/restart of a revenue-critical service straight through the gate.
# This frozenset is the server's own, code-owned classification of which
# *named* services are too sensitive for an unattended rollback/restart -- it
# is never influenced by anything the model returns. Matched case-insensitively
# against the incident's ``service_tags`` (a structural field the caller sets
# when opening the incident -- see api.py's RespondRequest -- not reasoner
# output), so a service tagged e.g. "checkout" or "payments" is always
# gated, regardless of what the model claims about
# ``requires_human_approval``.
#
# Deliberately scoped to specific business-service names, not the generic
# ``tier`` classification ("critical-path" -- see services.tier in
# db/migrations/0002_core_schema.sql): tier alone is too broad a signal here
# (most demo/simulated services are tagged critical-path) and this policy is
# about singling out the handful of services where an unattended
# rollback/restart has direct revenue or compliance blast radius. A fuller
# policy keyed on the DB's own services.tier column (looked up by
# service_id rather than trusting the caller-supplied service_tags) is a
# natural follow-up once the recall path can join that context in --
# tracked as a deploy-time hardening note, not done here to avoid adding a
# DB round-trip to every authorization decision.
CRITICAL_SERVICE_TAGS: frozenset[str] = frozenset(
    {
        "checkout",
        "payments",
        "payment",
        "billing",
        "invoicing",
    }
)

# Actions the critical-service policy applies to. ROLLBACK/RESTART are
# otherwise auto-approved (see HIGH_BLAST_RADIUS_ACTIONS above); on a
# critical-tier service they still touch production traffic for a
# revenue-critical path, so they are promoted to "requires human approval"
# regardless of the model's stated intent.
_CRITICAL_SERVICE_GATED_ACTIONS: frozenset[ActionKind] = frozenset(
    {ActionKind.ROLLBACK, ActionKind.RESTART}
)


def is_critical_tier_service(service_tags: Iterable[str]) -> bool:
    """True if any tag in ``service_tags`` names a server-classified
    critical-tier service (case-insensitive, whitespace-trimmed)."""

    normalized = {str(tag).strip().lower() for tag in service_tags}
    return not normalized.isdisjoint(CRITICAL_SERVICE_TAGS)


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """An auditable record of a destructive-action authorization decision.

    Recorded on both allow (with the approver) and deny, so the audit trail
    answers "who approved this irreversible action, when" and "what was refused".
    """

    tool: str
    action: str
    high_blast_radius: bool
    approved: bool
    approver: str | None
    reason: str
    decided_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, object]:
        return {
            "tool": self.tool,
            "action": self.action,
            "high_blast_radius": self.high_blast_radius,
            "approved": self.approved,
            "approver": self.approver,
            "reason": self.reason,
            "decided_at": self.decided_at.isoformat(),
        }


def enforce_tool_allowlist(tool: str) -> str:
    """Return ``tool`` if it is on the allowlist; otherwise refuse (R3)."""

    if tool not in ALLOWED_TOOLS:
        raise ToolNotAllowed(
            f"Tool '{tool}' is not on the remediation allowlist "
            f"({', '.join(sorted(ALLOWED_TOOLS))})."
        )
    return tool


def resolve_decision_tool(kind: DecisionKind) -> str:
    """Map a typed decision to its allowlisted tool name, enforcing the allowlist."""

    tool = DECISION_TOOL.get(kind)
    if tool is None:
        raise ToolNotAllowed(f"Decision '{kind}' does not map to an allowlisted tool.")
    return enforce_tool_allowlist(tool)


def is_high_blast_radius(
    command: RemediationCommand, *, service_tags: Iterable[str] = ()
) -> bool:
    """A command is high blast radius if any of the following hold:

    * its action is inherently destructive (``HIGH_BLAST_RADIUS_ACTIONS``); or
    * it targets a server-classified critical-tier service with a
      ROLLBACK/RESTART (audit C2 -- a SERVER-side decision keyed on
      ``service_tags``, never on anything the model returned); or
    * the reasoner's own ``requires_human_approval`` flag is set.

    That last check is intentionally an OR, not the sole signal (audit C2,
    "do not trust the model flag"): the model can only ever ADD caution by
    setting it, never remove the caution the first two, server-controlled
    checks already established. A model that lies and reports ``false`` for
    a checkout rollback is still caught by the critical-tier check above.
    """

    critical_tier_destructive = (
        command.action in _CRITICAL_SERVICE_GATED_ACTIONS
        and is_critical_tier_service(service_tags)
    )
    return (
        command.action in HIGH_BLAST_RADIUS_ACTIONS
        or critical_tier_destructive
        or command.requires_human_approval
    )


def authorize_action(
    command: RemediationCommand,
    *,
    human_approved: bool,
    approver: str | None = None,
    service_tags: Iterable[str] = (),
) -> ApprovalRecord:
    """Authorize (or refuse) a remediation before it touches the database.

    * Enforces the tool allowlist for the remediation tool.
    * Refuses a high-blast-radius/irreversible action unless ``human_approved``.
    * Requires a named ``approver`` when approving such an action -- an
      anonymous approval is not an approval.
    * Classifies destructiveness SERVER-side (audit C2): ``service_tags``
      (the incident's own typed field, not reasoner output) decides whether
      a ROLLBACK/RESTART on a critical-tier service requires approval,
      regardless of what the model claimed.

    Returns an :class:`ApprovalRecord` on success (for the caller to audit); the
    caller must record it. Raises :class:`DestructiveActionBlocked` on refusal.
    """

    tool = enforce_tool_allowlist("remediate_and_record")
    high_blast = is_high_blast_radius(command, service_tags=service_tags)

    if not high_blast:
        return ApprovalRecord(
            tool=tool,
            action=command.action.value,
            high_blast_radius=False,
            approved=True,
            approver=approver,
            reason="Reversible, single-target, auto-approved action.",
        )

    if not human_approved:
        raise DestructiveActionBlocked(
            f"Action '{command.action.value}' is high blast radius and requires "
            f"explicit human approval before execution (R5)."
        )
    if not approver or not str(approver).strip():
        raise DestructiveActionBlocked(
            f"Approval of high-blast-radius action '{command.action.value}' must "
            f"name the approving human; anonymous approval is refused (R5, R10)."
        )
    return ApprovalRecord(
        tool=tool,
        action=command.action.value,
        high_blast_radius=True,
        approved=True,
        approver=str(approver).strip(),
        reason="High-blast-radius action approved by a named human.",
    )
