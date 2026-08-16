# Postmortem — Controls-to-Compliance Matrix (v1)

Owner: **Governance & compliance** (charter §7). This is the single traceability table from every security
control to the **compliance targets in charter §6**. It is the evidence spine behind the posture summary in
`03-governance-and-compliance.md` §8.

**Sources:** charter §2/§4 (principles + rules R1–R10), `docs/HARDENING.md` (Track C, proven live against
CockroachDB v26.2.0), `research/postmortem/03-aws-infrastructure.md` §4 (AWS controls), and
`research/postmortem/04-cockroachdb-deployment-resilience.md` §4–§6. AWS-infra (`01-*`) and agent/app
guardrail (`02-*`) controls are referenced **by capability** — those docs own the exact implementation.

## Legend

**Status** (honest, per charter §8 — a design doc is *not* an enforced control):
- **IT** — *implemented + tested*: runs today against a live node or in the local test suites
  (`audit_check.sh`, `backup_pitr_smoke.sh`, `verify_phase2/3.sh`).
- **DT** — *deploy-time*: designed and locally exercised via the `fake` runtime; becomes real on the live
  AWS / CockroachDB-Cloud deployment (pending).
- **PL** — *planned*: designed, a named follow-up, not yet implemented anywhere.

**Enforced-at:** IAM · SQL (CockroachDB grants/settings) · APP (backend/agent code) · INFRA (CDK/network/
Bedrock/AWS) · PROC (process/policy).

**Framework columns:**
- **WA-Sec** — AWS Well-Architected Security Pillar area: IAM (identity & access mgmt), DP (data
  protection), IP (infrastructure protection), DET (detection), IR (incident response).
- **CIS** — CIS CockroachDB Benchmark control # or CIS AWS Foundations area.
- **NIST** — NIST CSF function: ID / PR / DE / RS / RC.
- **EU AI Act** — the agentic-AI obligation the control serves (Art. 9 risk mgmt, Art. 10 data governance,
  Art. 12 record-keeping/logging, Art. 14 human oversight, Art. 15 accuracy/robustness/cybersecurity).
- **SOC 2** — Trust Services Criterion: CC6 (logical access), CC7 (operations/monitoring), CC8 (change
  mgmt), A1 (availability), C1 (confidentiality).

---

## 1. Identity, access & least privilege (charter R1, R7; threats T1, T2, T5)

| Control | What it does | Enforced-at | Status | WA-Sec | CIS | NIST | EU AI Act | SOC 2 |
|---|---|---|---|---|---|---|---|---|
| Reader/writer role split (`postmortem_reader` / `postmortem_writer`) scoped to the exact 6 tables the agent's code writes | Makes "recall vs. act" a real RBAC boundary, not a convention; reader has zero write grants anywhere | SQL | **IT** (HARDENING §2.2) | IAM | 5.x user access | PR.AC | Art. 15 | CC6 |
| Writer role cannot reach `semantic_facts` / `DROP` / `TRUNCATE` / mass-DELETE | Structural backstop: destructive & memory-poisoning writes are unreachable even under full LLM compromise (R3) | SQL | **IT** (HARDENING §2.2) | IAM | 5.x | PR.AC / PR.DS | Art. 15 | CC6 |
| `REVOKE CREATE ON SCHEMA postmortem.public FROM public` | Removes default-granted schema-CREATE from every principal (a real baseline finding) | SQL | **IT** (HARDENING §2.3) | IAM | 5.x | PR.AC | Art. 15 | CC6 |
| Consolidator role (`postmortem_consolidator`) scoped to the distillation write-set only | Sleep-time job writes memory without widening the agent's grant | SQL | **PL** (HARDENING §6) | IAM | 5.x | PR.AC | Art. 15 | CC6 |
| One least-privilege IAM role per compute unit (Fargate task / receiver Lambda / consolidator Lambda); no shared "god role", no `*:*`, no wildcard write resource | Blast-radius containment; no lateral movement from one credential | IAM/INFRA | **DT** (doc `03` §4) | IAM | AWS-IAM | PR.AC | Art. 15 | CC6 |
| Bedrock `InvokeModel` scoped to specific model + inference-profile ARNs (not `*`) | Agent can only call the models it needs | IAM/INFRA | **DT** (doc `03` §4) | IAM | AWS-IAM | PR.AC | Art. 15 | CC6 |
| Managed MCP recall via read-only service account, system-table deny-listed | Read "hands" that cannot mutate; destructive DDL unreachable via MCP | SQL/APP | **DT** (doc `04` §4.2; README) | IAM | 5.x | PR.AC | Art. 15 | CC6 |
| Idle-in-session timeout per role (CIS 6.4) | Bounds a stolen live session | SQL | **PL** (open finding, HARDENING §5/§6) | IP | **6.4** | PR.AC | Art. 15 | CC6 |

