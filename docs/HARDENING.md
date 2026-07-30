# Production hardening (Phase 3, Track C)

Owner: Track C. Scope per `docs/PHASE3_PLAN.md` §Track C: SQL audit logging on the tables the
agent mutates, a backup/PITR smoke test, least-privilege reader/writer grant enforcement, and
findings from running the installed CockroachDB Agent Skills against this schema. Grounded in
`research/postmortem/04-cockroachdb-deployment-resilience.md` §4 (MCP reader/writer identity
model) and §6 (production-readiness).

Everything below was **run against a live CockroachDB v26.2.0 node**, not written from memory —
see "How to reproduce" for the exact scripts, which pass from a clean node.

---

## 1. What ships in this track

| File | Purpose |
|---|---|
| `db/migrations/0007_audit_logging.sql` | `postmortem_reader`/`postmortem_writer` roles + grants, table-level `EXPERIMENTAL_AUDIT`, role-based `sql.log.user_audit`/`sql.log.admin_audit.enabled` |
| `scripts/audit_check.sh` | Self-contained: boots a throwaway node, applies all migrations, exercises allowed/denied reads+writes through the two service-account users, asserts the audit log actually captured them |
| `scripts/backup_pitr_smoke.sh` | Self-contained: boots a throwaway node, seeds data, `BACKUP`s it, simulates a buggy agent write (corrupt + delete), `RESTORE`s to the pre-mutation instant, proves recovery |
| `docs/HARDENING.md` | This document |

Both scripts are idempotent and were run back-to-back twice during this work with identical
results; each tears its container down on exit (`docker rm -f`) unless `KEEP_CONTAINER=1`.

---

## 2. Least-privilege reader/writer roles

### 2.1 Design

Doc `04`'s §4.2 calls for two separately-scoped service-account identities so "recall vs. act" is
a real RBAC boundary, not an app-level convention. `0007_audit_logging.sql` implements exactly
that, verified against the **actual write path in code**, not assumed: `grep`ing
`backend/src/postmortem_backend/adapters/cockroach.py` (`remediate_and_record`, the one-ACID-
transaction wedge) and `adapters/outcome.py` (`record_outcome`) on 2026-07-31 shows the live agent
writes to exactly six tables:

| Table | Reader | Writer |
|---|---|---|
| `organizations` | SELECT | SELECT (FK check only) |
| `services` | SELECT | SELECT, **UPDATE** (health/version/deploy pointer) |
| `deploys` | SELECT | SELECT, **INSERT** (rollback/scale/restart record) |
| `incidents` | SELECT | SELECT, **UPDATE** (status/runbook_id/mttr/resolved_at) |
| `episodic_events` | SELECT | SELECT, **INSERT** (the episodic write itself) |
| `remediation_actions` | SELECT | SELECT, **INSERT, UPDATE** (action record + outcome) |
| `procedural_memory` | SELECT | SELECT, **UPDATE** (usage_count/last_used_at bump only) |
| everything else (`semantic_facts`, `session_turns`, `session_state`, `agent_events`,\* `eval_probes`,\* `alerts`, `slos`, `metric_samples`, `config_values`, `service_dependencies`, `orders`, `runbook_provenance`, `semantic_fact_provenance`) | SELECT | **nothing** |

\* No code path writes `agent_events`/`eval_probes` today either — those tables exist in
`0002_core_schema.sql` for future instrumentation, not yet wired up.

Two concrete service-account principals are created and bound to the roles, mirroring doc `04`
§4.1's naming: `postmortem_agent_reader` → `postmortem_reader`, `postmortem_agent_writer` →
`postmortem_writer`. Locally (insecure cluster) they need no password; in CockroachDB Cloud they
map 1:1 onto the two `ccloud service-account`s doc `04` §4.1 already specifies — `CREATE USER
postmortem_agent_writer WITH PASSWORD '...'` (rotated via Secrets Manager) plus `GRANT
postmortem_writer TO postmortem_agent_writer`, one time, alongside minting the Cloud service
account's own API key.

### 2.2 Verified live (`scripts/audit_check.sh`)

