# 03 — AWS Infrastructure & Deployment (Postmortem)

**Owner:** `03-aws-infrastructure.md` — the "(X) AWS glue + deployment + hosting + observability" slice of the
charter (§6 ownership map). This doc **deploys** the agent designed in `02` and **consumes** the memory store
defined in `01`/`04`. It never redefines the memory schema or the agent's tool interface — it wires them into AWS.

**Grounding & obligations from the charter (`00-charter.md`):**
- CockroachDB is the **non-negotiable** memory + operational store. **Do NOT** propose AgentCore *Memory* as the
  store (it may appear only as ephemeral working memory, and even that is optional). AgentCore *Runtime* is fair
  game **for hosting only**.
- Must demonstrate the three wedges: single-store ACID (memory + action in one txn), read-your-writes at global
  scale, and RPO=0 region survival. **The AWS compute tier must not become the thing that loses data or stalls
  during the region-failover money-shot** — resilience is CockroachDB's job; our compute must fail over cleanly
  around it.
- Team 2–4, ~4 weeks, deadline 19 Aug 2026. Lean beats clever.

**Verification note (per the required protocol):** every AWS limit/quota/model-ID/feature-surface claim below was
checked against the AWS Knowledge MCP on 2026-07-30. Where I cite a **price**, it is order-of-magnitude only and
flagged — model/token prices move and must be reconfirmed on the Bedrock/Fargate pricing pages before you commit a
budget. CockroachDB-Cloud-side specifics (PrivateLink availability per plan tier, connection-pooler endpoint) are
owned by `04` and flagged as cross-doc dependencies, not asserted as fact here.

---

## 0. TL;DR decisions (full rationale in §A)

| Open decision I own | Recommendation | One-line why |
|---|---|---|
| **Agent + web-backend hosting** | **ECS Fargate** (1 always-on service) for MVP; **AgentCore Runtime** documented as the production-readiness upgrade | A team of 2–4 doing a live, streaming, region-failover demo needs a warm CockroachDB pool + WebSocket + full control; Lambda's 15-min cap and connection churn make it wrong for the *interactive agent*, right for *async consolidation*. |
| **Changefeed → Lambda sink** | **Webhook sink → API Gateway (HTTP API) → thin receiver Lambda → SQS → consolidator Lambda**; S3 sink kept as the cheap nightly-batch alternative | Near-real-time so we can trigger consolidation on demand in the demo; two-stage (fast ack + slow worker) so a slow Bedrock call never backpressures the changefeed. |
| **Bedrock reasoning model** | **Claude Sonnet 4.6** (`anthropic.claude-sonnet-4-6`, via `us.` inference profile) for hard reasoning; **Claude 3.5 Haiku** for cheap/high-volume paths (triage, dedup) | Best agentic reasoning on Bedrock; Haiku fallback + prompt caching keeps cost sane. |
| **Bedrock embedding model** | **Titan Text Embeddings V2** (`amazon.titan-embed-text-v2:0`), `normalize=true`, **1024 dims** | Cheapest good embedder, returns unit-normalized vectors — exactly what CockroachDB's L2-only vector index needs. **Requires aligning the schema `VECTOR(1536)` → `VECTOR(1024)` with `01`/`04`.** |
| **IaC** | **AWS CDK (Python)** | One language shared with the Python agent/consolidation code; stable L2 constructs for Lambda + Fargate + Bedrock, and now for AgentCore Runtime — smooth upgrade path. |
| **Network to CockroachDB Cloud** | IP-allowlist + TLS for the hackathon; **AWS PrivateLink** as the production upgrade (plan-tier dependent — confirm with `04`) | Keep it moving now; PrivateLink is the "production readiness" talking point. |

---

## 1. Full AWS component architecture

### 1.1 Text architecture diagram

