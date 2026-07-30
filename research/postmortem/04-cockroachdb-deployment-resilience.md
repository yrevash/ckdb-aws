# 04 — CockroachDB Deployment, Resilience & Demo Mechanics

Owner doc for: cluster topology, C-SPANN + Managed MCP setup, `ccloud` CLI usage, the failover-demo
mechanics, and production-readiness. Obeys `00-charter.md`; does not redefine the memory schema (owned
by `01-memory-architecture.md`) or the agent tool interface (owned by `02-agent-orchestration.md`) —
this doc specifies the infrastructure and operational surface those components run on top of.

Grounded in `../deep-dive/cockroachdb/01-architecture-and-resilience.md` (multi-region/RPO-RTO),
`03-agent-toolchain.md` (MCP/ccloud), `02-vector-search-and-cspann.md` (C-SPANN), plus live verification
against current CockroachDB Cloud docs (July 2026, target version v26.2).

---

## 0. Target version & feature-flag posture

- **Target CockroachDB version: v26.2** (current stable, GA'd 2026‑04‑27). Vector indexes lost their
  "preview" banner as of v25.4 but `feature.vector_index.enabled` is still an opt-in cluster setting on
  v26.2 — flip it explicitly in cluster bootstrap, don't assume default-on.
- **Tier: CockroachDB Cloud Advanced.** Justified in §1 and §"Decisions."

---

## 1. Cluster topology

### 1.1 Tier decision: Advanced (not Standard, not Basic)

| Requirement | Basic | Standard | **Advanced** |
|---|---|---|---|
| Multi-region support | Yes, but only 2 regions survive ZONE loss; 3+ needed for REGION | Yes, select AWS/GCP regions, RU/vCPU-provisioned | Yes, all AWS/GCP/Azure regions, full control |
| REGION survival goal (RF=5, 2+2+1) | Requires 3 regions, same as Advanced | Documented as available but console/API restricts some multi-region asymmetry; scaling is RU-metered, less deterministic for a demo | Full control, deterministic vCPU/node topology |
| `ccloud cluster disruption` (region/AZ chaos injection) | Not available | Not available | **Available (limited-access enrollment required)** — this is the money-shot mechanism, see §2 |
| Built-in Console "Fault tolerance demo" | Not available | Not available | **Available, GA, no enrollment gate** — the safety-net mechanism, see §2 |
| Arbitrary cluster settings (`feature.vector_index.enabled`, `kv.closed_timestamp.*`, `--max-offset` tuning) | Restricted (serverless multi-tenant) | Partially restricted | Full access — same surface as self-hosted |
| CMEK, dedicated infra, PCI/HIPAA/SOC2 posture | No | Partial | Yes — relevant to the "Production Readiness" judging criterion |
| Cost | Cheapest, storage-based | ~$0.18/vCPU-hr, autoscale 2–200 vCPU | ~$0.60/vCPU-hr, fixed provisioned vCPU/node, custom-quoted at scale |

**Decision: Advanced.** It is the only tier that exposes (a) the native region/AZ chaos-injection CLI
that makes the failover demo credible without us hand-rolling infrastructure, (b) full cluster-settings
access needed to tune vector-index and multi-region clock behavior, and (c) the compliance posture that
maps to the "Production Readiness" judging axis. Standard is the fallback if Advanced provisioning or
`ccloud cluster disruption` enrollment falls through — see the three-tier demo plan in §2.

### 1.2 Region layout

**Recommendation: `us-east-1`, `us-east-2`, `us-west-2`** (3 AWS regions, all continental US).

Rationale over a wider/global spread (e.g., adding `eu-west-1`):
- Meets the hard floor for REGION survivability (≥3 regions) with room to spare.
- Keeps inter-region RTT in the 20–70ms band rather than 80–150ms+ transatlantic — this matters because
  the live incident console's chat/recall interactions run **during** the demo recording, and cross-region
  write latency (REGION survival goal adds ≥1 cross-region RTT per write, per official docs) is directly
  visible as UI lag if regions are too far apart. A checkout/payments SaaS is a plausible US-first
  business; 3 regions still reads as genuinely "multi-region" on camera (different metros, different
  power grids, different AWS availability domains).
- Lower AWS data-transfer cost during the ~4-week build/rehearsal cycle.

If the team wants a more dramatic "we survived losing a continent" narrative beat, swap `us-west-2` for
`eu-west-1` — same mechanics, higher latency risk during live typing segments. Flagging as an open call
for `06-demo-and-ux.md` to weigh in on for the video's narrative framing.

### 1.3 Survival goal: REGION (not ZONE)

Non-negotiable per charter §4 wedge #3 ("kill a region live; memory + agent keep working, zero data
loss") and §8 success metrics (RPO=0 rows, RTO<10s on region failure). ZONE survivability only protects
against AZ loss, not region loss — it cannot deliver the money shot.

```sql
CREATE DATABASE postmortem PRIMARY REGION "us-east-1"
  REGIONS "us-east-1", "us-east-2", "us-west-2"
  SURVIVE REGION FAILURE;
```

Consequence: replication factor for this database jumps from 3 → **5**, distributed **2+2+1** across the
three regions. Every byte of agent memory + operational data is now stored 5x, and every write pays at
least one cross-region round trip. This is the literal cost of the RPO=0/region-survival guarantee — see
§1.5 for the dollar figure.

### 1.4 Minimum viable topology that still proves RPO=0 / RTO<10s on camera

Per CockroachDB Advanced's own documented floor: **each region of a multi-region cluster must contain at
least 3 nodes**, and **REGION survival goal requires at least 3 regions**. There is no way to go smaller
and still get genuine REGION survivability — the Raft-quorum arithmetic (5 replicas, need 3 to commit)
requires that floor.

**Minimum viable: 3 regions × 3 nodes = 9 nodes.** This is not a compromise for the demo — it is the
actual production floor for this guarantee, which is itself a good production-readiness talking point
("what we show on camera is not a stripped-down toy topology, it's the real minimum CockroachDB requires
for zero-RPO region survival").

Node sizing for the demo cluster: 2–4 vCPU/node is sufficient to run the agent's memory workload at
demo scale (a handful of req/s, not production QPS). Cockroach Labs' own production guidance
(`managing-cluster-capacity` skill) recommends 4–8 vCPU/node for real production load — call that out
explicitly in the write-up as the documented gap between "what we demo" and "what we'd run in prod," so
judges don't mistake the demo sizing for a production claim.

### 1.5 Cost tradeoff (explicit, since REGION survival is expensive)

- Storage: RF=5 means every byte is stored ~1.67x more than a single-region RF=3 database (5/3×), on top
  of the Advanced storage rate (~$0.50/GiB-month as of this research).
- Compute: 9 nodes × 2 vCPU × ~$0.60/vCPU-hr (Advanced list price) ≈ **$10.80/hr ≈ ~$260/day** if left
  running continuously. Advanced (dedicated compute) clusters, unlike Basic (serverless), generally
  **cannot be paused** — the cost-control lever is node count / vCPU-per-node, or deleting and
  re-provisioning from a backup between work sessions.
- Cross-region network egress on every write (REGION survival goal replicates across regions on every
  commit) — real but small at demo QPS; would need modeling for a production sizing exercise (out of
  scope for `04`; production capacity planning is `06`'s / a stretch item's concern).
- **Recommendation:** provision the 9-node demo cluster only during active build/rehearsal weeks; scale
  down to a cheap single-region 3-node cluster (or delete + restore-from-backup) between work sessions to
  control hackathon cloud spend. Full 9-node uptime is only strictly required for final rehearsals and
  the actual recording session.

---

## 2. The failover demo — the money shot

This is the highest-risk deliverable in the entire project: a **live, on-camera, must-not-fail**
demonstration that the memory layer survives a real region loss with zero data loss and automatic
recovery in under 10 seconds. A recorded demo has one shot at looking right; "we'll just retry" is not
an option once footage is being edited into a <3-minute video. The plan below is deliberately layered —
best case, guaranteed fallback, last-resort — because the single most credible native mechanism
(`ccloud cluster disruption`) is gated behind a **limited-access enrollment** whose approval timeline we
do not control.

### 2.1 What CockroachDB Cloud actually lets a customer trigger (verified against current docs)

| Mechanism | Tier | Access | Scope | GA / gated | Verdict |
|---|---|---|---|---|---|
| `ccloud cluster disruption set <cluster> --region <r> --whole-region` | Advanced | CLI, customer-triggered | Kills network to **an entire region's nodes** | **Limited access — requires enrollment via Cockroach Labs account team** | **Best option if enrolled in time** — this is literally Cockroach Labs' own chaos-injection tool, purpose-built for exactly this narrative |
| `ccloud cluster disruption set <cluster> --region <r> --azs <az1,az2>` | Advanced | CLI, customer-triggered | Kills network to specific AZs | Limited access, same enrollment | Useful for rehearsal / a secondary "AZ-loss" beat, not the region money-shot |
| `ccloud cluster disruption clear <cluster>` | Advanced | CLI | Restores normal operation | Same enrollment | The "recovery" half of the demo |
| Console **Overview → Actions → Fault tolerance demo** | Advanced | Console, self-service, **no enrollment needed** | Blocks network to **one AZ** (not a full region), auto-runs a sample workload, shows live latency/failure-rate | **GA today** | Real, reproducible, zero setup — but proves ZONE not REGION survival; requires ≥3 nodes, all healthy, cluster CPU <30%; explicitly **not recommended on a production cluster** |
| Self-hosted CockroachDB on EC2, `cockroach node drain` + process stop, or `aws ec2 stop-instances` / security-group deny-all for one region's nodes | Self-managed | Full control, our AWS account | Kills an entire region, deterministically | We build and own it | **Zero vendor dependency — the guaranteed fallback** |

Confirmed dead end: there is **no documented `ccloud cluster update`/`scale`/failover verb** in the
public `ccloud` reference beyond what's listed above — consistent with the toolchain research finding
that Cockroach Labs' own published examples stop at read + low-risk-write operations. Do not build the
demo runbook around a CLI verb that doesn't appear in `ccloud <noun> <verb> --help` — verify live before
committing to a script.

### 2.2 Three-tier plan (this is the actual recommendation)

**Plan A — `ccloud cluster disruption`, on the real product cluster (target, not guaranteed).**
Apply for limited-access enrollment via the Cockroach Labs account/hackathon-support team on **day 1** of
the build (this is an external dependency with an unknown SLA — the single biggest schedule risk in this
entire doc, see §"Risks"). If enrolled with enough lead time to rehearse (target: confirmed and
functionally tested by end of week 2 of a ~4-week build), this becomes the primary demo mechanism, run
directly against the same Advanced cluster the agent uses in production. Zero architecture compromise:
what's on camera is exactly the system judges would run themselves.

**Plan B — self-hosted 3-region EC2 cluster, schema-identical, our own AWS account (the guaranteed
fallback — build this in parallel starting week 1 regardless of Plan A's status).**
Stand up a second CockroachDB v26.2 cluster, same DDL (applied via the same migration scripts used for
the Cloud cluster), 3 nodes per region across `us-east-1`/`us-east-2`/`us-west-2` on EC2, `SURVIVE REGION
FAILURE` configured identically. Seed it with the same demo dataset immediately before the recording
session (a `BACKUP`/`RESTORE` from the real cluster, or simply re-running the demo seed script). We have
root access to every node, so the kill mechanism is fully deterministic and rehearsable without limit:

```bash
# Option 1 — graceful-looking but decisive: stop the cockroach process on all 3 nodes in one region
for node in useast2-n1 useast2-n2 useast2-n3; do
  ssh $node "sudo systemctl stop cockroach"
done

# Option 2 — more visually dramatic for camera: kill the region at the network layer
aws ec2 stop-instances --region us-east-2 --instance-ids i-xxx i-yyy i-zzz
# or, to look like a real infra outage without waiting on instance-stop lifecycle:
aws ec2 revoke-security-group-ingress --region us-east-2 --group-id sg-xxxx --protocol -1 --port -1 --cidr 0.0.0.0/0
aws ec2 revoke-security-group-egress  --region us-east-2 --group-id sg-xxxx --protocol -1 --port -1 --cidr 0.0.0.0/0
```

Recovery: `systemctl start cockroach` on all 3 nodes / restore the security-group rules, and the cluster
self-heals without any manual promotion step (this is the point — no failover script, no DNS cutover).

Tradeoff, stated honestly: this is not the literal managed Cloud cluster the rest of the product runs on.
For a recorded (not live-audience) demo, this is an acceptable and common practice — the incident
console and agent talk to CockroachDB over a plain connection string/service-account key, so pointing
that connection at the EC2 cluster for the recording session is invisible to the viewer. Judges reviewing
the written architecture doc (this doc) will see the tradeoff disclosed here, which is itself a
production-readiness-judging positive (honesty about what's real vs. staged) rather than a negative.

**Plan C — Console "Fault tolerance demo," last resort (zero setup, always available, weaker claim).**
If Plan B also falls through on schedule (e.g., EC2 IAM/quota friction eats the runway), fall back to the
Advanced Console's built-in AZ-outage demo on a **staging** Advanced cluster (never production, per
Cockroach Labs' own warning). This is real, reproducible, and shows genuine live latency/failure-rate
metrics — but it kills one **availability zone**, not a **region**. If used, the video narration must be
adjusted to an honest claim ("we take out an entire availability zone — a full data center — live") not
the charter's literal "kill a region" framing. This is a narrative downgrade, not a technical one — AZ
loss under ZONE survivability is still a genuine zero-downtime, zero-data-loss proof, just a weaker
instance of the same mechanism.

**Go/no-go checkpoint:** by day ~18 of the build, confirm which plan is live and rehearse it end-to-end
at least 3 times before the actual recording session. Do not attempt Plan A live on the recording day
without having already validated it works at least once beforehand.

### 2.3 What the audience sees (the actual demo choreography)

The visual and data proof points, regardless of which plan (A/B/C) is executing the kill:

1. **Before state:** incident console open, agent mid-conversation with the on-call SRE about a live
   (simulated) incident. A visible "memory timeline" panel showing recent episodic writes with commit
   timestamps. A dashboard (Cloud Console → Metrics, or a lightweight Grafana/custom panel — `06` owns
   the console UI) showing `liveness.livenodes` = 9, all green, per-region latency baseline.
2. **The kill, on camera:** run the trigger command in a visible terminal pane (`ccloud cluster
   disruption set postmortem-demo --region us-east-2 --whole-region`, or the EC2 equivalent). Narrate it
   live ("I am now taking down the entire us-east-2 region — three nodes, gone").
3. **During the outage (the ~seconds-long window):**
   - `liveness.livenodes` visibly drops by 3 (9 → 6) within the node-liveness detection window.
   - A brief latency spike is visible on the p99 write-latency panel — this is expected and is *itself*
     the RTO proof: it should recover within single-digit seconds (leader-lease failover in v25.2+
     typically resolves outages from liveness failures in **under 1 second**; worst-case documented
     bound is under ~9s for the older epoch-lease model, meaningfully better on leader leases). Do not
     hide this spike — showing it and watching it recover *is* the RTO=<10s proof, more credible than a
     flat line would be.
   - **Critically: the agent keeps working.** The console shows the agent continuing to recall memory and
     respond in the incident chat, uninterrupted from the operator's perspective except for that brief
     latency blip. This is the "agent keeps remembering + acting" half of the wedge claim (charter §9.3).
4. **The zero-data-loss proof (do this on camera, not just claim it):** immediately after triggering the
   outage, execute and display a fresh write through the agent (e.g., the agent records a new episodic
   memory or takes a remediation action) *while the region is still down*, then, after recovery, run a
   verifiable row-count / checksum query proving that write is present and durable:
   ```sql
   -- run before the kill, note the count
   SELECT count(*) FROM episodic_events;
   -- run again after recovery — the count must be >= the pre-kill count + writes made during the outage,
   -- with zero rows missing that were ever acknowledged as committed
   SELECT count(*), max(committed_at) FROM episodic_events;
   ```
   Pair this with `SHOW RANGES FROM DATABASE postmortem` (or the range-distribution skill,
   `analyzing-range-distribution`) before/after to visually show ranges rebalancing off the dead region's
   replicas and leaseholders migrating — the mechanical "why" behind the zero-data-loss claim, not just
   the assertion.
5. **Recovery:** clear the disruption (`ccloud cluster disruption clear` / restart the EC2 nodes),
   `liveness.livenodes` climbs back to 9, ranges re-replicate to restore full RF=5 redundancy on the
   returning region — narrate that this is fully automatic, no human failover runbook was executed.

### 2.4 Demo runbook

See **§(A) DEMO RUNBOOK** at the end of this document for the numbered, rehearsable script.

---

## 3. C-SPANN vector index configuration

(Schema ownership is `01-memory-architecture.md`'s; this section specifies the index configuration those
tables should adopt, and how it behaves under the multi-region topology defined in §1.)

### 3.1 Opclass

**Use `vector_cosine_ops`**, not the default `vector_l2_ops`. Text embeddings from Bedrock (Titan or
Claude-family embedding models) are directional/semantic-similarity vectors — cosine distance is the
correct metric, and is explicitly what Cockroach Labs' own docs recommend for "RAG-style text
embeddings." L2 is for spatial/physical-position data, which agent memory is not. Cosine opclass
acceleration shipped in v25.3 and is present on the v26.2 target — confirmed current, not a preview gap.

```sql
CREATE TABLE episodic_events (
    -- ... other columns defined by 01 ...
    embedding VECTOR(1536),   -- match the Bedrock embedding model's output dimension
    VECTOR INDEX embed_idx (embedding vector_cosine_ops)
);
```

### 3.2 Params

- `min_partition_size = 16`, `max_partition_size = 128` (documented defaults) are a reasonable starting
  point at demo/hackathon data volumes (hundreds to low thousands of memory rows, not billions). Do not
  hand-tune `build_beam_size` — Cockroach Labs explicitly advises against it.
- Query-time recall/latency knob: `SET vector_search_beam_size = 32;` (session-level default). At demo
  scale, the default is very likely sufficient to hit the charter's recall@k ≥ 95% target (§8); if `05`'s
  evaluation harness shows recall below target, raise beam size before touching partition sizing — it's
  the cheaper knob to turn.
- Enable the feature explicitly at cluster bootstrap time, before any table creation:
  ```sql
  SET CLUSTER SETTING feature.vector_index.enabled = true;
  ```

### 3.3 Prefix scoping — the multi-tenant / multi-agent pattern

Lead the vector index with prefix columns matching the memory schema's scoping dimension (owned by `01`,
but the pattern this doc recommends adopting):

```sql
VECTOR INDEX embed_idx (agent_id, embedding vector_cosine_ops)
-- or, for tenant isolation if 01 adopts a multi-tenant model:
VECTOR INDEX embed_idx (org_id, agent_id, embedding vector_cosine_ops)
```

This builds one K-means tree per distinct `(org_id, agent_id)` pair — exactly the isolation boundary an
agent's own memory recall needs, and it's a **hard requirement**, not an optimization: any query whose
`WHERE` clause isn't an equality/`IN` match on every prefix column falls back to a full scan of the
index, defeating the ANN speedup entirely. Every recall query the agent issues must filter on the prefix
columns before ordering by `<=>`.

### 3.4 Behavior under REGION survival / multi-region

This is the differentiator worth stating explicitly in the write-up (ties to charter wedge #1 — single
store, one ACID transaction):

- A C-SPANN partition is **ordinary CockroachDB KV data** — it is not a separate index structure that
  needs its own replication/DR story. Under `SURVIVE REGION FAILURE`, every vector-index partition is
  replicated RF=5 (2+2+1) across the same 3 regions as the rest of the schema, automatically. There is no
  separate "is the vector index region-resilient" question to answer — it inherits whatever survival goal
  the database has.
- Combine prefix columns with `REGIONAL BY ROW` (if `01` adopts row-level homing for per-agent/per-tenant
  locality) to get a **geo-partitioned ANN index for free**: an agent's memory rows *and* their vector
  index entries are homed and searched in the region that agent instance actually runs in, with full
  ACID consistency — not the typical multi-region tradeoff of picking either low latency or strong
  consistency.
- **During the failover demo specifically:** if the killed region held the leaseholder for a given
  vector-index partition, CockroachDB elects a new leaseholder among the surviving replicas in the other
  2 regions (a partition has replicas in all 3 regions under RF=5/2+2+1) — semantic recall queries keep
  working through the outage, not just row-level relational reads. Worth calling out on camera or in the
  write-up: "the agent's semantic memory search — not just its raw data — survived the region kill,
  because the vector index is not a bolted-on side system."
- **Known caveat to plan around:** adding a vector index to a **non-empty** table blocks
  `INSERT`/`UPSERT`/`UPDATE`/`DELETE` on that table for the duration of the backfill (documented,
  unresolved as of v26.2). Create the vector index at table-creation time (empty table) rather than
  retrofitting it onto a populated `episodic_events` table mid-build — avoids an unplanned write-outage
  during development.

---

## 4. Managed MCP Server setup for a headless agent

The agent (owned/orchestrated by `02`) needs machine-to-machine, unattended access to CockroachDB — not
the interactive OAuth/browser-consent flow meant for a human at an IDE. This section is the exact setup
path for that posture.

### 4.1 Service-account creation

```bash
# 1. Authenticate as a human operator first (one-time, to provision the service account)
ccloud auth login --sso

# 2. Create a dedicated service account for the agent — do not reuse a human's identity or an org-admin key
ccloud service-account create \
  --name postmortem-agent \
  --description "Postmortem on-call SRE agent — headless MCP + ccloud identity"

# 3. Mint a scoped API key for that service account
ccloud service-account api-key create --service-account-id <id> -o json
```

Store the resulting key in AWS Secrets Manager (or the equivalent secret store `03` specifies), not in
environment variables baked into a container image or committed anywhere. Rotate on the same cadence as
any production credential — treat it exactly like a database password, because functionally it is one.

### 4.2 Scoping — read-only default, write is opt-in and separately scoped

Cockroach Labs' MCP safety model stacks three independent guardrails; design the agent's access to use
all three deliberately rather than relying on just one:

1. **Cloud RBAC role on the service account** — scope it to *this one cluster/folder*, not org-wide.
   Start the account with a **read-only** cluster role (`SELECT`-equivalent + introspection). This is the
   agent's default posture for its "Recall" and "Perceive" tool calls (charter §5 loop).
2. **A second, separately-scoped write-capable identity for the "Act" path.** Rather than granting the
   single agent identity broad write access, mint a **second service account** (`postmortem-agent-writer`)
   scoped to only the tables the agent's remediation actions touch (the operational tables + the
   episodic-memory insert path), and have the agent's orchestration layer (`02`) select which credential
   to use per tool call based on whether the call is a read (recall) or a write (act/record). This
   directly demonstrates the charter's "recall vs. act" distinction as an actual security boundary, not
   just an application-level convention — a strong "Production Readiness" judging point.
3. **Hardcoded deny-list, unconditional.** `DROP`/`TRUNCATE`/other destructive DDL are unreachable via
   MCP tool calls regardless of RBAC — this is true even for the writer identity. Don't design any agent
   behavior that depends on being able to issue destructive SQL through MCP; if a demo beat needs to show
   a blocked destructive action (a nice, cheap "trust" beat for the video — see `06`), it will show a
   real, structural refusal, not a mocked one.

### 4.3 Config

```jsonc
// agent runtime MCP client config (exact shape mirrors what Cloud Console generates
// from Connect → MCP tab for this specific cluster — always regenerate per cluster/org)
{
  "mcpServers": {
    "cockroachdb-cloud-read": {
      "url": "https://cockroachlabs.cloud/mcp",
      "headers": { "Authorization": "Bearer ${POSTMORTEM_AGENT_READER_KEY}" }
    },
    "cockroachdb-cloud-write": {
      "url": "https://cockroachlabs.cloud/mcp",
      "headers": { "Authorization": "Bearer ${POSTMORTEM_AGENT_WRITER_KEY}" }
    }
  }
}
```

**Gotcha carried over from the toolchain research:** the config snippet is cluster/org-scoped. If the
team provisions a separate dev cluster and a separate demo cluster (per §2.2 Plan B), each needs its own
snippet regenerated from that cluster's Connect modal — do not assume one snippet works across clusters.

### 4.4 The write path in the actual agent loop

The charter's wedge #2 requires memory-write + action-write to be **one ACID transaction** — this is a
constraint on `02`'s design, not something MCP itself provides (each MCP tool call is independent; MCP is
not a transaction coordinator across calls). The practical implication for this doc: the single-ACID-
transaction requirement means the agent's "Act" step should **not** be expressed as two separate MCP
`insert_rows` calls (one for the memory write, one for the operational-table write) — it should be a
single SQL statement/transaction issued through whichever path (`02` chooses direct SQL driver vs. MCP
`insert_rows` for this specific call) can express multi-table atomicity. MCP's `insert_rows` tool as
documented is scoped to single-table inserts; if `02` needs a genuine multi-statement transaction for the
wedge proof, the write path should go through a direct SQL connection (using the writer service account's
credentials as the DB user) rather than MCP's `insert_rows`, with MCP reserved for the agent's
introspective/read tool calls and simple single-table writes. **Flagging this explicitly for `02` to
confirm** — it's the one place where "use MCP for everything" and "one ACID transaction" are in tension.

### 4.5 Audit logging

- Every MCP request emits structured logs (tool name, cluster/org context, redacted SQL shape, latency,
  response size) tagged `mcp` — this is Cockroach Labs' internal observability, not something exported to
  a customer-facing dashboard today. Do not build a demo beat that assumes we can pull MCP-specific audit
  logs into our own console.
- For a **customer-facing, demo-able** audit trail, pair MCP write actions with:
  - `ccloud audit list -o json` — organization-level control-plane action history (this exists and is
    exportable).
  - CockroachDB's own **SQL audit logging** (`configuring-audit-logging` skill) on the specific tables the
    agent writes to, sent to the `SENSITIVE_ACCESS` logging channel, exportable via `ccloud cluster
    log-export enable` to CloudWatch. This is the mechanism to actually show "every agent action is
    audited" on camera — enable it on `episodic_events` and the operational tables the agent mutates.

---

## 5. `ccloud` CLI usage

### 5.1 For the failover demo

Covered fully in §2. Confirmed-available, customer-triggerable verbs: `ccloud cluster disruption
set/get/clear` (limited access, Advanced), `ccloud cluster maintenance get/set/delete`, `ccloud cluster
blackout-window` (also limited access), `ccloud replication create/update` (PCR — async, not the RPO=0
mechanism, not used for the money shot). **Confirmed not to exist** in the public reference: any
`ccloud cluster update`/`scale` verb for vCPU or node-count changes. This matches the toolchain research's
own flagged gap — re-verify with `ccloud cluster --help` before finalizing any script, since docs can lag
CLI reality, but plan the agent's scaling action (§5.2) around the Cloud API instead, not a hoped-for CLI
verb.

### 5.2 For the agent's data-tier scaling action

The charter lists `ccloud` CLI as a "bonus" tool-usage target (tech baseline §7: "ccloud CLI
(failover/scale demo)"). Given §5.1's finding that no scaling verb exists in the documented CLI surface,
design the agent's scaling action as one of two honest options:

**Option 1 (recommended) — scope the action to what `ccloud` genuinely supports.** Have the agent react
to a detected capacity/health signal by taking a **real, low-risk `ccloud`-native action**: e.g.,
`ccloud cluster backup config update` to tighten backup frequency after detecting elevated write volume
during an incident, or `ccloud cluster maintenance set` to push out a scheduled maintenance window that
would otherwise collide with an active incident, or provisioning a scoped read-only service account for a
newly-spun-up sub-agent (`ccloud service-account create` + `api-key create`) as part of a multi-agent
stretch scenario. This is defensible, demoable, and matches Cockroach Labs' own documented "guardrailed
automation" examples (adjusting operational windows, not topology) — safer to build a real demo around
than a hypothetical scale verb.

**Option 2 — implement true vCPU/node scaling via the Cloud API, narrate it as "the agent's ccloud-based
control-plane action."** The CockroachDB Cloud API (`https://cockroachlabs.cloud/api/v1/...`, the same
backend `ccloud` itself calls) does expose a cluster-update endpoint that the Console's "Edit cluster →
Capacity" flow uses to change vCPUs-per-node. If `02`/`03` want the "scale under load" demo beat to be
literal infrastructure scaling rather than a maintenance-window nudge, call this endpoint directly with
the service-account API key (same credential model as `ccloud`) rather than waiting on a CLI verb that
may not exist yet. **This should be verified against the live Cloud API reference before committing to
it in the demo script** — flagging as a pre-demo verification task, not a confirmed-working path today.

Recommendation: build Option 1 as the baseline (it's real today, zero verification risk); attempt Option
2 only as a stretch enhancement once Option 1 is working end-to-end.

### 5.3 Standard agent-usable patterns

```bash
# health check before any maintenance action (agent's own pre-flight)
ccloud cluster info postmortem-prod -o json

# pull a connection string programmatically (for the agent's own bootstrap, not hardcoded)
ccloud cluster connection-string postmortem-prod --database postmortem \
  --sql-user postmortem_agent -o json | jq -r '.connection_url'

# audit trail pull for the "show your work" demo beat
ccloud audit list -o json --since 24h
```

---

## 6. Production-readiness

### 6.1 Backups / PITR

- `CREATE SCHEDULE postmortem_daily FOR BACKUP INTO 's3://.../postmortem?AUTH=implicit' RECURRING
  '@daily' WITH SCHEDULE OPTIONS first_run = 'now';` — belt-and-suspenders behind synchronous replication.
  Replication protects against infra loss (the demo's whole point); backup/PITR protects against the
  failure mode replication *cannot* — a buggy agent action or bad write logically corrupting/deleting
  memory rows, since replication faithfully replicates mistakes too.
- Enable `revision_history` on the schedule so `RESTORE ... AS OF SYSTEM TIME <ts>` is available — this is
  the concrete answer to "what if the agent's remediation action was wrong and it wrote bad data,"
  a realistic question judges are likely to ask given the agent has write/act capability.
- `ccloud cluster backup config update` to manage retention policy programmatically; `ccloud cluster
  backup list -o json` / `SELECT * FROM crdb_internal.jobs WHERE job_type IN ('BACKUP','RESTORE')` to
  monitor.

### 6.2 Audit logging

Two layers, both needed for a complete story (see §4.5 for the MCP-specific nuance):
- **Cloud org audit log** (`ccloud audit list`) — control-plane actions (who scaled what, who created
  which service account).
- **SQL audit logging** (`configuring-audit-logging` skill) on the memory/operational tables the agent
  writes — data-plane actions (what the agent actually wrote, when). This is the layer that answers "can
  we prove what the agent did and when," which is the real substance behind "production readiness" for an
  agent with write/act capability on live infrastructure.

### 6.3 Relevant CockroachDB Agent Skills (map to this doc's tasks)

| Skill | Used for |
|---|---|
| `provisioning-cluster-for-production` | Initial 9-node/3-region Advanced cluster bootstrap, locality flags, `--max-offset` tuning (recommend 250ms for multi-region per official multi-region config guidance) |
| `reviewing-cluster-health` | Pre-flight check before the failover demo (§2.4 runbook step 0); also a plausible agent-invoked skill during the "agent observes degraded health" narrative beat |
| `performing-cluster-maintenance` | Node drain/decommission patterns, referenced if Plan B (self-hosted EC2) needs manual node lifecycle management |
| `managing-cluster-capacity` | Grounds the vCPU/node sizing guidance in §1.4 and the scaling-action design in §5.2 |
| `analyzing-range-distribution` | The on-camera `SHOW RANGES` proof in §2.3 step 4 — visualizing leaseholder migration off the killed region |
| `configuring-audit-logging` | §6.2 |
| `enabling-cmek-encryption` | Advanced-tier compliance posture, a cheap "production readiness" checkbox to demonstrate is *possible* even if not enabled for the hackathon build itself |
| `designing-multi-region-applications` | Cross-check for `01`'s table-locality choices (REGIONAL BY ROW vs REGIONAL BY TABLE) against this doc's REGION-survival topology |

### 6.4 Connection pooling — agent + Lambda

- CockroachDB Cloud does not offer an RDS-Proxy-equivalent managed pooler; connection management is the
  application's responsibility.
- **Agent runtime** (long-lived process — AgentCore Runtime/ECS/whatever `03` selects): use a standard
  connection pool (e.g., `pgxpool`/SQLAlchemy pool) sized conservatively — CockroachDB Cloud defaults to
  **100 max connections per node**; with 9 nodes that's a cluster-wide ceiling of ~900, but the practical
  per-service pool should be sized to the agent's actual concurrency (low tens of connections for a
  hackathon-scale demo), not the ceiling.
- **Lambda (the sleep-time consolidation job, `02`/`03`'s changefeed-triggered consumer):** Lambda's
  execution-environment reuse means a connection opened on a warm invocation can be cached and reused
  across subsequent invocations of the *same* execution environment — do this rather than opening a fresh
  connection per invocation. Because Lambda concurrency can spike unpredictably (each concurrent
  invocation potentially opening its own connection), **cap Lambda's reserved concurrency** for this
  function explicitly (a low number is fine — consolidation is a background batch job, not a hot path) to
  bound worst-case connection count against the ~100-per-node ceiling. If invocation concurrency needs to
  grow beyond what direct connections can absorb, introduce a small PgBouncer (transaction-pooling mode)
  sidecar on Fargate between Lambda and CockroachDB — not needed at hackathon scale, but the documented
  escalation path for `03` to have in back pocket.

---

## (A) DEMO RUNBOOK — the failover money-shot

Numbered, rehearsable script. Assumes Plan A or Plan B from §2.2 is confirmed and rehearsed at least 3x
before this is executed for the actual recording.

0. **Pre-flight (T-10min).** Confirm `liveness.livenodes = 9`, all green, cluster CPU comfortably below
   any threshold that would make the disruption tool refuse to run (Console fault-tolerance demo requires
   <30% CPU; apply the same bar to Plan A/B for consistency). Run `SELECT count(*) FROM episodic_events;`
   and note the baseline count. Confirm the incident console is connected and the agent is mid-session
   with a live incident narrative already established (per `06`'s storyboard).
1. **Establish the "before" state on camera.** Show the dashboard: 9 live nodes, 3 regions, baseline p99
   write latency. Show the agent recalling a past incident from memory in the console — this ties back to
   demo-thesis beat #1 (memory is load-bearing) immediately before the kill, so the audience already
   trusts memory is real and in-use.
2. **Trigger the kill, narrated live.**
   - Plan A: `ccloud cluster disruption set postmortem-demo --region us-east-2 --whole-region`
   - Plan B: run the prepared kill script (systemctl stop / security-group revoke) against the self-hosted
     EC2 region.
3. **Show the drop.** `liveness.livenodes` visibly falls to 6. Point at the p99 latency panel as it
   spikes.
4. **Prove continued operation.** In the same incident chat, ask the agent a new question that requires a
   fresh memory recall + a new episodic write (a real remediation action, not a canned response). Show the
   response arriving.
5. **Prove zero data loss.** Re-run `SELECT count(*), max(committed_at) FROM episodic_events;` — the count
   must equal baseline + the write(s) made in step 4, no gaps. Run `SHOW RANGES FROM DATABASE postmortem`
   (or the range-distribution skill output) filtered to the affected table, showing leaseholders now
   entirely in the two surviving regions.
6. **Prove the recovery time.** Point at the latency panel's recovery — call out the timestamp delta
   between the spike and the return to baseline; this is the on-camera RTO measurement. State the target
   (<10s) and show it was met.
7. **Clear the disruption / restart the region.**
   - Plan A: `ccloud cluster disruption clear postmortem-demo`
   - Plan B: restart the stopped nodes / restore security-group rules.
8. **Show self-healing.** `liveness.livenodes` climbs back to 9; ranges re-replicate to restore RF=5 on
   the returned region — narrate explicitly that no human ran a failover script or promoted a replica by
   hand at any point in this sequence.
9. **Cut.** This segment should run 45–90 seconds of the <3-minute video budget — tight, no dead air
   during the ~seconds-long recovery window (that window recovering fast *on screen* is the actual proof;
   don't cut around it).

---

## (B) Decisions & recommendations

| Decision | Choice | Rationale |
|---|---|---|
| Cloud tier | **Advanced** | Only tier with native chaos-injection CLI + built-in fault-tolerance demo + full cluster-settings access + compliance posture |
| Region layout | **us-east-1, us-east-2, us-west-2** | Meets 3-region REGION-survival floor; keeps cross-region write latency low enough not to visibly lag the live console during recording; swap in `eu-west-1` only if `06` wants a more dramatic global narrative |
| Survival goal | **REGION** (RF=5, 2+2+1) | Charter-mandated (wedge #3, §8 RPO=0/RTO<10s); ZONE cannot deliver a region-kill proof |
| Minimum topology | **9 nodes (3×3)** | The documented floor for REGION survival on Advanced — not an artificial demo minimum, the actual production minimum |
| Failover demo mechanism | **3-tier: (A) `ccloud cluster disruption` if enrolled in time → (B) self-hosted 3-region EC2 cluster, schema-identical, as the guaranteed fallback → (C) Advanced's built-in AZ-only fault-tolerance demo as last resort** | `ccloud cluster disruption` is gated behind an unpredictable external enrollment process; betting the entire money-shot on an unconfirmed vendor approval is an unacceptable schedule risk for a hard hackathon deadline. Plan B must be built starting week 1 regardless of Plan A's status. |
| Vector opclass | **`vector_cosine_ops`** | Correct metric for semantic text-embedding recall; matches Cockroach Labs' own RAG guidance |
| Vector prefix columns | **`(agent_id[, org_id], embedding)`** | Per-agent/tenant isolation; hard requirement for index acceleration to fire at all |
| MCP identity model | **Two service accounts (reader / writer), not one** | Makes the charter's "recall vs. act" distinction a real RBAC boundary, not just app-level convention; stronger production-readiness story |
| Multi-table atomic write path | **Direct SQL connection for the Act+Record transaction, not chained MCP `insert_rows` calls** | MCP's write tools are single-table; the charter's "one ACID transaction" wedge claim requires a real multi-statement transaction, which needs `02` to route that specific call path around MCP |
| Agent's `ccloud` scaling action | **Option 1 (real low-risk `ccloud` actions: backup config, maintenance windows, service-account provisioning) as baseline; Cloud API-based vCPU scaling only as a verified stretch** | No documented `ccloud` scale/update verb exists as of this research — build on what's confirmed to work, not a hoped-for verb |

---

## (C) Interfaces I expose / depend on

**I depend on:**
- The memory schema and table definitions from `01-memory-architecture.md` — I specify the vector-index
  configuration (§3) that should be applied *to* those tables, but I don't define the tables themselves.
- The agent tool interface / hosting choice from `02-agent-orchestration.md` and `03-aws-infrastructure.md`
  — specifically, which runtime holds the long-lived connection pool (§6.4), and how the "Act" step routes
  its write (MCP vs. direct SQL, §4.4) is a joint decision with `02`.
- The demo storyboard/narrative pacing from `06-demo-and-ux.md` — the runbook in §(A) is written to slot
  into `06`'s <3-minute video structure; region-layout choice (§1.2) has an open call flagged for `06`.

**I expose:**
- The cluster topology and connection endpoint(s) (production Cloud Advanced cluster, and the Plan-B
  self-hosted demo cluster) that `02`/`03` connect to.
- The two-service-account MCP identity model (§4.2) that `02` should use to select credentials per tool
  call (read vs. write).
- The vector-index DDL pattern (§3.1–3.3) that `01` should adopt for any `VECTOR` column.
- The failover demo runbook (§A) that `06` choreographs into the final video.
- The `ccloud`-based scaling/audit actions (§5) available for `02`'s agent tool surface if it wants a
  literal infrastructure-control tool beyond MCP.

---

## (D) Risks & mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **`ccloud cluster disruption` enrollment never arrives, or arrives too late to rehearse** | High — this is the single biggest schedule risk in the whole plan | Apply day 1; do not block any other work on it; Plan B (self-hosted EC2) is built in parallel from week 1 regardless, as the guaranteed path |
| **Self-hosted Plan B cluster drifts from the "real" product schema/data**, making the demo feel disconnected from the actual system | Medium | Apply the exact same migration scripts to both clusters; seed Plan B immediately pre-recording via `BACKUP`/`RESTORE` from the real cluster, not a hand-maintained separate dataset |
| **Live recovery takes visibly longer than 10s on the actual recording day** (network variance, cold caches, region choice too far apart) | Medium-High — a failed money-shot number is worse than not showing a number | Rehearse ≥3x under conditions matching the recording setup; if RTO is inconsistent, don't over-claim a specific number on camera — show the recovery happening and let the timestamp delta speak for itself rather than pre-committing to "exactly Xs" in narration |
| **Demo cluster costs run up during the ~4-week build** (9-node Advanced can't be paused) | Low-Medium | Provision full 9-node topology only for active rehearsal/recording weeks; scale down or delete-and-restore between sessions (§1.5) |
| **Vector-index backfill blocks writes if retrofitted onto a populated table** | Low (avoidable) | Create the vector index at table-creation time, before `01`'s schema is seeded with demo data (§3.4) |
| **MCP's single-table `insert_rows` can't express the charter's required multi-table ACID write**, and this gets discovered late in `02`'s build | Medium | Flagged explicitly in §4.4 now, early, as a cross-doc dependency for `02` to resolve rather than discover during integration |
| **No `ccloud` scale/update verb exists**, undermining the "agent scales its own data tier" demo beat if assumed without verification | Low (avoidable) | §5.2 designs the baseline demo beat around confirmed-real `ccloud` actions instead of a hypothetical verb; Cloud API scaling is explicitly marked "verify before committing" |
| **Region layout (3x continental US) undersells the "multi-region" story visually** vs. a global spread | Low | Flagged as an open call for `06`; mechanically identical either way, purely a narrative/latency tradeoff |

**Tie-back to success metrics (charter §8):** this doc's plan directly targets read-your-write staleness
(§2.3 step 4 — proven via the live-recall-during-outage beat), region-failover RPO=0 (§2.3 step 5 — row
count/checksum proof), region-failover RTO<10s (§2.3 step 6 / §(A) step 6 — on-camera latency-recovery
timestamp), and memory-write+remediation atomicity (§4.4 — the ACID transaction routing decision). Vector
recall quality (recall@k≥95%) and cross-agent memory visibility are primarily `01`/`05`'s instrumentation
responsibility, but §3.2's beam-size guidance and §3.4's multi-region index behavior are the levers this
doc controls if `05`'s evaluation shows a gap.
