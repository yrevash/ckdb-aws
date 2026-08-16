# Postmortem — Governance & Compliance (v1)

Owner: **Governance & compliance** (charter §7). This is the governance narrative that ties the charter's
principles to operating policy, and the home of the **data policy**, the **human-oversight / accountability
model**, the **secure-SDLC & supply-chain policy**, the **secrets policy**, the **security incident-response
runbook**, and the **honest compliance posture** for the hackathon.

**Companion documents (read together):**
- `00-security-charter.md` — the contract: principles (§2), threats T1–T8 (§3), rules R1–R10 (§4),
  data classification (§5), compliance targets (§6), ownership (§7).
- `THREAT_MODEL.md` — detailed STRIDE analysis, kill-chains, trust boundaries.
- `CONTROLS_MATRIX.md` — every control → WA-Sec / CIS / NIST / EU-AI-Act / SOC 2, with honest status.
- `HARDENING.md` — the Track C controls proven live against CockroachDB v26.2.0.

**Status convention** (per charter §8): **[enforced+tested]** runs today; **[deploy-time]** is real on the
live AWS deployment (locally exercised via the `fake` runtime); **[planned]** is a named follow-up.
Accuracy over hype — a control in a design doc is not an enforced control.

---

## 1. Security principles in practice

The charter's 13 principles (§2) are not aspirations here; each maps to a concrete, mostly-verifiable
practice. The five that most define this system:

1. **Least privilege, at two layers.** Every identity is scoped at *both* IAM and SQL. The load-bearing
   example is real today: the agent's synchronous path uses `postmortem_writer`, which can write exactly
   six tables and **cannot** touch `semantic_facts`, `DROP`, `TRUNCATE`, or another tenant — asserted by
   query, not by convention (`HARDENING.md` §2). **[enforced+tested]**
2. **Defense in depth — no single control trusted alone.** The flagship case: a prompt-injected destructive
   action must pass *five* independent layers (Guardrails → structured tool I/O → provenance gate →
   human approval → **SQL grant deny**). The last layer holds even under total LLM compromise and is real
   today (`THREAT_MODEL.md` §6 Scenario 1).
3. **No ungrounded action (provenance-gated).** The agent may act only on a *cited, retrieved* memory; an
   action with no provenance is rejected, and the action + its episodic record commit in **one
   transaction** (R4, the wedge). **[deploy-time for the gate; enforced+tested for the one-transaction
   atomicity]**
4. **Human-in-the-loop for irreversible action.** High-blast-radius actions require explicit human approval
   *and* are blocked at the grant layer even if approval logic is bypassed (R5 ∧ R3) — approval is an
   *additional* gate, never the only one.
5. **Full auditability & tamper-evidence.** Three CockroachDB audit mechanisms (table-level, role-based,
   admin) capture every privileged write and **every denied attempt**, attributed to the real principal.
   **[enforced+tested]** (`HARDENING.md` §3).

The remaining principles (deny-by-default, zero standing secrets, encryption everywhere, blast-radius
containment, data minimization/residency, observability, immutable infra) are traced control-by-control in
`CONTROLS_MATRIX.md`.

---

## 2. Data classification & residency policy

### 2.1 Classification (charter §5) and the handling rule for each

| Class | Examples | Handling rule | Status |
|---|---|---|---|
| **Secret** | CockroachDB reader/writer/consolidator DSNs, MCP service-account keys, Bedrock/AWS creds, changefeed webhook secret | AWS Secrets Manager only, rotation on, readable by exactly one owning role's ARN. **Never** in source, committed env files, logs, or the changefeed URI (creds go in an `EXTERNAL CONNECTION`). | **[deploy-time]** (Secrets Manager) |
| **Sensitive / operational** | incidents, orders, deploys, service state, all memory (episodic/semantic/procedural) + provenance | CockroachDB with RBAC + audit + encryption at rest/in transit; per-`org_id` scoping; large blobs offloaded to S3 (reference-only in rows). | **[enforced+tested]** RBAC/audit; **[deploy-time]** encryption/S3 |
| **Internal** | metrics, telemetry, eval reports | Access-controlled, not public; no secrets. | **[deploy-time]** |
| **Public** | the open-source repo (code + docs) | **Must contain no secrets and no real customer data.** Enforced by repo hygiene + secret scanning. | **[enforced+tested policy]**; scanning **[planned]** |