```
                                   ┌──────────────────────────────────────────────┐
   SRE (browser)                   │                 AWS account (us-east-1)        │
        │  HTTPS / WSS             │                                                │
        ▼                          │                                                │
  ┌───────────────┐   static       │   ┌────────────────────────────────────────┐  │
  │ CloudFront +  │◄───assets──────┼───│ S3 (React SPA build)                    │  │
  │ S3 (web UI)   │                │   └────────────────────────────────────────┘  │
  └──────┬────────┘                │                                                │
         │ /api, /ws               │   ┌───────────── ALB (HTTP+WS) ───────────┐    │
         ▼                         │   │                                        │   │
  ┌──────────────────────────────────────────────────────────────────────┐    │   │
  │ (A)+(U) ECS Fargate service  "postmortem-agent"                       │    │   │
  │   FastAPI backend + agent reasoning loop (framework per doc 02)       │    │   │
  │   • holds warm CockroachDB conn pool (~4×vCPU)                        │    │   │
  │   • streams ChatOps + memory-timeline over WebSocket                  │    │   │
  │   • calls Bedrock (reason) + Titan (embed)                            │    │   │
  │   • acts via CockroachDB Managed MCP server (service account)         │    │   │
  └───┬───────────────┬───────────────────────┬─────────────────┬────────┘    │   │
      │ InvokeModel   │ SQL (memory+ops, TLS)  │ MCP (tools)     │ PutObject   │   │
      ▼               ▼                        ▼                 ▼             │   │
 ┌─────────┐   ┌───────────────────────────────────────┐   ┌──────────┐       │   │
 │ Bedrock │   │  CockroachDB Cloud (multi-region)      │   │ S3 (raw  │       │   │
 │ Claude  │   │  ── ONE STORE ──                       │   │ post-    │       │   │
 │ Sonnet  │   │  memory: episodic/semantic/procedural  │   │ mortems, │       │   │
 │ 4.6 /   │   │  + operational: services/deploys/      │   │ artifacts│       │   │
 │ Haiku + │   │    incidents/orders                    │   │ prompts) │       │   │
 │ Titanv2 │   │  reached via PrivateLink (prod) or     │   └──────────┘       │   │
 │ embed + │   │  IP-allowlist+TLS (hackathon)          │                      │   │
 │Guardrail│   └───────────────┬───────────────────────┘                      │   │
 └─────────┘                   │ CHANGEFEED (webhook sink, resolved=…)        │   │
                               ▼                                              │   │
              ┌─────────────────────────────────────┐                        │   │
              │ API Gateway (HTTP API)  /consolidate │  auth: shared-secret   │   │
              └───────────────┬─────────────────────┘  header + WAF/throttle  │   │
                              ▼                                                   │
              ┌───────────────────────────┐   dedup/ack fast (<2s)               │
              │ (C1) receiver Lambda       │──► SQS (FIFO or std) ──► DLQ         │
              │  validate + enqueue        │                                      │
              └───────────────────────────┘                                      │
                              │ SQS event-source mapping (batch)                  │
                              ▼                                                   │
              ┌───────────────────────────────────────────────┐                  │
              │ (C2) consolidator Lambda (the "sleep-time" job)│                  │
              │  read episodic window (follower reads) →       │                  │
              │  Bedrock distill (Claude) + embed (Titan) →    │──► write facts/  │
              │  idempotent write-back to CockroachDB          │    runbooks back │
              │  raw prompt/response → S3                      │                  │
              └───────────────────────────────────────────────┘                  │
                              ▲                                                   │
              EventBridge Scheduler (nightly cron) ── alt trigger ────────────────┘
              EventBridge custom bus "postmortem" ── SUM alerts ──► agent (async)

  Cross-cutting: IAM roles (least-priv, one per compute unit) · Secrets Manager
  (CRDB creds, MCP service-account key, webhook secret) · CloudWatch logs/metrics/alarms
  · X-Ray / OTel (ADOT) tracing · Bedrock Guardrails on reason + consolidation
```

### 1.2 Component-by-component

**Amazon Bedrock (reasoning + embeddings).**
- **Reasoning:** **Claude Sonnet 4.6** — `anthropic.claude-sonnet-4-6`, invoked through a cross-region inference
  profile (`us.anthropic.claude-sonnet-4-6`) so Bedrock load-balances across US regions and you're insulated from
  single-region capacity blips. Verified available on `bedrock-runtime` (AWS Knowledge MCP, 2026-07-30). Use it for
  the core Perceive→Reason→Act decision and for consolidation distillation. Use **Claude 3.5 Haiku**
  (`us.anthropic.claude-3-5-haiku-20241022-v1:0`) for cheap, high-frequency paths: alert triage, dedup, "is this
  incident similar enough to reuse runbook X". Enable **prompt caching** on the long, stable system prompt / runbook
  context to cut token cost on repeated turns.