## 2. Agentic guardrails & human oversight (charter R3–R6, R9; threats T1, T3)

| Control | What it does | Enforced-at | Status | WA-Sec | CIS | NIST | EU AI Act | SOC 2 |
|---|---|---|---|---|---|---|---|---|
| Provenance gate — action with no cited retrieved memory/runbook is rejected before execution (R4) | No ungrounded action; the anti-hallucination control | APP | **DT** (§02; design proven via bitemporal/provenance locally) | IR/DP | — | PR.DS / PR.IP | **Art. 14 / Art. 15** | CC6/CC7 |
| One-transaction wedge — action + its episodic record commit atomically | Action and its justification are inseparable & non-repudiable | APP/SQL | **IT** (README; live serializable proof) | DET | — | PR.PT / DE.AE | **Art. 12** | CC7 |
| Human-approval gate for high-blast-radius actions; approver + actor recorded (R5) | Human-in-the-loop for irreversible action | APP | **DT** (§02; doc `03` §4) | IR | — | RS.RP | **Art. 14** | CC6 |
| Tool allowlist + structured tool I/O — free text can never become a tool call (R6, R8) | Prompt-injection containment at the tool boundary | APP | **DT** (§02) | IP | — | PR.AC | **Art. 15** | CC6 |
| Bedrock Guardrails on responder input & consolidator output (prompt-attack, denied-topics, sensitive-info, contextual grounding) (R6) | Screens injected content and poisoned distillation | INFRA | **DT** (doc `03` §1.2, §4) | DP/IP | — | PR.DS / DE.CM | **Art. 15** | CC7 |
| Input validation/typing of all external inputs; changefeed webhook authenticated (R9) | Untrusted input never trusted; forged webhook rejected | APP/INFRA | **DT** (§02; doc `03` §3) | IP | — | PR.DS | Art. 15 | CC6 |
| Consolidator writes separated from agent writes; human-approved runbooks only (R7) | Memory-poisoning containment | SQL/APP | **IT** (grant split) / **DT** (approval) | DP | 5.x | PR.DS | Art. 15 | CC6/C1 |

## 3. Data protection, encryption & residency (charter R2, R8; threats T4, T5, T8)