- `postmortem_writer`'s only `INSERT` grants are `{deploys, episodic_events,
  remediation_actions}` — asserted by query, not eyeballed.
- `postmortem_writer` has **zero** privileges on `semantic_facts` (the consolidation job's table —
  see §5 follow-ups).
- `postmortem_reader` has **zero** `INSERT`/`UPDATE`/`DELETE` grants anywhere.
- `postmortem_agent_writer` attempting `INSERT INTO semantic_facts` → denied
  (`SQLSTATE 42501`).
- `postmortem_agent_reader` attempting `INSERT INTO episodic_events` → denied (`42501`).
- `postmortem_agent_writer`'s real, allowed write is immediately visible via
  `postmortem_agent_reader`'s `SELECT` — the recall path sees the act path's write with no lag,
  under the actual grant model (not just same-process trust).

### 2.3 A real defense-in-depth finding: PUBLIC had schema CREATE

Running `SHOW GRANTS ON DATABASE postmortem` / `SHOW GRANTS FOR public` against a fresh v26.2
node (before this migration) showed CockroachDB grants `CREATE ON SCHEMA <db>.public` to the
`public` pseudo-role **by default** — every authenticated principal, including a hypothetical
future low-privilege reader, could create arbitrary objects in the application schema. No table-
level PUBLIC grants existed (unlike Postgres, CockroachDB does not default-grant PUBLIC on user
tables), so this is narrower than a Postgres audit would find, but it's real. **Fix applied**:
`REVOKE CREATE ON SCHEMA postmortem.public FROM public;` in `0007_audit_logging.sql`, line 1 of
Section 1.

---

## 3. Audit logging

### 3.1 Two mechanisms, both applied, both verified

`0007_audit_logging.sql` enables both of CockroachDB's audit mechanisms against the five tables
the agent's writer role can mutate (`incidents`, `remediation_actions`, `episodic_events`,
`semantic_facts`, `procedural_memory` — the exact table list named in the Phase 3 plan, deliberately
including `semantic_facts` even though the *live* agent doesn't write it yet, because the
sleep-time consolidation job will, per charter §5):

1. **Table-level (`ALTER TABLE ... EXPERIMENTAL_AUDIT SET READ WRITE`)** — every read+write against
   these five tables is written to the `SENSITIVE_ACCESS` logging channel (`cockroach-sql-audit.*`
   locally), tagged with the executing SQL user and table name (`EventType: sensitive_table_access`).
2. **Role-based (`sql.log.user_audit = 'postmortem_writer ALL'`)** — every statement executed by the
   `postmortem_writer` role is logged (`EventType: role_based_audit_event`, `Role:
   postmortem_writer`), cluster-wide, independent of which table.
3. **Admin audit (`sql.log.admin_audit.enabled = true`)** — every statement run by an `admin`-role
   principal (schema migrations, `GRANT`/`REVOKE`, cluster-setting changes) is logged too. Cheap,
   high-value, per the `configuring-audit-logging` skill's own guidance.

`postmortem_reader` is **intentionally not** role-audited: SQL grants already make it structurally
read-only (§2), and the `configuring-audit-logging` skill explicitly warns that all-statement
logging at real query volume is "high overhead" and should be reserved for investigations, not
steady-state production. Auditing only the role that can actually mutate state is the
skill-recommended tradeoff.

### 3.2 Real finding #1: `schema_locked` blocks `EXPERIMENTAL_AUDIT` on v26.2

CockroachDB v26.2 sets `schema_locked = true` on every new table by default (a changefeed-
performance optimization — see the `WITH (... schema_locked = true)` on every table this project's
migrations create). `EXPERIMENTAL_AUDIT` is implemented as a descriptor mutation and is
**unconditionally refused** on a locked table:

```
ERROR: this schema change is disallowed because table "incidents" is locked and this operation
cannot automatically unlock the table
DETAIL: To unlock the table, execute `ALTER TABLE incidents SET (schema_locked = false);`
```

This is undocumented in the `configuring-audit-logging` skill (which predates the v26.2
schema-locked-by-default change) and would have silently blocked audit rollout on this schema.
**Fix applied**: `0007_audit_logging.sql` unlocks, audits, and relocks each of the five tables in
sequence — verified this restores `schema_locked = true` afterward (`SHOW CREATE TABLE incidents`
confirmed post-migration).

### 3.3 Real finding #2: role-based audit filters are `ALL`/`NONE` only, not `READ`/`WRITE`

The `configuring-audit-logging` skill's own example (`SET CLUSTER SETTING sql.log.user_audit =
'app_service_account READ';`) does not work on live v26.2:

```
ERROR: line 2: unknown statement filter: "READ" (valid filters include: "ALL", "NONE")
```

**Fix applied**: `postmortem_writer ALL` (there is no cheaper "writes only" role-level filter to
reach for — table-level `EXPERIMENTAL_AUDIT SET READ WRITE` is where the read/write granularity
actually lives, at the *table* axis rather than the *role* axis). Flagging this doc-vs-behavior
mismatch is itself a production-readiness finding worth carrying into any real compliance
write-up that cites the skill's example verbatim.

### 3.4 Verified live: writes AND denied attempts both land in the log

`scripts/audit_check.sh` performs a real writer `INSERT` and greps the container's own
`cockroach-sql-audit.*.log` for it, asserting:

- A `sensitive_table_access` entry exists for the insert (table-level trail).
- A `role_based_audit_event` entry exists, tagged `Role: postmortem_writer` (role-based trail).
- The entry attributes the write to `User: postmortem_agent_writer` (the actual principal, not
  just the role).
- The **denied** `semantic_facts` write attempt from §2.2 is *itself* present in the audit log
  (`SQLSTATE 42501`) — a real security property: privilege-violation attempts are audited even
  though they never touch data, which is exactly the signal a "did someone probe for write access
  outside their lane" investigation needs.

Sample redacted log line (CockroachDB redacts literal values between `‹...›` guillemets by
default; `--redact` at the process level redacts identifiers too):

```json
{"EventType":"role_based_audit_event","Statement":"INSERT INTO \"\".\"\".episodic_events(...)
VALUES (...)","Tag":"INSERT","User":"‹postmortem_agent_writer›","NumRows":1,
"Role":"‹postmortem_writer›"}
```

### 3.5 CockroachDB Cloud production plan (not yet applied — documented for `03`/deployment)

Two layers, matching doc `04` §6.2:

- **Data-plane (what the agent wrote)**: the SQL audit logging in this migration, exported via
  `ccloud cluster log-export enable --channels SENSITIVE_ACCESS --target cloudwatch` (or the
  Console's Export Logs page) so `cockroach-sql-audit` entries land in CloudWatch Logs rather than
  living only on ephemeral node disk. This is the mechanism that answers "what did the agent write,
  and when" on camera/in an audit response.
- **Control-plane (who changed cluster config)**: `ccloud audit list -o json --since 24h` —
  organization-level action history (service-account creation, backup-config changes, disruption
  triggers). Independent of SQL audit logging; covers operator/admin actions on the cluster itself,
  not agent SQL activity.
- **PrivateLink**: the agent's writer/reader connections and the MCP endpoint should terminate over
  AWS PrivateLink (`ccloud cluster networking privatelink` / the `configuring-private-connectivity`
  skill) rather than a public connection string once this moves off a hackathon demo cluster —
  keeps the audit story's premise ("we know exactly who wrote what") from being undermined by an
  internet-reachable SQL port. Not exercised in this track (no live Cloud cluster provisioned for
  Phase 3) — flagged as a `03`/infra follow-up.

---

## 4. Backup / point-in-time recovery

### 4.1 Why this exists (belt-and-suspenders, not redundant with replication)

Doc `04` §6.1 and the charter's wedge #3 are about **RPO=0 on infrastructure loss** — synchronous
Raft replication faithfully protects against a node or region dying. It does **not** protect
against a *logically wrong* write: a buggy remediation action, a bad episodic/semantic write, an
agent hallucinating a fix and "recording" it as ground truth. Replication replicates mistakes just
as durably as correct data. Backup/PITR is the only mechanism that answers "what if the agent's
write was wrong" — directly relevant given this agent has live write/act capability, which is
exactly the kind of question judges (per the charter's Production Readiness axis) are likely to
ask.

### 4.2 Verified live (`scripts/backup_pitr_smoke.sh`)

Ran end-to-end against a throwaway v26.2 node, twice, identical result both times:

1. Seeded an `episodic_events` row and an `incidents` row (shape matching the agent's real write
   path).
2. Captured `cluster_logical_timestamp()` as the PITR cutpoint, **before** any bad write.
3. `BACKUP DATABASE postmortem INTO 'nodelocal://1/backups/pm_pitr_smoke' WITH revision_history;`
   → succeeded.
4. Simulated a buggy agent write: `UPDATE incidents SET title = 'CORRUPTED BY BUGGY AGENT' ...`
   and `DELETE FROM episodic_events WHERE ...` — the marker row is gone, the title is corrupted,
   confirmed by direct query (replication would have carried this exact damage to every replica).
5. `BACKUP DATABASE postmortem INTO LATEST IN '...' WITH revision_history;` (a follow-up
   incremental so the backup chain covers the mutation instant) → succeeded.
6. `RESTORE DATABASE postmortem FROM LATEST IN '...' AS OF SYSTEM TIME '<T1>' WITH new_db_name =
   'postmortem_pitr_recovered';` → succeeded.
7. **Recovery confirmed**: the restored database has the deleted `episodic_events` row back and
   the pre-corruption incident title — while the *live*, unrestored `postmortem` database still
   shows the damage (checked explicitly, so the test can't pass by accident/no-op).

This proves the exact recovery story doc `04` §6.1 calls for: "what if the agent's remediation
action was wrong and it wrote bad data" → point-in-time restore to any instant, into a scratch
database, with zero rows lost from before that instant.

### 4.3 CockroachDB Cloud production plan (not yet applied — documented for `03`/deployment)

```sql
CREATE SCHEDULE postmortem_daily
  FOR BACKUP INTO 's3://<bucket>/postmortem?AUTH=implicit'
  RECURRING '@daily'
  WITH SCHEDULE OPTIONS first_run = 'now', revision_history = true;