- **Embeddings:** **Titan Text Embeddings V2** (`amazon.titan-embed-text-v2:0`) with `normalize=true`. Verified: max
  8,192 input tokens, output **1024** dims (default; also 512/256), on-demand. Cheapest solid option and — critically
  — it returns **unit-normalized** vectors, which is exactly the precondition CockroachDB's vector index needs
  (`04`/`01` note: C-SPANN accelerates **L2 distance only**; for unit vectors L2 ranking ≡ cosine ranking). Cohere
  Embed v4 (1024, multimodal) is a viable alternative if we ever embed images/screenshots.
  - **⚠️ Cross-doc action:** the starter schema in `01`/`04` declares `VECTOR(1536)` (an OpenAI/Titan-G1 dimension).
    Titan V2 is **1024**. Pick one and make it consistent across `01` (DDL), `02` (embedding call), and here. My
    recommendation: **standardize on Titan V2 @ 1024** (`VECTOR(1024)`), staying fully inside Bedrock/AWS. If the team
    prefers 1536, use Titan **G1** (`amazon.titan-embed-text-v1`, 1536) instead — but G1 is older and not
    dimension-configurable. This is a hard interface dependency, called out again in §B.
- **Bedrock Guardrails:** one guardrail attached to both the agent reasoning call and the consolidation output —
  content filters (incl. **prompt-attack** detection for injected alert payloads), **denied topics** (block the model
  from advising destructive irreversible ops without escalation), **sensitive-information** filters (mask PII/secrets
  that leak into incident logs), and **contextual-grounding** checks (reduce hallucinated fixes not supported by
  recalled memory). Verified these are all first-class Guardrails filters (AWS Knowledge MCP).

**AWS Lambda (the sleep-time consolidation compute).** Two functions (§3): a **receiver** (fast ack of the changefeed
webhook) and a **consolidator** (the actual distillation worker). Lambda's hard ceiling is **900 s / 15 min**
(verified) — fine for consolidating a bounded batch; if a distillation window ever risks exceeding that, chunk the
Map over episodes or offload the long tail to a Step Functions loop. Python runtime, **ARM64/Graviton** (≈20% cheaper,
faster cold start), **AWS Lambda Powertools** for structured logging/metrics/tracing.

**Amazon S3.** Three logical prefixes in one bucket (`postmortem-artifacts-<acct>-<region>`, SSE-KMS, versioning on,
Block Public Access on): `raw/` (uploaded postmortems, transcripts, large tool outputs kept out of CockroachDB rows
to respect the ~1 MB row / ~16 MB txn guidance from `04`), `cdc/` (only if we run the S3 changefeed sink),
`consolidation/` (the exact Bedrock prompt+response for every distillation — provenance/audit, a Production-Readiness
judging point). CockroachDB rows store only the S3 **reference** + embedding + metadata.

**Changefeed → Lambda wiring.** Recommended **webhook sink** (see §2). CockroachDB emits row changes to an HTTPS
endpoint (API Gateway HTTP API), authenticated with a shared secret carried via an `EXTERNAL CONNECTION` (never
inline creds — matches `04` §3). `resolved` watermarks define consistent batch boundaries for consolidation;
`webhook_sink_config` batches (100 msgs / 1 MB / 5 s) to cap invocation rate.

**EventBridge.** Two lean uses: (1) a **custom bus** `postmortem` to route SUM-simulator alerts to the agent
asynchronously (loose coupling — producers don't know consumers; matches the "(S) simulator emits, agent reacts"
flow), each rule with a **DLQ**; (2) **EventBridge Scheduler** as the *true nightly* "sleep-time" trigger — a cron
that kicks the consolidator directly, complementing the near-real-time changefeed path so the demo can show both
"consolidate on-demand" and "overnight batch." We deliberately do **not** stand up MSK/Kafka — overkill for hackathon
throughput.