| Control | What it does | Enforced-at | Status | WA-Sec | CIS | NIST | EU AI Act | SOC 2 |
|---|---|---|---|---|---|---|---|---|
| Encryption in transit — TLS 1.2+ on all DB & AWS connections; DB conns verify TLS (R8) | No cleartext data on the wire | INFRA/APP | **DT** (charter §2.5; doc `03`) | DP | AWS-DP | PR.DS | Art. 15 | C1 |
| Encryption at rest — KMS/SSE-KMS on S3; CMEK available on Advanced tier (R8) | No cleartext data at rest | INFRA | **DT** (doc `03` §1.2; doc `04` §6.3) | DP | AWS-DP | PR.DS | Art. 15 | C1 |
| Secrets in AWS Secrets Manager with rotation; no secret in source/env/logs/changefeed URI (R2) | Zero standing secrets; leak surface removed | INFRA/PROC | **DT** (doc `03` §4; doc `04` §4.1) | DP/IAM | AWS-IAM | PR.DS / PR.AC | Art. 15 | CC6/C1 |
| Audit-log literal redaction (`‹...›`) by default | Sensitive values not exposed in the audit trail | SQL | **IT** (HARDENING §3.4) | DP | 6.x | PR.DS | Art. 12 | C1 |
| Data minimization — large blobs in S3, only reference+embedding+metadata in rows | Reduces sensitive-data footprint in the hot store (charter §11) | APP | **DT** (doc `03` §1.2) | DP | — | PR.DS | **Art. 10** | C1 |
| Tenant residency — `REGIONAL BY ROW`, per-`org_id` scoping, no cross-tenant SELECT (charter §11; T5) | Data stays in-region per tenant; no cross-tenant leakage | SQL/APP | **DT** (doc `04` §3.3) | DP | — | PR.DS | **Art. 10** | C1 |
| Data classification policy (Secret / Sensitive / Internal / Public) applied to handling | Drives every storage & access decision | PROC | **IT (documented)** (charter §5; gov doc §2) | DP | — | ID.AM | Art. 10 | C1 |

## 4. Network & infrastructure protection (charter R1; threat T8)

| Control | What it does | Enforced-at | Status | WA-Sec | CIS | NIST | EU AI Act | SOC 2 |
|---|---|---|---|---|---|---|---|---|
| PrivateLink from VPC to CockroachDB Cloud; DB traffic never on public internet; synth refuses a PrivateLink deployment with no endpoint-service name (audit B2). Opt-in `crdb_egress_mode=public` documented in `01-*` | Removes internet-reachable SQL port | INFRA | **IT** (synth fail-closed) + **DT** (real endpoint-service name) | IP | AWS-NET | PR.AC / PR.PT | Art. 15 | CC6 |
| Two distinct SQL service-account DSNs delivered as two separate Secrets Manager secrets; startup fails closed on identical/same-principal DSNs | Makes reader/writer separation structural, not cosmetic | INFRA + APP | **IT** (CDK synth test + `backend/tests/test_runtime.py`) / **DT** (real DSN values) | IAM | AWS-IAM | PR.AC | Art. 15 | CC6 |
| Private subnets + security groups (deny-by-default) for compute | Blast-radius containment; no default ingress | INFRA | **DT** (doc `03` §4) | IP | AWS-NET | PR.AC | Art. 15 | CC6 |
| WAF + throttle on the changefeed API Gateway / console | Filters malicious/abusive requests | INFRA | **DT** (doc `03` §1.2, §4) | IP | AWS-NET | PR.PT / DE.CM | Art. 15 | CC7 |
| S3 Block Public Access + versioning | No public data; recoverable objects | INFRA | **DT** (doc `03` §1.2) | IP/DP | AWS-DP | PR.DS | Art. 15 | C1 |
| Immutable, reviewed IaC (CDK); no manual console changes (charter §2.13) | Diffable, reproducible, drift-detectable infra | PROC/INFRA | **DT** (doc `03` §6) | IP | — | PR.IP | Art. 15 | CC8 |

## 5. Detection, audit & monitoring (charter R10; threats T3, T7)

