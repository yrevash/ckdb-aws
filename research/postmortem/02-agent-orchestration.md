# 02 — Agent Orchestration (the reasoning loop, tools, consolidation, framework)

> **Owner:** `02` (Agent + Consolidation).
> **Obeys:** `00-charter.md`. **Depends on:** `01` (memory + operational schema — the contract), `03` (hosting, Bedrock, Lambda, changefeed wiring, IAM).
> **Consumed by:** `03` (deploys this), `06` (visualizes tool calls + memory timeline).
> Framework/API facts below were verified against AWS docs via the AgentCore knowledge tools on 2026-07-30 (AgentCore Runtime, Strands SDK, Bedrock Converse, AgentCore Memory). CockroachDB agent-tool facts come from `deep-dive/cockroachdb/03-agent-toolchain.md`.

---

## 0. TL;DR

Postmortem is a **single Strands agent** running a **Perceive → Recall → Reason → Act → Record** loop, hosted on **Amazon Bedrock AgentCore Runtime**, reasoning on **Claude Sonnet via the Bedrock Converse API**, with a **second background consolidator agent on Lambda** (sleep-time compute). Memory and operational data are **one CockroachDB store**, so the wedge — *a memory write and a remediation write commit in one ACID transaction* — is achieved by a **direct-SQL transactional tool**, not MCP. **MCP is the read/recall + introspection surface (read-only by default); direct pooled SQL is the explicit write path.** This split is the central design decision of this doc.

---

## 1. The core reasoning loop

### 1.1 Stage contract

| Stage | Trigger | Reads | Writes | Latency budget |
|---|---|---|---|---|
| **Perceive** | Alert (EventBridge/changefeed → Runtime) or SRE message (console) | raw signal | working/session state row | < 100 ms |
| **Recall** | after Perceive | `episodic_memory`, `semantic_facts`, `procedural_runbooks` (vector + relational), operational tables | — (read-only) | < 300 ms (recall@k ≥ 95%, staleness 0 ms) |
| **Reason** | after Recall | assembled context | — | model-bound (1–5 s) |
| **Act** | model emits tool call | operational tables + `ccloud` | operational + memory (atomic) | tool-bound |
| **Record** | after Act (or bundled with Act) | — | `episodic_memory` (+ working-state close) | in the Act transaction where atomic |

Design invariants (from charter §4 + core-concepts §6):
- **Async memory writes never block the response path** — the *episodic append that is NOT part of the remediation transaction* is fire-and-forget; the *remediation-coupled* episodic write is synchronous by necessity (that is the wedge).
- **Read-your-own-writes**: because Recall queries the same CockroachDB cluster the last Record committed to, a just-written memory is immediately visible — no eventual-consistency lag (charter metric: 0 ms staleness).
- **Reranking beyond raw cosine** happens in Recall (§1.3).

### 1.2 Sequence diagram (text)

```
 SRE / Alert        AgentCore Runtime (Strands agent)         CockroachDB (one store)      Bedrock         ccloud
     │                          │                                     │                       │              │
     │──alert / message────────▶│  PERCEIVE                           │                       │              │
     │                          │  open session row (working mem) ───▶│ INSERT session_state  │              │
     │                          │                                     │                       │              │
     │                          │  RECALL                             │                       │              │
     │                          │  embed(query) ─────────────────────────────────────────────▶│ (Titan v2)  │
     │                          │  vector+relational recall (SQL) ───▶│ SELECT ... ORDER BY    │              │
     │                          │   (episodic+semantic+procedural,    │  embedding <-> $q      │              │
     │                          │    bitemporal-current, scoped)  ◀───│ rows + runbook         │              │
     │                          │  rerank + assemble context          │                       │              │
     │                          │                                     │                       │              │
     │                          │  REASON (Converse, tools bound) ───────────────────────────▶│ plan/tooluse │
     │                          │◀──── toolUse: remediate_and_record ─────────────────────────│              │
     │                          │                                     │                       │              │
     │                          │  ACT + RECORD (ONE txn)             │                       │              │
     │                          │  BEGIN;                             │                       │              │
     │                          │   UPDATE deploys SET status=...  ──▶│                        │              │
     │                          │   INSERT incident_actions       ──▶│  SERIALIZABLE          │              │
     │                          │   INSERT episodic_memory(outcome)──▶│  one ACID commit       │              │
     │                          │  COMMIT; ◀──────────────────────────│ ✔ memory+action agree │              │
     │                          │   (optional) scale_data_tier ──────────────────────────────────────────────▶│ ccloud
     │◀── response + citations ─│  RECORD (async episodic narration) ▶│ INSERT episodic (bg)   │              │
     │                          │                                     │                       │              │
                    (later) CockroachDB CHANGEFEED on episodic_memory ──▶ Lambda consolidator (§3)
```