**IAM.** One narrowly-scoped role per compute unit (§4). No shared "god role."

**Secrets.** AWS Secrets Manager holds: CockroachDB connection string (agent RW role), CockroachDB MCP service-account
key, the consolidator's CockroachDB role, and the changefeed webhook shared secret. Rotation enabled where the tier
supports it. Each role can read only its own secret ARN.

**Observability.** CloudWatch structured JSON logs (Powertools) with a correlation ID propagated from the changefeed
MVCC timestamp through receiver→SQS→consolidator; CloudWatch metrics + alarms (Lambda errors/throttles, **SQS DLQ
depth**, consolidation latency, Bedrock `ThrottlingException` rate, Fargate CPU/mem); **X-Ray** (or **OTel via ADOT**)
distributed traces spanning ALB→Fargate→Bedrock→CockroachDB and API GW→Lambda→Bedrock. If we adopt AgentCore Runtime,
its built-in **Observability** component gives per-session agent traces for free.

---

## 2. Changefeed sink decision (with watermark handling)

**Chosen: webhook sink → API Gateway (HTTP API) → receiver Lambda → SQS → consolidator Lambda.**

```sql
-- Credentials live in the external connection, not the URI (per 04 §3)
CREATE EXTERNAL CONNECTION memory_consolidation_webhook
  AS 'webhook-https://<api-id>.execute-api.us-east-1.amazonaws.com/consolidate?secret_via=header';

CREATE CHANGEFEED FOR TABLE episodic_events, semantic_facts, procedural_memory
INTO 'external://memory_consolidation_webhook'
WITH updated,                      -- MVCC change ts on every row (idempotency key)
     resolved = '30s',             -- watermark: "everything ≤ T delivered" → batch boundary
     min_checkpoint_frequency = '15s',
     webhook_sink_config = '{"Flush":{"Bytes":1048576,"Messages":100,"Frequency":"5s"}}';
```

**Why webhook over the alternatives:**
- **vs S3 sink:** S3 sink is cheaper and broker-free but batch/latent (files land on a `resolved` interval), which is
  fine for *nightly* but bad for a live demo where you want to trigger consolidation and see a runbook appear in
  seconds. **Keep S3 sink as the documented low-cost nightly alternative** (and it doubles as the `cdc/` archive) —
  one-line swap if cost matters more than latency.
- **vs Kafka/MSK sink:** MSK gives ordering + fan-out to many consumers but is heavy to run and expensive; unjustified
  at hackathon volume with a single consumer.

**Watermark / resolved-timestamp handling (the important part):**
- The webhook sink is **at-least-once and per-message unordered**. The receiver Lambda **must not** treat each message
  as "process now." Instead:
  1. Every data message carries the row PK + its `updated` MVCC timestamp. The receiver enqueues `(pk, mvcc_ts,
     payload_ref)` to SQS and **acks fast** (see §3 — a slow ack backpressures the changefeed).
  2. **Resolved messages** are the trigger. When the receiver sees `resolved = T`, it knows every episodic row with
     `mvcc_ts ≤ T` has been delivered. It emits a "window closed up to T" control message. The consolidator then
     safely processes the closed window `(last_watermark, T]` — a **consistent, replayable batch boundary**, not a
     partial view. `last_watermark` is persisted (a tiny `consolidation_state` row in CockroachDB, or DynamoDB) so
     restarts resume exactly where they left off.
- This makes consolidation **windowed and deterministic**: we consolidate closed intervals, we never double-count, and
  a crash just re-opens the last unclosed window.

---

## 3. Consolidation pipeline end-to-end (trigger → Lambda → Bedrock → write-back)

**Design principle: split fast-ack from slow-work.** CockroachDB's webhook sink expects a prompt `2xx`; if the
endpoint is slow it retries and **backpressures/pauses the changefeed** — you do not want a 20-second Bedrock call
sitting on the sink's critical path.

```
CHANGEFEED ──► API GW ──► (C1) receiver Lambda ──► SQS ──► (C2) consolidator Lambda ──► CockroachDB + S3
                          (validate, dedup,           (batch)  (read window, distill, embed, write back)
                           ack <2s)                                     │
                                                                        └── DLQ on repeated failure
```