| Control | What it does | Enforced-at | Status | WA-Sec | CIS | NIST | EU AI Act | SOC 2 |
|---|---|---|---|---|---|---|---|---|
| Table-level `EXPERIMENTAL_AUDIT` on the 5 agent-mutated tables → SENSITIVE_ACCESS channel | Every read+write to sensitive tables logged, tagged user+table | SQL | **IT** (HARDENING §3.1) | DET | **6.3** | DE.CM / PR.PT | **Art. 12** | CC7 |
| Role-based audit (`sql.log.user_audit = 'postmortem_writer ALL'`) | Every statement by the mutating role logged cluster-wide | SQL | **IT** (HARDENING §3.1) (cluster setting via `db/bootstrap/091_audit_settings.sql`; on a managed tier that refuses it, apply.sh reports BOOTSTRAP_DEGRADED and this row is **not in force** — HARDENING §3.6) | DET | **6.3** | DE.CM | **Art. 12** | CC7 |
| Admin audit (`sql.log.admin_audit.enabled = true`) | Every schema/GRANT/cluster-setting change logged | SQL | **IT** (HARDENING §3.1) (cluster setting via `db/bootstrap/091_audit_settings.sql`; on a managed tier that refuses it, apply.sh reports BOOTSTRAP_DEGRADED and this row is **not in force** — HARDENING §3.6) | DET | **6.3** | DE.CM | Art. 12 | CC7/CC8 |
| Denied attempts audited (privilege-violation `42501` lands in the log) | "Did someone probe outside their lane" signal | SQL | **IT** (HARDENING §3.4) | DET | 6.x | DE.AE / DE.CM | Art. 12 | CC7 |
| Attribution to actual principal (`postmortem_agent_writer`), not just role | Non-repudiation of who wrote what | SQL | **IT** (HARDENING §3.4) | DET | 6.x | PR.PT | Art. 12 | CC7 |
| CIS 6.3 (audit-logging controls) | Baseline FAIL → PASS after `0007_audit_logging.sql` | SQL | **IT** (HARDENING §5) (cluster setting via `db/bootstrap/091_audit_settings.sql`; on a managed tier that refuses it, apply.sh reports BOOTSTRAP_DEGRADED and this row is **not in force** — HARDENING §3.6) | DET | **6.3 PASS** | DE.CM | Art. 12 | CC7 |
| Log export off ephemeral node disk to CloudWatch (SENSITIVE_ACCESS channel) | Durable, exportable audit evidence | INFRA | **DT** (HARDENING §3.5; doc `04` §6.2) | DET | 6.x | DE.CM / RS.AN | Art. 12 | CC7 |
| Control-plane audit (`ccloud audit list`) | Who changed cluster config / service accounts | INFRA | **DT** (doc `04` §6.2) | DET | AWS-LOG | DE.CM | Art. 12 | CC7/CC8 |
| CloudTrail / GuardDuty / AWS Config | Account-level detection & config compliance | INFRA | **DT** (charter §7; doc `03`) | DET | AWS-LOG | DE.CM | Art. 15 | CC7 |
| Anomaly detection — action spikes, repeated denials, novel targets (charter §12) | Flags unusual agent behavior | INFRA/APP | **PL** | DET | — | DE.AE | Art. 15 | CC7 |

## 6. Resilience, backup & recovery (charter wedge #3; threats T3-recover, DoS)

| Control | What it does | Enforced-at | Status | WA-Sec | CIS | NIST | EU AI Act | SOC 2 |
|---|---|---|---|---|---|---|---|---|
| SURVIVE REGION FAILURE, RF=5 (2+2+1), RPO=0 / RTO<10s | Memory + agent survive a full region loss, zero data loss | SQL/INFRA | **IT (local 9-node sim)** (README; `verify_phase3.sh`) | IR | — | RC.RP / PR.PT | Art. 15 | A1 |
| PITR with `revision_history` → restore to any pre-mutation instant into a scratch DB | Recovery from a *logically wrong* write replication can't fix | SQL | **IT (local, nodelocal)** (HARDENING §4) | IR | — | RC.RP | Art. 15 | A1 |
| Scheduled daily `BACKUP INTO s3://…?AUTH=implicit WITH revision_history` | Durable off-cluster backups | SQL/INFRA | **DT** (HARDENING §4.3; doc `04` §6.1) | IR | — | RC.RP | Art. 15 | A1 |
| Two-stage fast-ack changefeed (receiver→SQS/DLQ→consolidator) | Slow work can't backpressure/stall the memory stream | INFRA | **DT** (doc `03` §3) | IR | — | PR.PT | Art. 15 | A1 |
| SQS DLQ + alarms on failed consolidation | No silent loss of runbooks | INFRA | **DT** (doc `03` §3) | DET/IR | — | DE.CM / RS.AN | Art. 15 | CC7/A1 |
| Security incident-response runbook (detect→contain→revoke→restore→audit) | Bounded, rehearsable response to a compromise | PROC | **IT (documented)** (gov doc §7) | IR | — | RS.RP / RC.RP | **Art. 15** | CC7 |

