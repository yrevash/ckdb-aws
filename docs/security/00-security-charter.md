# Postmortem — Security Charter (v1)

The single source of truth for security. Every security implementation + doc must obey this charter.
Builds on the Track C baseline in [`docs/HARDENING.md`](../HARDENING.md) — extend it, do not duplicate.

Grounding: `research/postmortem/03-aws-infrastructure.md` (§security), `research/postmortem/04-cockroachdb-
deployment-resilience.md` (RBAC/audit), `research/postmortem/00-charter.md` (the wedge).

## 1. Why security is core here (not a bolt-on)

Postmortem is an agent that **acts on real operational data** with real credentials. It is a
**privileged, semi-autonomous actor**. That makes it a higher-value target and a higher-blast-radius
failure than a read-only chatbot. Security *is* the product's production-readiness story.

## 2. Security principles (SOTA, enterprise)

1. **Least privilege, everywhere.** Every identity (agent role, Lambda, Fargate task, service account,
   human) gets the minimum grant for its job — enforced at the IAM *and* SQL-grant layers.
2. **Deny by default / secure by default.** No implicit allow. New resources are private, encrypted,
   and unexposed until explicitly opened.
3. **Defense in depth.** No single control is trusted alone: IAM ∧ SQL grants ∧ Guardrails ∧
   app-layer validation must all hold.
4. **Zero standing secrets.** No long-lived keys in code, repo, env files, logs, or the changefeed
   URI. Secrets live in AWS Secrets Manager with rotation; prefer OIDC/short-lived credentials.
5. **Encryption everywhere.** At rest (KMS/CMEK), in transit (TLS 1.2+), including CockroachDB
   connections and all AWS service calls.
6. **Human-in-the-loop for irreversible action.** Destructive/high-blast-radius agent actions require
   explicit human approval AND are blocked at the grant layer even if approval logic is bypassed.
7. **No ungrounded action (provenance-gated).** The agent may only act on a **cited, retrieved**
   memory/runbook; an action with no provenance is rejected. Every action + its memory write commit in
   **one transaction** and are audit-logged.
8. **Prompt-injection resistance.** Treat all model inputs (alerts, logs, webhooks, recalled memory) as
   untrusted. Guardrails + allowlisted tools + structured tool I/O; never let free text authorize a
   destructive tool call.
9. **Blast-radius containment.** Per-session isolation, scoped roles, tenant isolation (no
   cross-`org_id` access), and tight network segmentation.
10. **Full auditability & tamper-evidence.** Every privileged action is attributable (who/what/when/
    why) and logged to an append-only, exportable trail. Denied attempts are audited too.
11. **Data minimization & residency.** Store only what's needed; keep each tenant's data in its region
    (`REGIONAL BY ROW`); classify data (see §5).
12. **Observability & anomaly detection.** Security-relevant events are monitored; unusual agent
    behavior (action spikes, repeated denials, novel targets) raises alerts.
13. **Reproducible, immutable infra.** All infra as code (CDK); no manual console changes; changes are
    reviewed and diffable.

## 3. Threat model (what we defend against — prioritized)

| # | Threat | Primary defenses |
|---|--------|------------------|
| T1 | **Prompt injection → unauthorized/destructive action** | Guardrails, tool allowlist, provenance gate, human approval, SQL grant deny, structured tool I/O |
| T2 | **Over-privileged credentials / lateral movement** | Least-privilege IAM + SQL roles (reader/writer/consolidator), per-component roles, no shared creds |
| T3 | **Memory poisoning** (bad memory → bad action) | Consolidator writes separated from agent writes; provenance + confidence + success-rate gates; human-approved runbooks only |
| T4 | **Secret leakage** | Secrets Manager, no secrets in repo/logs/URIs, rotation, scanning |
| T5 | **Data exfiltration / cross-tenant leakage** | Scoped queries by `org_id`, RBAC, network egress control, no broad SELECT |
| T6 | **Supply-chain compromise** | Pinned deps + lockfiles, provenance, minimal base images, dependency scanning |
| T7 | **Tampering with the audit trail** | Append-only + exportable audit logs, admin-audit, least-privilege on log config |
| T8 | **Public exposure of DB / console / data** | PrivateLink to CockroachDB, private subnets, WAF on the console, no public S3, auth on the console |