### 1.3 Recall: the query + rerank strategy

Recall is a **hybrid, scoped, bitemporal-aware** retrieval issued as SQL (see §2.1 for why SQL not MCP here):

1. **Embed** the incident signature (alert labels + service + symptom text) with **Titan Embeddings V2**, normalized to unit length (charter §7; cosine opclass).
2. **Candidate fetch** — three parallel scoped queries against the co-located store:
   - *Episodic*: nearest past incidents by `embedding <-> $q`, filtered `WHERE tx_to IS NULL` (bitemporal current) and scope (`org_id`, optionally `service_id`).
   - *Semantic*: current distilled facts about the affected services/topology.
   - *Procedural*: the top runbook(s) whose trigger-embedding matches, plus any runbook explicitly linked to the matched past incidents (`runbook_id` FK).
3. **Rerank** candidates by a composite score, not raw cosine: `score = w1·cosine + w2·recency(valid_from) + w3·outcome_success_rate + w4·scope_match`. This is the "reranking beyond raw vector similarity" production requirement. (v1: weights are constants; keep them in config for tuning by `05`.)
4. **Budget**: keep top-k episodes (k≈3), top facts (≤10), and **exactly one primary runbook** to control context size and cost.

### 1.4 Reason: context assembly + prompt strategy

The Converse request is assembled deterministically (order matters for prompt caching — stable prefix first):

```
[ system ]  (STATIC, cache-eligible prefix)
  Role: Postmortem, an on-call SRE agent. Safety rules. Tool-use policy.
  Output contract: propose_action | remediate_and_record | ask_human | escalate.
  "You MUST cite the memory_id / runbook_id that justifies any action."

[ system: RETRIEVED MEMORY ]  (DYNAMIC, per-incident)
  ## Similar past incidents (episodic)
   - [mem:ep_8842 | 2026-05-02 | resolved in 6m] payments-api 5xx spike after deploy
     d9f… → action: rollback deploy d9f…; outcome: SUCCESS (MTTR 6m)
  ## Known facts (semantic)
   - payments-api depends on ledger-svc; canary at 5% since 2026-06.
  ## Procedural runbook (surfaced, ranked #1)
   RUNBOOK rb_017 "5xx spike post-deploy" (confidence 0.92, derived from 4 incidents)
     step 1: confirm deploy correlation (query deploys WHERE service=$s ORDER BY ts DESC)
     step 2: if error-onset ≈ deploy time → rollback via remediate_and_record
     step 3: verify 5xx returns to baseline; if not → escalate

[ user ]  current alert / SRE message + live operational snapshot (current deploy, SLO burn)
```

**Runbook surfacing + execution.** A runbook is retrieved procedural memory rendered as **explicit numbered steps in the system block**, each step annotated with the *tool* it maps to. The agent does not "free-solo": the tool-use policy instructs it to **follow the ranked runbook's steps in order**, and each step's action is a concrete tool call. If no runbook matches (cold path), the agent reasons from episodic + semantic memory and its base instructions, and the resulting episode becomes raw material the consolidator later distills into a *new* runbook (§3). This is procedural memory closing the loop.

**Grounding / anti-hallucination.** The output contract forces the model to cite the `memory_id`/`runbook_id` backing any action; the Act tool **rejects** an action whose cited id does not exist (a cheap provenance check). Bedrock **Guardrails** (owned by `03`) wrap I/O for PII/content filtering.

### 1.5 Act + Record: the one-transaction wedge

The single most important tool is `remediate_and_record` (§2.2). It performs the operational mutation **and** the episodic outcome write in **one CockroachDB SERIALIZABLE transaction**, exploiting co-location (charter §6: operational tables live beside memory). Pseudocode:

```
def remediate_and_record(action, target, cited_memory_id, outcome_stub):
    with pool.transaction(isolation=SERIALIZABLE) as tx:      # BEGIN
        assert tx.exists(cited_memory_id)                     # provenance gate
        tx.apply_operational_action(action, target)           # e.g. UPDATE deploys
        action_id = tx.insert("incident_actions", {...})      # audit row
        tx.insert("episodic_memory", {                        # the memory write
            "kind": "action_outcome", "action_id": action_id,
            "embedding": embed(action_summary), "provenance": cited_memory_id,
            "valid_from": now(), "tx_to": None })
    # COMMIT: memory and operational state can never disagree (wedge proof #2)
    return action_id
```

If the operational write fails, the memory write rolls back with it, and vice-versa — **they are the same transaction**. This is exactly what AgentCore Memory (a separate managed store) *cannot* do, which is the charter's justification for CockroachDB.

---

## 2. Tool design (the agent's interface)

### 2.1 MCP vs direct SQL — the recommendation

**Recommendation: use BOTH, split by role.**

| Concern | CockroachDB Managed MCP | Direct pooled SQL (psycopg/asyncpg) |
|---|---|---|
| Recall / introspection (read) | ✅ **Use here.** Read-only by default, RBAC per call, system-table deny-list, structured audit logs, zero infra. | possible but loses the audit/safety story |
| **Atomic memory+action write (the wedge)** | ❌ **Cannot.** Managed MCP is stateless hosted HTTP; each `insert_rows` call is its own transaction. It **cannot hold `BEGIN…COMMIT` across statements**, so it cannot bind an operational UPDATE + an episodic INSERT into one txn. | ✅ **Required here.** Only a persistent SQL session/pool can open a multi-statement SERIALIZABLE transaction. |
| Destructive ops (`DROP`/`TRUNCATE`) | Hard-blocked regardless of consent (good). | Governed by our own allowlist + validation hook (replicate the plugin's pre-exec SQL guard). |

So: **MCP is the safe read/recall + human-in-the-loop write-consent demo surface; the direct-SQL tool is the transactional write path** that delivers the one-ACID-transaction wedge. This is not a compromise — it is the correct decomposition, and it lets the demo *show* MCP's read-only-default + OAuth write-consent screen (a trust story) while the load-bearing atomic write happens over SQL where it must.

> Assumption (flag to `03`/`04`): the agent holds a CockroachDB connection pool (service-account SQL user, tightly scoped RBAC) **and** an MCP service-account key (read-scoped). Both credentials in Secrets Manager, minted per charter/toolchain guidance.

### 2.2 Tool catalog

| Tool | Category | Backend | Txn? | Write-gated? |
|---|---|---|---|---|
| `recall_memory(query, scopes, k)` | Memory read | **MCP** `select_query` (or SQL fallback) | no | no |
| `get_operational_state(service)` | Read | **MCP** `select_query` | no | no |
| `remediate_and_record(action, target, cited_id, outcome)` | **Act+Record (atomic)** | **Direct SQL** | **yes (1 txn)** | **yes** |
| `record_episode(event)` | Memory write (async narration) | Direct SQL (single insert) | single | no |
| `update_incident_state(incident_id, status)` | Incident-state write | Direct SQL | single | soft |
| `scale_data_tier(cluster, target)` | Data-tier scaling | **`ccloud`** CLI `-o json` | n/a | **yes** |
| `propose_action(plan)` | Control (no side effect) | — | no | no |
| `escalate(reason)` / `ask_human(q)` | Control | console | no | no |

Operational actions on the **system-under-management** (`restart`, `scale`, `rollback`, `feature-flag`) are all expressed **through `remediate_and_record`** with an `action` enum, because every one of them must be co-recorded atomically. `scale_data_tier` is the exception — it acts on the **CockroachDB data tier itself via `ccloud`** (an external control plane, not a SQL row), so it cannot be inside the SQL transaction; it is therefore a **separate, human-gated, idempotent** tool, and its outcome is recorded via a follow-up `record_episode` (accepting non-atomicity for this out-of-band control-plane action, and saying so).

### 2.3 Safety model

Three stacked gates (mirrors the MCP toolchain's own layering):
1. **Read-only default** — recall/introspection need no consent; MCP enforces this and deny-lists system tables.
2. **Explicit write path** — `remediate_and_record`, `scale_data_tier`, `update_incident_state` are the *only* mutating tools; each runs a **pre-execution validator** (allowlist of action verbs, parameter schema check, destructive-verb block) before touching the DB.
3. **Human-in-the-loop for high-blast-radius actions** — a policy tier (`rollback` on payments critical path, any `scale_data_tier`, region failover) requires SRE approval in the console before the transaction commits; low-risk actions (feature-flag toggle on non-critical service) can auto-execute. Approval state itself is a row, so it is auditable. (AgentCore **Policy**/Cedar or Bedrock Guardrails can back this at `03`'s discretion.)

---

## 3. Sleep-time consolidation agent (dual-agent pattern)

### 3.1 Trigger + placement

Per charter §6 and `03` ownership: a **CockroachDB changefeed** on `episodic_memory` (and `incident_actions`) emits new/closed episodes → sink → **AWS Lambda consolidator**. The consolidator is a *second agent* (Letta-style sleep-time compute) that runs **off the hot path** — it never blocks the responder. Batching: the changefeed can fire per-row, but the consolidator debounces (e.g., process when an incident reaches a terminal state, or on a schedule over the last N new episodes) to distill patterns rather than single events.

### 3.2 Algorithm

```
on_batch(new_episodes):
  # 1. GROUP raw episodes into candidate patterns
  clusters = cluster_by(embedding_similarity + service + symptom, new_episodes ∪ recent_history)

  for cluster in clusters:
    # 2. DISTILL with an LLM (Bedrock, cheaper model e.g. Nova/Haiku)
    facts    = extract_semantic_facts(cluster)      # durable truths: topology, dependency, config
    runbook  = synthesize_runbook(cluster)          # ordered steps that led to SUCCESS outcomes
                                                     # weighted by outcome_success_rate

    # 3. DEDUPLICATE / RECONCILE (Mem0-style ADD/UPDATE/DELETE/NOOP)
    for f in facts:
      existing = vector_lookup(semantic_facts, f)
      decision = llm_reconcile(existing, f)          # ADD | UPDATE | NOOP | CONTRADICT
      if decision == UPDATE or CONTRADICT:
        # BITEMPORAL transition, not overwrite:
        close_current(existing, tx_to=now())         # retire old belief on transaction timeline
        insert(semantic_facts, f, valid_from=..., supersedes=existing.id, tx_to=None)

    rb = vector_lookup(procedural_runbooks, runbook.trigger)
    upsert_runbook(rb, runbook, confidence = success_count / attempt_count)

    # 4. WRITE BACK WITH PROVENANCE (one txn per pattern)
    with tx:
      link every new fact/runbook -> source episode_ids (provenance array)
      stamp author='consolidator', model_id, created_at
```

### 3.3 Properties

- **Bitemporal, not destructive** (charter §5): a changed fact becomes a *transition* — old row keeps `valid_from..valid_to`/`tx_to`, new row supersedes it. The agent's Recall always filters `tx_to IS NULL`, so it reads current belief; the timeline panel (`06`) can replay history.
- **Provenance** (core-concepts §4): every distilled fact/runbook carries `source_episode_ids`, `author`, `model_id`, `created_at` — the audit trail. (Stretch: sign these rows.)
- **Manufactures procedural memory**: a runbook is born from ≥N episodes that shared a symptom and a SUCCESS remediation. Once written, the *responder* surfaces it in Recall (§1.3), so the next similar incident is handled by an institution-learned runbook instead of cold reasoning. **This is the demo's "overnight consolidation → new runbook" beat (charter §9.4).**
- **Read-your-own-writes across agents**: consolidator writes to the same cluster the responder reads → the new runbook is *immediately* visible to the live agent, no sync lag (charter metric: cross-agent visibility no lag). Both agents sharing one strongly-consistent store is what makes this trivial.

---

## 4. Framework decision (owned here)

Requirements to satisfy: (a) clean CockroachDB integration — must allow a **tool that owns a live SQL transaction** for the wedge; (b) runs on AWS with Bedrock; (c) tool use + background/second agent; (d) shippable in ~4 weeks by a small team.

| Criterion | **Strands Agents (+ AgentCore Runtime)** | LangGraph (+ AgentCore Runtime) | Claude Agent SDK |
|---|---|---|---|
| AWS/Bedrock nativeness | ✅ First-class; AWS-authored; Bedrock default | ◑ Works on Bedrock; AgentCore hosts it | ◑ Can target Bedrock; less AWS-native ops glue |
| Hosting | ✅ AgentCore Runtime (serverless, microVM session isolation, up to 8 h, MCP+A2A, built-in Identity) | ✅ Same Runtime (framework-agnostic) | ◑ Runtime is framework-agnostic so it can host custom code, but no first-party pattern |
| **CockroachDB txn tool** | ✅ Tools are plain Python `@tool` fns; `invocation_state` injects a live pool/connection → open `BEGIN…COMMIT` inside a tool trivially | ✅ Nodes are Python; can hold a txn; `langchain-cockroachdb` vectorstore + checkpointer exist | ✅ Python tools can do it too |
| Control-flow fit for Perceive→Recall→Reason→Act→Record | ✅ Model-driven loop maps directly; low ceremony | ◑ Explicit graph is powerful but heavier than our linear loop needs | ✅ Simple tool loop |
| Multi-agent (stretch) | ✅ agents-as-tools + A2A (`StrandsA2AExecutor`) | ✅ Strong graph orchestration | ◑ Sub-agents supported, less AWS-integrated |
| Model routing (Nova for cheap classify, Sonnet for reason) | ✅ Swap `model=` per agent | ✅ per-node | ◑ Claude-centric |
| MCP client (for CockroachDB Managed MCP recall) | ✅ Native MCP tool support | ✅ | ✅ |
| Team velocity / risk | ✅ Minimal boilerplate; AWS samples pair Strands+AgentCore+Memory | ◑ More concepts to learn | ◑ Great for coding agents; less ops-agent precedent |

**Decision: Strands Agents SDK, hosted on Amazon Bedrock AgentCore Runtime, reasoning on Bedrock Claude Sonnet.**

Rationale:
1. **AWS-native, lowest glue.** Charter runs on AWS/Bedrock; Strands is AWS's own SDK and AgentCore Runtime is its purpose-built host (serverless, per-session microVM isolation, MCP + A2A, built-in Identity, up to 8-hour executions). Verified against AWS docs.
2. **The wedge is easy and explicit.** Strands tools are ordinary Python functions and `invocation_state` passes live objects (DB pools, session_id) into tools — so `remediate_and_record` can open a single CockroachDB SERIALIZABLE transaction directly. No framework abstraction fights us on the one thing that matters most.
3. **We deliberately DO NOT use AgentCore Memory.** It exists and is good, but it is a *separate* managed store — it cannot co-transact with operational data, which is precisely the charter's reason CockroachDB is necessary (§4). We keep Strands' session/memory hooks pointed at **CockroachDB** (short-term session state as rows; long-term as the memory tables). This is a feature, not a gap: it is the demo's whole point.
4. **Model routing** falls out naturally — the responder uses Sonnet; the consolidator and the alert-classifier use a cheaper model (Nova/Haiku) via a one-line `model=` swap.
5. **Stretch path is clean** — agents-as-tools / A2A give detector/responder/consolidator without a rewrite.

Tradeoffs / when I'd switch: if the control flow needed **durable, resumable, branch-heavy state machines** (many conditional recovery branches, human-approval waits spanning hours), **LangGraph**'s explicit graph + checkpointer would earn its weight, and `langchain-cockroachdb` gives a ready vectorstore/checkpointer on CockroachDB. Our loop is linear enough that Strands wins on velocity. **Claude Agent SDK** is excellent for computer-use/coding agents but is less AWS-ops-native and more model-locked; not the best fit for an AWS SRE agent that must route models and integrate with AgentCore Identity/Policy. (LangGraph and Strands both host on the *same* AgentCore Runtime, so the hosting bet is not lost either way — this de-risks the choice.)

---

## 5. How memory makes the agent measurably better (MTTR)

The charter's headline metric is **MTTR with memory vs cold** (§8). Concretely:

| Behavior | Cold-start agent (no memory) | Postmortem (with memory) |
|---|---|---|
| Recall step | none — reasons from alert text + base prompt only | retrieves the *actual past incident* + the proven fix + the runbook |
| Diagnosis | re-derives root cause from scratch, may explore wrong branches | jumps to "this matches ep_8842: 5xx after deploy → rollback" |
| Action selection | generic, may pick a plausible-but-wrong remediation | picks the remediation that **previously produced a SUCCESS outcome** (reranked by `outcome_success_rate`) |
| Steps to resolution | many tool calls / clarifying questions | runbook-guided, few steps |
| Result | high, variable MTTR; org **re-solves the same outage** | **lower, tighter MTTR**; institutional knowledge compounds |

The measurable delta (instrumented by `05`): for a *recurring* incident class, memory converts an open-ended reasoning task into a **retrieval + confirm + execute** task. The demo shows the *same* alert handled twice — first cold (agent reasons, resolves slowly), then after that episode exists (agent recalls it and resolves fast, changing the action it takes). Charter demo thesis §9.1: "memory is load-bearing — recall changes the action." The consolidation loop makes this compound: after N incidents, a runbook exists and even the *first* occurrence of a *similar* incident benefits.

Second-order effects tied to metrics: **read-your-write staleness = 0 ms** means a memory written mid-incident is usable later in the *same* incident; **RPO=0 region survival** means the memory that drives MTTR reduction does not evaporate in an outage (a cold agent after a region loss is back to square one — Postmortem is not).

---

## 6. (Stretch) Multi-agent split + shared memory

Split into three roles, each a Strands agent (agents-as-tools or A2A on AgentCore Runtime):

- **Detector** — cheap model (Nova). Watches the alert stream, deduplicates, classifies severity, opens an incident row, and hands off. Perceive + a thin Recall.
- **Responder** — Sonnet. The full Perceive→Recall→Reason→Act→Record loop of §1; owns remediation.
- **Consolidator** — sleep-time, on Lambda (§3). Distills episodes → facts/runbooks.

**Shared memory with strong consistency** is the easy part *because of the store*: all three agents read and write the **same CockroachDB cluster**, which is SERIALIZABLE and multi-region with RPO=0. So:
- No message-bus memory sync, no eventual consistency, no per-agent memory copies to reconcile (the failure mode called out in core-concepts §4).
- The detector's incident row, the responder's action+episode, and the consolidator's runbook are all **immediately visible to each other** the instant they commit (read-your-own-writes across agents — charter metric).
- Coordination/handoff state (who owns the incident, approval gates) is **rows in CockroachDB**, so contention is resolved by the database's transactions (e.g., `SELECT … FOR UPDATE` to claim an incident) rather than by application-level locking. Governance/provenance scopes (`org_id`, `agent_id`, `incident_id`) from core-concepts §4 tag every write, so multi-agent memory stays attributable and auditable.

This is the strongest form of the wedge: *shared, strongly-consistent, survivable agent memory* that graph-DB / vector-DB stacks cannot offer without stitching systems together. **v1 recommendation: ship the single responder + the background consolidator (already a 2-agent system); add the detector split only if core is bulletproof** (charter scope discipline).

---

## A. Decisions & recommendations

1. **Framework: Strands Agents SDK on AgentCore Runtime, Bedrock Claude Sonnet** for the responder; cheaper model (Nova/Haiku) for classifier + consolidator. LangGraph is the documented fallback (same Runtime host, `langchain-cockroachdb` available) if we need durable branch-heavy state machines.
2. **MCP vs SQL: both, split by role.** MCP (read-only default, RBAC, audit) for Recall/introspection and the human-write-consent demo; **direct pooled SQL for the atomic write path** — because managed MCP cannot hold a multi-statement transaction, and the wedge requires one.
3. **The wedge = one tool.** `remediate_and_record` runs the operational mutation + episodic memory write in a single CockroachDB SERIALIZABLE transaction (co-located tables). Non-atomic control-plane action (`scale_data_tier` via `ccloud`) is separate, idempotent, human-gated, and recorded after the fact.
4. **Single-vs-multi agent for v1: two agents** — live responder + background consolidator (sleep-time compute via changefeed→Lambda). Detector split is stretch.
5. **We deliberately do NOT use AgentCore Memory** — CockroachDB is the memory store (that is the charter's raison d'être); Strands' session hooks point at CockroachDB rows.
6. **Safety: three stacked gates** — read-only default, single explicit validated write path, human-in-the-loop for high-blast-radius actions; Bedrock Guardrails + (optionally) AgentCore Policy/Cedar at the boundary (owned by `03`).

## B. Interfaces I expose / depend on

**Expose (consumed by `03` deploy, `06` UI):**
- **Tool contract** — the §2.2 catalog (names, params, txn semantics, gating). `06` visualizes each call on the memory-timeline; `03` wires IAM/Secrets for the SQL pool + MCP key + `ccloud` creds.
- **Reasoning-loop contract** — Perceive→Recall→Reason→Act→Record stages with the latency budgets in §1.1 (targets for `03`/`05` to instrument).
- **Consolidator contract** — consumes a changefeed on `episodic_memory`/`incident_actions`; emits facts/runbooks with provenance (`03` owns the changefeed→Lambda plumbing and the consolidator's Bedrock IAM).

**Depend on:**
- **`01` (schema — hard dependency).** I assume these tables/columns exist (flagging so `01` can confirm or correct): `episodic_memory`, `semantic_facts`, `procedural_runbooks` each with `embedding VECTOR` (cosine opclass, C-SPANN index), **bitemporal** columns (`valid_from`, `valid_to`, `tx_from`, `tx_to`), **scope** columns (`org_id`, `agent_id`, `session_id`, `incident_id`, `service_id`), and **provenance** (`source_episode_ids`, `author`, `model_id`, `created_at`, `supersedes`); operational tables `services`, `deploys`, `incidents`, `orders`, plus my write targets `incident_actions` and `session_state`. **Co-location requirement**: memory tables and operational tables in the **same database/cluster** so one transaction spans both (charter §6). If `01` names things differently, only the tool SQL changes, not the design.
- **`03` (hosting — hard dependency).** AgentCore Runtime for the Strands agent; Lambda for the consolidator; changefeed sink; Bedrock model access (Sonnet + embeddings + cheap model); Secrets Manager for the SQL service-account, MCP key, `ccloud` key; IAM least-privilege; Guardrails. Hosting choice (Runtime vs Lambda/ECS) is `03`'s call — my design assumes **Runtime** and degrades gracefully to a container if `03` decides otherwise (the Strands agent is portable).
- **`04`** — CockroachDB version with cosine vector opclass + C-SPANN; Managed MCP service account (read-scoped); `ccloud` for the `scale_data_tier`/failover mechanics.

## C. Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **MCP cannot do the atomic write** (assumed as a compromise) | Would break the wedge | **Designed around it**: direct-SQL tool owns the transaction; MCP is read/consent only. This is the core decision, not a workaround. |
| `01` schema drifts from my assumptions | Tool SQL breaks | Isolated all schema coupling in the tool layer; treat §B as a checklist to confirm with `01` before build. |
| Agent takes an unsafe operational action (payments critical path) | Real damage in SUM | Human-in-the-loop gate for high-blast-radius actions; pre-exec validator + action allowlist; provenance-citation gate rejects ungrounded actions; Guardrails. |
| Consolidator hallucinates a bad runbook | Poisons future decisions | Runbooks carry `confidence = success/attempts` and provenance; only SUCCESS-outcome episodes seed steps; bitemporal supersession (no destructive overwrite) allows rollback of a bad belief; `05` evals gate runbook quality. |
| LLM latency blows the Reason budget | Slow MTTR, hurts the metric | Prompt caching on the static system prefix; cheap model for classify; keep context tight (k≈3, one runbook); set `maxTokens` explicitly (avoids quota-reservation throttling). |
| Managed MCP is stateless/hosted → per-call auth+latency | Recall latency | Prefer direct SQL for hot recall if MCP latency threatens the 300 ms budget; keep MCP for the demo's consent/audit story. Benchmark early (`05`). |
| Non-atomic `scale_data_tier` (ccloud) fails after op recorded | Memory/reality mismatch on control-plane action | Idempotent + human-gated + post-hoc `record_episode`; explicitly scoped OUT of the atomicity guarantee and documented as such. |
| Two agents write conflicting memory | Corrupt shared state | Single strongly-consistent store + SERIALIZABLE + `FOR UPDATE` incident claim; scope tags for attribution; this is where CockroachDB *removes* a class of multi-agent memory bugs rather than adding one. |

## ⚠️ Charter challenge

None. The charter's §4 wedge and §6 co-location contract are exactly what make the one-transaction tool possible; this doc operationalizes them without divergence. One clarification requested from `01`/`04`: confirm the cosine opclass + C-SPANN availability on the target cluster version (charter §7 already flags this).