**Step 1 — Receiver Lambda (C1).** Validates the shared-secret header (401 otherwise), parses the batch, drops
messages it has already seen (idempotency cache keyed on `(pk, mvcc_ts)` — a short-TTL DynamoDB table or CockroachDB
`processed_changes`), enqueues survivors to SQS, and on a `resolved` message enqueues a "close window @ T" control
event. Returns `200` in well under 2 s. Reserved concurrency capped (e.g. 5) so it can never storm connections.

**Step 2 — Consolidator Lambda (C2)**, triggered by the SQS "close window" event (near-real-time) **or** by
EventBridge Scheduler (nightly). For window `(last_watermark, T]`:
1. **Read** the episodic events in the window from CockroachDB using **follower reads**
   (`AS OF SYSTEM TIME follower_read_timestamp()`, per `04` §6) — recall/aggregation is staleness-tolerant and this
   keeps load off the leaseholder.
2. **Distill** with Bedrock: Claude Sonnet 4.6 (with Guardrails) turns the raw episodes into (a) candidate
   **semantic facts** and (b) candidate **procedural runbooks**; Haiku pre-filters obvious duplicates first to save
   tokens. Embed each candidate's text with Titan V2 (normalized).
3. **Write back** to CockroachDB **idempotently** (see below), inside the memory schema owned by `01`.
4. **Archive** the exact prompt + model response + token counts to `s3://…/consolidation/` for provenance.
5. **Advance** `last_watermark = T` (same transaction as, or immediately after, the write-back).

**Idempotency (mandatory — at-least-once delivery + retries):**
- **Semantic facts:** use the bitemporal *transition* CTE from `04` §2 — closing the current fact and opening the new
  one keyed on `(org_id, subject, predicate)`. Re-running the same window is a no-op because the "close current where
  `valid_to IS NULL`" clause matches nothing the second time, and the new fact's content hash lets us skip re-insert.
- **Procedural runbooks:** versioned by `(org_id, agent_id, name, version)` — a re-run bumps to a new version only if
  the distilled `steps` hash differs; identical distillation → skip. Never mutate `steps` in place (audit trail).
- **Window key:** `(last_watermark, T]` is itself the idempotency key — processing is a pure function of a closed
  interval, so replays are safe.
- Wrap multi-statement write-backs in the client-side `40001` retry-with-backoff helper from `04` §6; prefer
  CTE-collapsed single statements so they stay implicit, auto-retried transactions.

**Failure handling:**
- SQS **redrive → DLQ** after N attempts; CloudWatch alarm on DLQ depth (a failed consolidation is silent otherwise —
  the classic anti-pattern). Async Lambda also gets a Lambda **on-failure destination** as a second net.
- **Poison messages** (a single episode that always crashes distillation) are isolated by processing per-episode
  inside the batch and sending only the offending record to DLQ, not the whole window.