### 2.2 Residency

- **Tenant data stays in its region.** With `REGIONAL BY ROW`, each tenant's incidents, memory, *and* the
  C-SPANN vector-index entries for those rows are homed and searched in that tenant's region — under full
  ACID consistency, not the usual "pick latency or consistency" tradeoff (doc `04` §3.4). **[deploy-time]**
- **Data minimization.** Only reference + embedding + metadata live in the hot row; raw postmortems /
  transcripts / large tool outputs live in S3 (SSE-KMS, Block Public Access). Keeps sensitive footprint
  small and respects CockroachDB's ~1 MB row / ~16 MB txn guidance. **[deploy-time]**
- **AWS compute is single-region (`us-east-1`); the store is multi-region.** This is deliberate and safe:
  RPO=0 is CockroachDB's property, not the compute tier's — compute failing over around a region kill does
  not risk data (doc `03` §6). Cross-tenant residency is a *data-store* guarantee, unaffected by
  single-region compute.

---

## 3. Human-oversight & accountability model (EU AI Act alignment)

Postmortem is a **high-autonomy system acting on real infrastructure** — the class of system the EU AI Act's
agentic/high-risk provisions target. Our oversight model maps to four obligations:

### 3.1 Effective human oversight (Art. 14)

- **Approval gate on irreversible action.** Any action the system classifies as high-blast-radius
  (destructive DB ops, prod topology change, mass mutation) pauses for **explicit human approval** in the
  console; the approver identity and decision are recorded. **[deploy-time]**
- **Oversight that cannot be over-ridden by the model.** Approval is *necessary but not sufficient*: the
  action is *also* blocked at the SQL grant layer (R3), so a subverted agent that fabricates an approval
  still cannot execute a destructive statement — its writer role has no such grant (`HARDENING.md` §2).
  **[enforced+tested]** This is the crucial property: oversight degrades safely, never to "the model
  decided it was fine."
- **Meaningful, not rubber-stamp, oversight.** The human sees the recalled memory the action is grounded in
  (the provenance), the similarity score, and the proposed change before approving — the console surfaces
  the "why," not just a yes/no. **[deploy-time]**

### 3.2 Automatic record-keeping / logging of autonomous decisions (Art. 12)

- Every autonomous action and its justification commit **atomically** (the one-transaction wedge) and are
  written to an append-only, exportable audit trail attributed to the real principal — including **denied
  attempts**. **[enforced+tested]** (`HARDENING.md` §3–§4). This is the strongest EU-AI-Act claim we can
  make *today*: the decision log is not bolted on, it is inseparable from the action itself.

### 3.3 Risk management & robustness/cybersecurity (Art. 9, Art. 15)

- `THREAT_MODEL.md` is the documented, maintained risk analysis (assets, boundaries, STRIDE, kill-chains,
  residual risk). Prompt-injection robustness is layered defense-in-depth (§1.2 above). **[documented;
  mitigations mixed IT/DT]**

### 3.4 Data governance (Art. 10)

- Classification (§2.1), residency (§2.2), minimization, and provenance archiving (every consolidation's
  exact Bedrock prompt+response to S3) constitute the data-governance regime. **[policy IT; storage DT]**

### 3.5 Accountability chain (who is answerable for an autonomous action)

1. **The action** → attributable to `postmortem_agent_writer` in the audit log (which principal). **[IT]**
2. **The justification** → the episodic record committed in the same transaction (why: the cited memory).
   **[IT]**
3. **The approval** (if high-blast-radius) → the recorded human approver (who authorized). **[DT]**
4. **The learned memory** it acted on → provenance rows + S3 archive tracing back to source episodes.
   **[DT]**
5. **The config** that permitted it → admin-audit of every GRANT/REVOKE/cluster-setting change. **[IT]**

No autonomous action exists without an attributable actor, a cited justification, and (for irreversible
ones) a human authorizer.