## 4. Non-negotiable rules (guardrails — enforce, don't just document)

- **R1** IAM & SQL grants are deny-by-default and least-privilege; no `*:*`, no wildcard resource on
  write actions, no `PUBLIC` grants.
- **R2** No secret in source, env-committed files, logs, or connection URIs. Secrets Manager only.
- **R3** Destructive DB ops (`DROP`/`TRUNCATE`/mass `DELETE`/prod topology changes) are unreachable by
  the agent's roles; the agent's MCP path is read-only; writes go through the scoped writer role.
- **R4** Every agent action is **provenance-cited** and written **in the same transaction** as its
  episodic record; ungrounded actions are rejected before execution.
- **R5** High-blast-radius actions require explicit human approval; approval + actor are recorded.
- **R6** Bedrock **Guardrails** screen all model inputs and outputs (prompt injection, sensitive data,
  harmful content) on both the responder and the consolidator.
- **R7** Reader / writer / consolidator roles stay separated; the agent's synchronous path can never
  write learned memory (`semantic_facts`/`procedural_memory`).
- **R8** Encryption at rest (KMS) and in transit (TLS) is mandatory; DB connections verify TLS.
- **R9** All external inputs (alerts, webhooks, form fields) are validated/typed before use; changefeed
  webhooks are authenticated.
- **R10** Every privileged action and every denied attempt is audit-logged and exportable.

## 5. Data classification

- **Secret:** credentials, service-account keys, API keys → Secrets Manager, never persisted in app tables.
- **Sensitive/operational:** incidents, orders, deploy state, memory → CockroachDB, RBAC + audit + encryption.
- **Internal:** metrics, telemetry, eval reports → access-controlled, not public.
- **Public:** the open-source code + docs (this repo) — must contain **no** secrets or real customer data.

## 6. Compliance targets (map controls to these)

- **AWS Well-Architected — Security Pillar** (identity, detection, infra protection, data protection, IR).
- **CIS Benchmarks** — CockroachDB (Track C started this: CIS 6.3 pass, 6.4 open) + AWS Foundations.
- **NIST** — CSF functions (Identify/Protect/Detect/Respond/Recover) + relevant 800-53 controls.
- **EU AI Act / agentic-AI governance** — provenance, human oversight, logging of autonomous decisions,
  risk management for a high-autonomy system.
- **SOC 2** control families (Security, Availability, Confidentiality) — as a documented posture, not a
  certification.

## 7. Ownership map (who implements what)

| Domain | Owner doc/impl | Scope |
|--------|----------------|-------|
| **AWS infrastructure security** | `docs/security/01-aws-infrastructure-security.md` + `infra/` CDK | IAM least-privilege, Secrets Manager, KMS, VPC + PrivateLink, security groups, WAF on console, Bedrock Guardrails config, CloudTrail/GuardDuty/Config, private S3, least-priv Lambda/Fargate roles |
| **Agentic & application guardrails** | `docs/security/02-agent-and-app-guardrails.md` + `backend/` + `web/` | Tool allowlist, provenance gate, human-approval gate, prompt-injection defenses, input validation, role-scoped DB access in code, secure HTTP headers, no client secrets, changefeed webhook auth |
| **Governance & compliance** | `docs/security/03-governance-and-compliance.md` (+ THREAT_MODEL, controls matrix) | Threat model detail, control-to-compliance matrix (WA/CIS/NIST/EU-AI-Act/SOC2), data governance, security IR runbook, secure-SDLC/supply-chain policy |

## 8. Rules of engagement for implementers

- Keep `scripts/verify_phase2.sh` (and `verify_phase3.sh`) **green** — security is additive/hardening,
  not a regression. Add tests for new guardrail logic.
- Mark every control **[implemented+tested]** vs **[deploy-time / pending real AWS Aug 1]** — accuracy
  over claims. Do not claim a control is enforced if it only exists in a design doc.
- Stay in your ownership lane; reuse existing venvs; for live-DB tests spin your own throwaway node on a
  unique port (do not touch the shared node).
- Least-privilege and deny-by-default are the tie-breakers whenever a design choice is ambiguous.