- **Bedrock throttling:** exponential backoff + jitter; on sustained `ThrottlingException`, fall back Sonnet→Haiku or
  defer the window (it's a closed interval — deferring is safe).
- **Partial write-back failure:** because each fact/runbook write is independently idempotent, a retried window
  reconciles cleanly; the watermark only advances after a successful write-back, so nothing is skipped.

---

## 4. Security & production-readiness

**IAM — one least-privilege role per compute unit:**
- *Fargate task role (agent):* `bedrock:InvokeModel` scoped to the **specific Sonnet/Haiku/Titan model + inference-
  profile ARNs** (not `*`); `bedrock:ApplyGuardrail` on the one guardrail; `s3:GetObject/PutObject` on the artifact
  bucket prefix only; `secretsmanager:GetSecretValue` on the agent-secret ARN only; CloudWatch Logs + X-Ray put.
- *Receiver Lambda role:* SQS `SendMessage` to the one queue, DynamoDB `PutItem/GetItem` on the dedup table, logs.
  No Bedrock, no DB write.
- *Consolidator Lambda role:* `bedrock:InvokeModel`/`ApplyGuardrail` (same scoped ARNs), SQS consume, S3 put on
  `consolidation/` prefix, `secretsmanager:GetSecretValue` on the consolidator-secret ARN, logs/traces. No public
  access, no operational-write beyond memory tables.
- Task/exec roles distinct from task roles; no long-lived IAM users anywhere.

**Secrets:** Secrets Manager for CockroachDB connection strings (separate least-priv DB roles for agent vs
consolidator), the MCP service-account key, and the webhook shared secret. Rotation on where supported. No secret in
env vars in plaintext, no creds in the changefeed URI (external connection only).

**Network path to CockroachDB Cloud:**
- **Hackathon:** TLS + CockroachDB Cloud **IP allowlist** of our NAT-gateway egress IP / VPC CIDR. Simple, moving.
- **Production upgrade:** **AWS PrivateLink** from our VPC to the CockroachDB Cloud cluster so agent/consolidator
  traffic never touches the public internet — the clean "Production Readiness" story. **Availability is CockroachDB-
  Cloud-plan-tier dependent (Advanced/Dedicated) — confirm with `04`; do not assume it on the free/basic tier.**
- Put the consolidator Lambda in **private subnets** with the PrivateLink/NAT path. Accept the ~sub-second VPC
  cold-start penalty — it's an async job, latency-insensitive (the cold-start-in-VPC anti-pattern only bites
  user-facing sync functions; consolidation is neither).
- **Connection pooling — explicit correction:** **RDS Proxy does NOT front CockroachDB** (RDS/Aurora only). So:
  (a) the always-on Fargate agent holds a sized long-lived pool (~`4 × vCPU`, per `04`); (b) the bursty consolidator
  Lambda is bounded by **reserved concurrency** and connects through CockroachDB Cloud's **built-in connection
  pooler** endpoint (owned/confirmed by `04`) — or a small PgBouncer if needed — to avoid a connection storm.

**Guardrails & least-privilege for the agent's *action* tools (the wedge is that the agent *acts*):**
- The agent acts on the SUM's operational tables via the CockroachDB **Managed MCP server** under a **headless
  service account** whose DB grants are scoped to exactly the operational + memory tables/schemas it needs — nothing
  else. This is the real blast-radius control.
- **Destructive/irreversible actions** are gated: a Bedrock Guardrail *denied-topic* + an explicit **human-approval
  step** in the console (or a Step Functions `Wait`-for-callback for the stretch multi-agent split) before the agent
  executes anything the demo classifies as high-risk. Everything the agent does is **recorded** as an episodic event
  in the same ACID transaction as the operational mutation (the single-store wedge) — which also gives a complete,
  queryable audit trail for free.