---

## 4. Secrets-management policy (charter R2)

- **Single source:** AWS Secrets Manager. Secrets held: CockroachDB reader / writer / consolidator DSNs,
  MCP service-account key, changefeed webhook shared secret, any Bedrock/AWS-scoped creds. **[deploy-time]**
- **Zero standing secrets:** prefer OIDC / short-lived credentials; rotation enabled where the tier
  supports it; service-account keys treated exactly like DB passwords (doc `04` §4.1). **[deploy-time]**
- **Least-privilege read:** each role/compute unit can read only its own secret ARN — the receiver Lambda
  cannot read the writer DSN, etc. **[deploy-time]**
- **Never in the clear:** no secret in source, committed env files, container images, logs, or the
  changefeed connection URI (creds live in an `EXTERNAL CONNECTION`, not the URI). CockroachDB audit logs
  redact literal values by default. **[enforced+tested for redaction/repo hygiene; deploy-time for the rest]**
- **Local dev exception (documented):** local `docker compose` uses a `root`-as-everything DSN in
  `.env.example` — acceptable *only* for the insecure local node; production wiring points
  `postmortem_agent_reader`/`_writer` at their own Secrets-Manager-sourced DSNs (`HARDENING.md` §6).

---

## 5. Secure-SDLC & supply-chain policy (charter R6; threat T6)

- **Pinned dependencies + lockfiles.** Python and pnpm dependencies are pinned with lockfiles committed —
  reproducible builds, no drifting transitive dependency. **[enforced+tested — lockfiles present]**
- **Dependency & image vulnerability scanning.** CI should scan dependencies and container base images and
  **gate on high/critical** findings before deploy. **[planned]** — the one SDLC control not yet wired; a
  named follow-up, not claimed as done.
- **Minimal base images.** ARM64 slim runtimes; smaller attack surface (doc `03` §1.2). **[deploy-time]**
- **Review + green verifiers before merge.** Changes are peer-reviewed and must keep `verify_phase2.sh` /
  `verify_phase3.sh` green — security is additive hardening, never a regression (charter §8; add tests for
  new guardrail logic). **[enforced+tested]**
- **Immutable, reviewed infra.** All infra as CDK; no manual console changes; changes are diffable and
  reviewed; drift surfaced by admin-audit + Config. **[deploy-time]**
- **AI-decision provenance.** Every consolidation archives its exact Bedrock prompt + response + token
  counts to S3 — supply-chain-style provenance for the AI outputs, not just the code. **[deploy-time]**
- **Repo hygiene.** The public repo must never contain secrets or real customer data (charter §5 Public).
  **[enforced+tested policy]**

---

## 6. Security incident-response runbook — compromise of the agent/system itself

Scope: this runbook is for a **security compromise of Postmortem** (subverted agent, stolen credential,
poisoned memory, tampering) — *not* the operational-incident response the agent itself performs. Phases:
**Detect → Contain → Revoke → Restore-from-PITR → Audit → Recover/Learn.** It is written to be rehearsable
and maps to WA-Sec IR, NIST RS/RC, EU AI Act Art. 15, and SOC 2 CC7.

### Phase 0 — Detect

Triggers (any one):
- **Denied-attempt spike / novel target** in the SQL audit log — the `42501` privilege-violation entries
  are captured today (`HARDENING.md` §3.4); a burst is the "someone/something is probing outside its lane"
  signal. **[enforced+tested substrate; anomaly alerting = planned]**
- **Anomalous action pattern** — action-rate spike, repeated approvals denied, actions with no/weak
  provenance (charter §12). **[planned]**
- **Guardrail trips** (prompt-attack / denied-topic) at abnormal rate. **[deploy-time]**
- **Control-plane change** you didn't make — admin-audit shows an unexpected GRANT/REVOKE/cluster-setting
  change (`HARDENING.md` §3.1). **[enforced+tested]**
- External signal — GuardDuty / CloudTrail / Config finding. **[deploy-time]**

### Phase 1 — Contain (stop the bleeding, preserve evidence)

