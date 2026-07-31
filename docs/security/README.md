# Security

Enterprise-grade security posture for Postmortem — a **privileged, semi-autonomous agent that acts on
real operational data**. Security is treated as core (the production-readiness story), not a bolt-on.

## Read order
1. [`00-security-charter.md`](./00-security-charter.md) — principles, threat model, the 10
   non-negotiable rules, data classification, compliance targets, ownership map. **Start here.**
2. [`01-aws-infrastructure-security.md`](./01-aws-infrastructure-security.md) — IAM least-privilege,
   KMS, Secrets Manager, private VPC + PrivateLink, WAF, Bedrock Guardrails, CloudTrail/GuardDuty/Config
   (CDK, 25 synth tests).
3. [`02-agent-and-app-guardrails.md`](./02-agent-and-app-guardrails.md) — tool allowlist +
   human-approval gate, provenance gate (no ungrounded action), prompt-injection defenses, input
   validation + authenticated webhook, role-scoped DB access, web security headers.
4. [`THREAT_MODEL.md`](./THREAT_MODEL.md) — STRIDE analysis, kill-chains, trust boundaries.
5. [`CONTROLS_MATRIX.md`](./CONTROLS_MATRIX.md) — ~45 controls mapped to AWS Well-Architected / CIS /
   NIST CSF / EU AI Act / SOC 2, each tagged implemented-tested / deploy-time / planned.
6. [`03-governance-and-compliance.md`](./03-governance-and-compliance.md) — governance narrative,
   human-oversight model, secure-SDLC, security incident-response runbook, honest posture statement.
7. [`../HARDENING.md`](../HARDENING.md) — the Track C database hardening baseline (audit logging,
   reader/writer roles, PITR) these build on.

## The one thing to remember

The flagship defense — **prompt-injection → destructive action is stopped even under total LLM
compromise** — because the agent's SQL role structurally cannot `DROP`/`TRUNCATE`/mass-`DELETE` (grant
deny, and the denied attempt is itself audited), and no action executes without a resolved provenance
citation committed in the same transaction. Defense in depth that does not trust the model.

## Honesty

Every control is tagged **implemented+tested** (runs in `scripts/verify_phase2.sh` today) vs
**deploy-time** (needs the real AWS account, Aug 1) vs **planned**. No certification is claimed; the
posture is "AWS Well-Architected Security-Pillar aligned + CIS in progress," not audited.