- Bedrock Guardrails on **both** the reasoning path (screen injected alert content) and the consolidation output
  (don't let a poisoned episode distill into a harmful runbook).

---

## 5. Cost sketch — 4-week hackathon (rough monthly, AWS only; CockroachDB Cloud owned by `04`)

**Order-of-magnitude only — reconfirm Bedrock/Fargate unit prices on the pricing pages before budgeting.**

| Component | Config | Rough $/mo |
|---|---|---|
| ECS Fargate (agent+backend) | 1 task, 0.5 vCPU / 1 GB, always-on | ~$18–36 |
| ALB (for WebSocket) | 1 ALB, low LCU | ~$16–22 |
| Lambda (receiver + consolidator) | low volume, ARM64 | ~$0–2 (mostly free tier) |
| API Gateway (HTTP API) | low volume | ~$0–1 |
| SQS + DynamoDB dedup | tiny | ~$0–1 |
| **Bedrock — Claude Sonnet 4.6 + Haiku** | demo-bounded traffic, prompt caching | **~$20–80 (the swing factor)** |
| Bedrock — Titan V2 embeddings | very cheap per token | ~$1–5 |
| S3 + CloudFront | small assets/artifacts | ~$1–3 |
| Secrets Manager | ~4 secrets @ ~$0.40 | ~$1–2 |
| CloudWatch / X-Ray | short retention | ~$2–5 |
| **Total AWS (excl. CockroachDB)** | | **~$60–160/mo**, Bedrock-dominated |

**How to stay cheap:**
- Route most calls to **Haiku**; reserve Sonnet 4.6 for genuinely hard reasoning; turn on **prompt caching** for the
  stable system/runbook context.
- Run **1 Fargate task**; if you can live without WebSocket, drop the ALB and use **App Runner** (~$5/mo idle, built-in
  HTTPS/TLS, no VPC/ALB to manage) — accept SSE-only streaming and add a VPC connector for PrivateLink. Or, cheapest of
  all for MVP, a single **Lambda Function URL** backend (loses persistent pool + true streaming).
- Batch embeddings; keep raw blobs in S3 (out of CockroachDB rows); **CloudWatch log retention = 7 days**.
- **Tear the stack down between work sessions** (`cdk destroy`) and lean on **AWS credits** — this is a throwaway env.

---

## 6. Deployment approach

- **IaC: AWS CDK (Python).** Rationale: one language shared with the Python agent + consolidation code (less
  context-switching for a 2–4 person team); stable L2 constructs for Lambda, Fargate/ALB, S3, EventBridge, API GW,
  IAM, and now **AgentCore Runtime** (per `04`, CDK L2 stable) — so adopting the AgentCore upgrade later is a construct
  swap, not a rewrite. SAM is cleaner for *pure* serverless but weaker for the Fargate/ALB/mixed footprint; Terraform
  is fine but gives up the tightest AWS-native + AgentCore integration.
- **Stacks:** keep it lean — `SharedStack` (VPC, secrets, S3, CockroachDB connectivity, guardrail) + `AppStack`
  (Fargate service, ALB, Lambdas, API GW, SQS, schedules). One region for AWS **compute** (us-east-1); CockroachDB
  spans 3 regions for the failover demo — **our compute living in one region is fine, because RPO=0 is CockroachDB's
  property, not our Lambda's.** Optionally a warm standby agent task in a second region for the "app keeps serving
  during the region kill" narrative, but the store is what proves the wedge.
- **Environments:** a single throwaway `dev`/`demo` account; no separate prod stack for a hackathon. Config via CDK
  context. GitHub Actions for `cdk deploy` on main; `sam local`-style local iteration for the consolidator via a small
  test harness that replays a captured changefeed batch.

---

## ⚠️ Charter challenge

None. This plan obeys the charter: CockroachDB is the sole memory + operational store (AgentCore *Memory* is used
nowhere as a store — at most ephemeral working state, and even that is optional); Bedrock + Lambda + S3 are the AWS
spine; the agent *acts* on real operational data in one ACID transaction with its memory write (the wedge). One
**alignment flag, not a challenge:** the embedding dimension (`VECTOR(1536)` in `01`/`04` vs Titan V2's 1024) must be
reconciled — see §1.2 and §B.

---

## A. Decisions & recommendations

1. **Hosting:** **ECS Fargate** (one always-on service, ALB for WebSocket) hosts the agent + web backend for the MVP —
   warm CockroachDB pool, streaming ChatOps, full control, clean fail-over around the region-kill demo, predictable
   ~$40–60/mo. **AWS Lambda is used only for async consolidation** (its right job; wrong for the interactive agent
   because of the 900 s cap + connection churn). **AgentCore Runtime is the documented production-readiness upgrade**
   (Firecracker microVM per-session isolation, up to **8 h** sessions [verified: `maxLifetime` default 28800 s,
   idle-timeout default 900 s, both configurable 60–28800 s], managed Identity/Observability/Guardrails) — adopt it
   for the agent container if time permits; it maps 1:1 to the "Production Readiness" judging criterion and, because it
   just needs the agent image in ECR, it's a low-risk swap. The React SPA is served from **S3 + CloudFront**
   regardless.
2. **Changefeed sink:** **webhook → API Gateway HTTP API → receiver Lambda → SQS → consolidator Lambda**, with
   `resolved`-timestamp **window closing** for consistent, idempotent batches. **S3 sink retained as the cheap
   nightly-batch alternative** (one-line changefeed swap). No MSK.
3. **Bedrock models:** **Claude Sonnet 4.6** (reasoning) + **Claude 3.5 Haiku** (cheap paths) + **Titan Text
   Embeddings V2 @ 1024, normalized** (embeddings). Bedrock **Guardrails** on reasoning + consolidation.
4. **IaC:** **AWS CDK (Python)**, two lean stacks, single throwaway account, one compute region.

## B. Interfaces I expose / depend on

**I expose (to other docs):**
- **Deployment surface for `02`'s agent:** a Fargate service (or AgentCore Runtime container) running the agent
  reasoning loop + FastAPI backend, with Bedrock model access, Secrets Manager creds, and MCP connectivity wired in.
- **Consolidation compute for `02`'s sleep-time design:** the receiver+consolidator Lambda pipeline, the SQS/DLQ,
  the EventBridge nightly schedule, and the idempotent write-back contract (bitemporal-transition for facts, versioned
  upsert for runbooks).
- **The AWS endpoints/ARNs** the changefeed targets (API GW URL, external-connection secret), the S3 artifact bucket +
  prefixes, the CloudWatch/X-Ray observability plane, and the Bedrock Guardrail ARN.

**I depend on:**
- **`01` (memory schema):** table/column definitions I write back into — I consume, never redefine. **Hard dependency:
  the vector dimension** — I need `01` to standardize on **`VECTOR(1024)`** (Titan V2) or explicitly choose Titan G1
  (1536); the two must match `02`'s embedding call.
- **`02` (agent + tool interface + framework):** the agent container image / entrypoint I host, the recall/act/record
  tool contract, and the consolidation logic that runs inside my Lambda.
- **`04` (CockroachDB deployment):** the cluster connection endpoint(s), the **built-in connection-pooler** endpoint,
  **PrivateLink availability for the chosen plan tier**, the MCP service-account provisioning, and the changefeed
  being created against my webhook external connection.

## C. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Embedding-dimension mismatch (1536 vs Titan V2 1024) | Recall silently broken / index build fails | Standardize on `VECTOR(1024)` + Titan V2 across `01`/`02`/`03` **before** any embedding is written (§1.2, §B). |
| Slow consolidator backpressures the changefeed | Changefeed pauses; memory stream stalls | Two-stage fast-ack receiver + SQS decouple; consolidator never sits on the webhook critical path (§3). |
| RDS Proxy assumed for CockroachDB | Connection storm from Lambda; wasted effort | RDS Proxy is RDS/Aurora-only — use CockroachDB's built-in pooler + Lambda reserved concurrency + warm Fargate pool (§4). |
| At-least-once delivery double-writes facts/runbooks | Duplicate/inconsistent memory | Bitemporal-transition CTE + versioned-runbook + window-key idempotency; `40001` retry helper (§3). |
| VPC cold start on consolidator | Slower async runs | Acceptable (async, latency-insensitive); keep the *interactive* agent out of Lambda entirely (Fargate) (§4). |
| Bedrock throttling / cost spike | Failed or expensive consolidation | Backoff+jitter, Sonnet→Haiku fallback, prompt caching, defer closed windows; DLQ + alarms (§3, §5). |
| PrivateLink not on chosen CockroachDB tier | Traffic over public internet | Hackathon: IP-allowlist + TLS; confirm PrivateLink tier with `04` for the prod story (§4). |
| Region-failover demo: our compute in one region looks like a SPOF | Weakens the money-shot | Emphasize RPO=0 is CockroachDB's; optionally a warm standby Fargate task in a 2nd region so the *app* also survives (§6). |
| Silent consolidation failures | Lost runbooks, no alert | SQS DLQ + Lambda on-failure destination + CloudWatch alarm on DLQ depth (never no-DLQ) (§3). |

## D. Cost sketch (summary)

**~$60–160/mo of AWS** for the 4-week build, **Bedrock-dominated** (~$20–85 of it), the rest split across Fargate+ALB
(~$35–60) and small-change serverless/storage/observability. **Excludes CockroachDB Cloud (owned by `04`).** Cheapest
lever: push traffic to Haiku + prompt caching; drop the ALB via App Runner if WebSocket isn't required; run one
Fargate task; `cdk destroy` between sessions; use AWS credits. All unit prices are indicative and must be reconfirmed
on the current Bedrock/Fargate pricing pages before committing a budget.