1. **Freeze the agent's act path.** Revoke or disable the `postmortem_writer` grant / rotate the writer key
   so no further writes land. Recall (reader) can stay up for investigation if needed — the recall/act
   split means you can cut *acting* without cutting *reading*. **[enforced+tested — the grant boundary exists]**
2. **Pause high-blast-radius flow.** Disable the changefeed→consolidator path (stop distilling potentially
   poisoned episodes into runbooks) and require human approval on *all* actions, not just high-risk.
3. **Do not destroy evidence.** The audit trail is append-only; export the current SENSITIVE_ACCESS /
   role / admin channels immediately (to CloudWatch / S3) before any node cycling. **[export = deploy-time;
   local trail = enforced+tested]**
4. **Snapshot the timeline.** Note `cluster_logical_timestamp()` now and the suspected first-bad-write time
   — you will need both for PITR (Phase 3).

### Phase 2 — Revoke (cut the attacker's access)

1. **Rotate every potentially exposed secret** in Secrets Manager — writer DSN first, then MCP keys,
   webhook secret, any Bedrock/AWS creds in scope. **[deploy-time]**
2. **Invalidate live sessions.** (Interim until CIS 6.4 idle-timeout lands: force-rotate credentials, which
   invalidates sessions bound to them — `HARDENING.md` §6.) **[timeout = planned; rotation = deploy-time]**
3. **Tighten grants** if the compromise revealed an over-broad one; re-assert the least-privilege baseline
   (reader/writer/consolidator scoping, no `PUBLIC` schema CREATE).

### Phase 3 — Restore-from-PITR (undo the damage)

1. Identify **T1 = the last-known-good instant** before the first bad write (from Phase 1.4 + the audit
   trail, which shows exactly what the writer touched and when).
2. `RESTORE DATABASE postmortem FROM LATEST IN '<backup>' AS OF SYSTEM TIME '<T1>' WITH new_db_name =
   'postmortem_recovered'` — restore into a **scratch** database first, verify, then cut over. This exact
   flow is proven end-to-end locally (`scripts/backup_pitr_smoke.sh`, `HARDENING.md` §4.2). **[enforced+tested
   locally; production S3-backed schedule = deploy-time]**
3. **Memory poisoning specifically:** because learned facts/runbooks carry provenance, you can either PITR
   the whole store to before the poison, or surgically identify and retire the poisoned facts/runbooks via
   their provenance rows + S3 archive. Prefer PITR-to-scratch + diff for confidence.
4. **RPO=0 note:** replication protects against infra loss but *replicates* a logically-bad write — PITR is
   the *only* mechanism that answers "the agent's write was wrong" (`HARDENING.md` §4.1). This is why both
   exist.

### Phase 4 — Audit (reconstruct exactly what happened)

- Use the three audit channels to answer: **which principal**, **which statements**, **which tables**,
  **when**, and **which attempts were denied** — all attributable to `postmortem_agent_writer`, not just a
  role (`HARDENING.md` §3.4). **[enforced+tested]**
- Correlate with control-plane audit (`ccloud audit list`) for any config change, and CloudTrail for AWS
  actions. **[deploy-time]**
- Determine root cause on the T1–T8 map (injection? stolen cred? poisoned memory? config weakening?).

### Phase 5 — Recover & learn

- Cut the verified scratch DB over to live; re-enable the act path with restored grants; re-enable
  consolidation once the source episodes are confirmed clean.
- Post-incident: update `THREAT_MODEL.md` if a new path was found; add a guardrail test to the verifiers so
  the same class of compromise is caught next time (secure-SDLC §5); close any control that Phase 4 showed
  was `[deploy-time]`/`[planned]` and material.

**Recovery objectives (honest):** RPO=0 for *infrastructure* loss (proven, local 9-node sim);
point-in-time recovery to any pre-corruption instant for *logical* damage (proven locally); no committed
RTO for the *security-IR* process itself at hackathon stage — it is documented and rehearsable, not
SLA-backed.

---

## 7. Compliance posture summary (what we can credibly claim)

**The honest split** — what is real today vs. what a formal audit would require. Full traceability is in
`CONTROLS_MATRIX.md` §8.