```

- `revision_history = true` is required for `RESTORE ... AS OF SYSTEM TIME` to be usable across the
  schedule's backup chain, exactly as exercised locally in §4.2.
- Retention/monitoring: `ccloud cluster backup config update` to manage retention; `ccloud cluster
  backup list -o json` or `SELECT * FROM crdb_internal.jobs WHERE job_type IN ('BACKUP','RESTORE')`
  to monitor — both confirmed-real commands per doc `04` §6.1, not re-verified live in this track
  (no Cloud cluster provisioned for Phase 3).
- Local smoke test used `nodelocal://` storage, which does not exist on Cloud; the production
  equivalent is `s3://...?AUTH=implicit` with the cluster's IAM role, per doc `04`. The `BACKUP`/
  `RESTORE` SQL surface and the `AS OF SYSTEM TIME` semantics are identical between the two — the
  storage URI is the only thing that changes, so this local proof generalizes directly.

---

## 5. CockroachDB Agent Skills run against this schema

Skills actually invoked in this session (not summarized from memory): `hardening-user-privileges`,
`configuring-audit-logging`, `auditing-cis-benchmark`, `reviewing-cluster-health`.

| Skill | Top findings against this schema | Fix applied / recommended |
|---|---|---|
| `hardening-user-privileges` | Fresh cluster had exactly `root`/`admin` — no purpose-specific roles existed for the agent's two access patterns at all. `SHOW GRANTS FOR public` showed `CREATE ON SCHEMA postmortem.public` granted to `public`. | **Applied**: created `postmortem_reader`/`postmortem_writer` scoped exactly to the recall/act split (§2); revoked schema `CREATE` from `public`. |
| `configuring-audit-logging` | Baseline: `sql.log.admin_audit.enabled = false`, `sql.log.user_audit = ''` — no audit trail existed for any principal. Skill's own `READ`/`WRITE` role-filter example does not work on v26.2 (§3.3). | **Applied**: table-level + role-based + admin audit, all three, with the `READ`/`WRITE`-filter gap documented instead of silently worked around. |
| `auditing-cis-benchmark` (self-hosted Level 1) | Ran the SQL-only automatable controls against this local node (no OS/systemd/cert access in this containerized session, so §1-2 controls are `[N/A: not applicable to a containerized dev node]`, not evaluated). **6.3** (`sql.log.user_audit` non-empty + `sql.log.admin_audit.enabled` = true): baseline **[FAIL]**, now **[PASS]** after this migration. **6.4** (`sql.defaults.idle_in_session_timeout` set): baseline and still **[FAIL]** — `SHOW CLUSTER SETTING sql.defaults.idle_in_session_timeout` returns `00:00:00` (no timeout), and the setting itself carries a hint recommending `ALTER ROLE ... SET` per-role instead of the global default. **Not applied** in this track (out of the audit/backup/RBAC scope this track owns) — see follow-ups. | 6.3 fixed; 6.4 documented as an open finding, not fixed here. |
| `reviewing-cluster-health` | Production-readiness check flagged `kv.rangefeed.enabled = false` on the fresh node — **WARN: should be true for CDC**. This directly matters for this project: charter §5/§6 "architecture spine" specifies the sleep-time consolidation job is *changefeed-triggered*, which requires rangefeeds on. Not this track's table to fix (Track A/B's changefeed wiring), but the gap would silently break consolidation if nobody flips it before that lands. | **Not applied** (belongs to whichever track wires up the changefeed→Lambda consumer) — flagged below. |