## 7. Secure SDLC & supply chain (charter R6; threat T6)

| Control | What it does | Enforced-at | Status | WA-Sec | CIS | NIST | EU AI Act | SOC 2 |
|---|---|---|---|---|---|---|---|---|
| Pinned dependencies + lockfiles (Python, pnpm) | Reproducible builds; no drifting transitive dep | PROC | **IT (lockfiles present)** | IP | — | PR.IP / ID.SC | Art. 15 | CC8 |
| Dependency & image vulnerability scanning in CI (gate on high/critical) | Catches known-vuln deps/base images before deploy | PROC | **PL** (gov doc §6) | IP/DET | — | ID.RA / DE.CM | Art. 15 | CC7/CC8 |
| Minimal base images (ARM64 slim), Block-Public artifact stores | Smaller attack surface | INFRA | **DT** (doc `03` §1.2) | IP | — | PR.IP | Art. 15 | CC8 |
| Code review + green verifiers before merge (`verify_phase2/3.sh`) | No security regression; peer review | PROC | **IT** (charter §8; README) | IP | — | PR.IP | Art. 15 | CC8 |
| Repo hygiene — no secrets, no real customer data in the open-source repo (charter §5 Public) | Public artifact stays clean | PROC | **IT (documented policy)** | DP | — | PR.DS | Art. 10 | C1 |
| Build/artifact provenance (consolidation prompt+response archived to S3) | Traceable AI-decision provenance | INFRA/APP | **DT** (doc `03` §1.2, §3) | DET | — | PR.IP | **Art. 12** | CC7 |

---

## 8. Coverage summary (honest tally)

| Framework | What we can credibly claim | Where it's real today (IT) vs. designed (DT/PL) |
|---|---|---|
| **AWS Well-Architected — Security Pillar** | Alignment across all 5 areas (IAM, DP, IP, DET, IR) | **IT:** detection (audit), IR (PITR + region survival), RBAC identity split. **DT:** most DP/IP (KMS, PrivateLink, Secrets Manager, WAF). |
| **CIS** | CockroachDB Benchmark **6.3 PASS** (was FAIL); **6.4 open**. AWS Foundations areas mapped, deploy-time. | **IT:** 6.3 (audit). **PL:** 6.4 (idle timeout). **DT:** AWS Foundations (IAM/logging/network). |
| **NIST CSF** | All 5 functions touched (ID/PR/DE/RS/RC) | **IT:** DE (audit), RC (PITR/region), PR.AC (grants). **DT:** most PR. **PL:** DE.AE anomaly. |
| **EU AI Act (agentic)** | Art. 12 (logging of autonomous decisions) **partly real today**; Art. 14 (human oversight) & 15 (robustness/cybersecurity) designed; Art. 10 (data governance) policy-level | **IT:** Art. 12 via one-transaction wedge + audit trail. **DT:** Art. 14 human-approval + provenance gate. |
| **SOC 2** | Documented posture across CC6/CC7/CC8/A1/C1 — **not a certification** | **IT:** CC7 (audit/monitoring), A1 (resilience/PITR), CC6 (access control at SQL layer). **DT:** CC8, C1 (encryption). |

**Bottom line:** the controls that are **IT** today are the CockroachDB-layer ones (RBAC split, three audit
mechanisms, denied-attempt auditing, PITR, region survival) plus the one-transaction wedge and repo/SDLC
hygiene. The **DT** controls are the AWS-boundary ones (encryption, Secrets Manager, PrivateLink, WAF,
Guardrails, human-approval, log export) that come online with the live deployment. Nothing AWS-side is
claimed as done. See `03-governance-and-compliance.md` §8 for the full posture statement.