### What we can credibly claim for the hackathon

- **AWS Well-Architected Security Pillar — alignment across all five areas.** The *detection* and *incident
  response* areas are **real today** at the data layer: three CockroachDB audit mechanisms with
  denied-attempt capture, PITR proven end-to-end, and RPO=0 region survival proven on a 9-node simulated
  multi-region cluster. Identity is real at the SQL layer (reader/writer split, no `PUBLIC` schema CREATE).
  Data-protection and infrastructure-protection controls (KMS, Secrets Manager, PrivateLink, WAF) are
  designed and locally exercised, activating on the live deployment.
- **CIS — measurable progress, stated exactly.** CockroachDB Benchmark **6.3 (audit logging): PASS** (was
  FAIL before `0007_audit_logging.sql`). **6.4 (idle-session timeout): OPEN** — documented, not fixed.
  We surfaced and worked around two real skill-vs-v26.2 gaps (schema_locked vs `EXPERIMENTAL_AUDIT`;
  `user_audit` accepting only ALL/NONE) — production-readiness findings in their own right (`HARDENING.md`
  §3.2–§3.3). AWS Foundations controls are mapped and deploy-time.
- **NIST CSF — all five functions touched**, with Detect (audit) and Recover (PITR + region survival) real
  today.
- **EU AI Act / agentic governance — the record-keeping obligation (Art. 12) is partly real today**: the
  one-transaction wedge makes every autonomous decision's log inseparable from the action, attributed to a
  real principal, with denied attempts captured. Human oversight (Art. 14) via the approval gate + the
  grant-layer backstop, and robustness (Art. 15) via layered prompt-injection defense, are designed and
  locally exercised (deploy-time for the Bedrock/console pieces).
- **SOC 2 — a documented posture**, not a certification, across CC6 (access), CC7 (monitoring), CC8
  (change), A1 (availability), C1 (confidentiality). CC7 and A1 have the strongest *today* evidence
  (audit + PITR + region survival).

### What would need a formal audit (and what we do NOT claim)

- **We do not claim any certification** — not SOC 2, not CIS-certified, not an EU AI Act conformity
  assessment. These are *alignment* and *posture* claims backed by specific controls, several of which are
  still `[deploy-time]`.
- **The AWS-boundary controls are not yet running on a live account** (Bedrock Guardrails, KMS/CMEK,
  Secrets Manager, PrivateLink, WAF, CloudTrail/GuardDuty/Config, log export). They are designed and
  exercised through the `fake` runtime; nothing here claims live AWS is deployed (README status table).
- **PITR is proven with `nodelocal://` storage locally**, not the scheduled `s3://…?AUTH=implicit` Cloud
  backup — the SQL surface is identical, only the storage URI changes, so the proof generalizes, but the
  scheduled Cloud backup itself is deploy-time.
- **CIS 6.4 is open**, and **anomaly-based detection / CI vulnerability-scanning gates are planned**, not
  implemented. A formal audit would test these, and they would currently be findings.
- **A formal audit would require** independent evidence collection over a monitoring period, control-
  operating-effectiveness testing (not just design), and third-party assessment — none of which a
  hackathon build provides. Our claim is precise: **a defensible, honestly-scoped security posture with a
  meaningful set of controls enforced and tested today, and the rest designed to a documented standard and
  ready to activate on deployment.**

---

## 8. Cross-reference index

| Need | Document |
|---|---|
| Principles, rules R1–R10, threats T1–T8, ownership | `00-security-charter.md` |
| Detailed STRIDE, trust boundaries, kill-chains, residual risk | `THREAT_MODEL.md` |
| Every control → WA/CIS/NIST/EU-AI-Act/SOC2 with status | `CONTROLS_MATRIX.md` |
| Live-proven Track C controls (roles, audit, PITR, CIS) | `HARDENING.md` |
| AWS infrastructure controls (IAM/KMS/PrivateLink/Guardrails/etc.) | `01-*` (sibling, by capability) |
| Agent/app guardrails (provenance gate, approval, tool allowlist) | `02-*` (sibling, by capability) |