---

## 6. Follow-ups for other tracks

- **Track A/B — consolidation job needs its own SQL role.** `postmortem_writer` is deliberately
  scoped to the live agent's atomic Act+Record path only (§2.1) and has *zero* privileges on
  `semantic_facts`, `procedural_memory` INSERT, `runbook_provenance`, or
  `semantic_fact_provenance` — all of which the sleep-time consolidation job (charter §5, a
  changefeed-triggered Lambda) needs to write. Reusing `postmortem_writer` for that job would
  either fail on privilege errors or require broadening the agent's own write grant beyond what its
  code actually needs — do neither. Create a third role (e.g. `postmortem_consolidator`) scoped to
  exactly the consolidation job's write set when that job's SQL is implemented.
- **Track A — `kv.rangefeed.enabled` is `false` on a fresh node.** Flip it before the changefeed→
  Lambda wiring lands (`reviewing-cluster-health` finding, §5 table above); it's a one-line cluster
  setting but sits outside this track's audit/backup/RBAC scope to apply blind.
- **CIS 6.4 — no `sql.defaults.idle_in_session_timeout`.** Recommend `ALTER ROLE
  postmortem_agent_reader, postmortem_agent_writer SET idle_in_session_timeout = '15m';` (per-role,
  per the setting's own hint) rather than a global default that could affect long-lived
  operational sessions. Left unapplied here; low risk, easy pickup for whichever track owns
  connection-pool/session lifecycle (doc `04` §6.4 territory).
- **`agent_events`/`eval_probes` are ungranted for both roles' write side and unwritten by any code
  path today.** When Track A/B/D's evaluation instrumentation starts writing them, decide which
  role should hold that grant (probably a fourth, evaluation-scoped identity, not `postmortem_writer`
  — keeps the Act path's privilege set from growing to serve an unrelated concern) rather than
  defaulting to widening `postmortem_writer`.
- **CockroachDB Cloud application of §3.5/§4.3 is documented, not applied.** No Cloud cluster was
  provisioned for this track (Phase 3 Track C scope is local verification); whoever stands up the
  Advanced cluster (doc `04` §1) should apply the `CREATE SCHEDULE`, `log-export enable`, and
  PrivateLink steps as part of that provisioning, using this document as the checklist.
- **Backend code changes are out of this track's file ownership.** The reader/writer grant model in
  `0007_audit_logging.sql` is designed to match `backend/.env.example`'s existing
  `COCKROACH_WRITER_DATABASE_URL` / `DATABASE_URL` split and doc `04` §4.2's two-service-account
  model, but no backend code was touched. Whoever owns `backend/` should point
  `postmortem_agent_reader`/`postmortem_agent_writer` credentials at the corresponding env vars
  when wiring real Cloud/production connections (local dev's `root`-as-everything DSN in
  `.env.example` is fine for now).

---

## 7. How to reproduce

```sh
# Roles + grants + audit logging, exercised end-to-end against a throwaway node:
scripts/audit_check.sh

# Backup -> corrupt -> point-in-time restore, exercised end-to-end:
scripts/backup_pitr_smoke.sh

# Either script: KEEP_CONTAINER=1 scripts/audit_check.sh leaves the node running for inspection.
```

Both scripts: boot their own `cockroachdb/cockroach:v26.2.0` container on a dedicated port
(`26268`/`26269` by default — distinct from the shared dev cluster on `26257`, the multi-region
demo ports, and any other track's in-progress work), apply every migration in
`db/migrations/` through the project's own `db/apply.sh` (unmodified), run their assertions, and
`docker rm -f` their container on exit unless `KEEP_CONTAINER=1` is set. No shared state, no
manual setup required beyond a working `docker` daemon.
