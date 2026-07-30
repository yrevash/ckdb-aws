# Postmortem — Foundation Charter (v1)

**This is the single source of truth.** Every specialized planning doc (`01`–`06`) must obey this
charter and must not contradict it. If a planner believes the charter is wrong, they flag it in an
"⚠️ Charter challenge" section rather than silently diverging.

Grounding: see `../deep-dive/08-synthesis-where-cockroachdb-wins.md` (why we win),
`../deep-dive/cockroachdb/*` (capabilities), `../05-ideas-shortlist.md` (idea origin).

---

## 1. One-sentence product

**Postmortem** is an **on-call SRE agent with persistent, self-improving memory in CockroachDB**: it
remembers every past incident, recalls the proven fix when a similar one recurs, **acts** on the live
system to remediate, and consolidates raw incidents into reusable runbooks during downtime — all on a
memory layer that survives a full region outage with zero data loss.

## 2. The problem (real, quantifiable)

On-call AI agents start **cold** every incident. Institutional knowledge — past incidents, what
actually fixed them, service topology — is scattered and lost, so orgs **re-solve the same outages**
and **MTTR stays high**. Postmortem makes memory the thing that drives faster resolution.

## 3. Users & context

- **Primary user:** an on-call SRE / platform engineer at a company running a cloud-native SaaS
  platform on AWS.
- **The system under management (SUM):** a microservices SaaS platform with a **checkout/payments
  critical path** whose operational data (services, deploys, SLOs, incidents, orders/transactions)
  lives in **CockroachDB**. (For the hackathon the SUM is a controllable mock/simulator — see `05`.)
- **The agent** watches alerts, converses with the SRE in a web incident console, recalls memory,
  proposes + takes safe actions, and records outcomes.

## 4. The strategic wedge (do not violate)

From the synthesis: we win **only** on axes CockroachDB uniquely owns. The build MUST demonstrate:
1. **Single store** — the agent's memory (embeddings + facts) and the operational data it acts on live
   in **one ACID transaction**; they can never disagree.
2. **Read-your-own-writes at global scale** — a memory just written is immediately recalled by any
   agent/node, no lag.
3. **RPO=0 region survival** — kill a region live; memory + agent keep working, zero data loss.

We do **NOT** compete on: raw ANN speed (specialists win), or memory-only zero-ops convenience
(AWS Bedrock AgentCore Memory wins). Hence the agent **must act on real operational data** — that is
what makes AgentCore Memory insufficient and CockroachDB necessary.

## 5. Scope (Standard)

**In scope (v1 MVP core loop):**
- Perceive → **Recall** (vector + relational memory query) → Reason (LLM) → **Act** (remediation on the
  SUM, transactional with memory write) → **Record** (episodic write).
- **Sleep-time consolidation:** a background job distills raw episodes → semantic facts + procedural
  runbooks; triggered by CockroachDB changefeed.
- **Memory types:** episodic, semantic, procedural (the frontier), + minimal working/session state.
- **Bitemporal facts:** facts evolve as transitions, not overwrites.
- **Live region-failover demo** proving RPO=0.
- **Web incident console:** ChatOps-style conversation + live memory-timeline panel.

**Stretch (only if core is bulletproof):** multi-agent split (detector/responder/consolidator);
signed/audit provenance trail; multi-tenant `REGIONAL BY ROW` homing.

**Out of scope:** real production integrations (real PagerDuty/Slack/K8s); training custom models;
anything not serving the 3 wedge proofs.

## 6. Architecture spine (named components — planners refine within these boundaries)

```
[Alerts/Signals] → (A) Postmortem Agent ──(reason)── Amazon Bedrock (LLM + embeddings)
                        │  ▲
        (recall/act/record via SQL + MCP)
                        ▼  │
                 (M) CockroachDB  ── one store: memory (episodic/semantic/procedural, VECTOR+C-SPANN,
                        │            bitemporal, multi-scope) + operational data (services/deploys/
                        │            incidents/orders)
             (changefeed)│
                        ▼
        (C) Consolidation job (AWS Lambda) → distills episodes → writes facts/runbooks back
                        
        (U) Web incident console (ChatOps + memory timeline)  ── talks to (A)
        (S) System-under-management simulator  ── the agent's actions mutate its operational tables
        (X) AWS glue: S3 (raw postmortems/artifacts), EventBridge/changefeed sink, IAM, observability
```

**Ownership map (who plans what):**
| Component | Owner doc |
|-----------|-----------|
| (M) Memory schema, vector/bitemporal/scope design, SQL access patterns | `01-memory-architecture.md` |
| (A)+(C) Agent reasoning loop, tools, framework choice, sleep-time consolidation design | `02-agent-orchestration.md` |
| (X)+deployment, Bedrock, Lambda, S3, changefeed→Lambda wiring, IAM, hosting, observability | `03-aws-infrastructure.md` |
| CockroachDB cluster topology, C-SPANN + MCP setup, ccloud, the failover-demo mechanics, prod-readiness | `04-cockroachdb-deployment-resilience.md` |
| (S) Incident/runbook dataset + memory-quality evaluation + success metrics instrumentation | `05-data-and-evaluation.md` |
| (U) Console UX + the <3-min demo narrative/storyboard | `06-demo-and-ux.md` |

**Shared interface contracts (respect these across docs):**
- The **memory schema** (`01`) is the contract between the agent (`02`), consolidation (`02/03`), and
  the UI (`06`). Planners consume it; only `01` defines it.
- The **agent tool interface** (`02`) defines how the agent recalls/acts/records; `03` deploys it,
  `06` visualizes it.
- The **operational-data tables** are co-located with memory in CockroachDB so memory-write +
  action-write share one transaction (this is the wedge — do not split them across stores).

## 7. Tech baseline (defaults; owners may refine with justification)

- **Memory + operational store:** CockroachDB (multi-region, C-SPANN vectors). Non-negotiable.
- **CockroachDB tools used (target 4/4):** C-SPANN vector index + Managed MCP Server (headless
  service-account) as core; ccloud CLI (failover/scale demo) + Agent Skills (ops hardening) as bonus.
- **AWS services:** Amazon Bedrock (reasoning + embeddings) + AWS Lambda (consolidation) + S3; the
  hosting choice for the agent (AgentCore Runtime vs Lambda/ECS) is an **open decision owned by `03`**.
- **Agent framework:** OPEN decision owned by `02` — evaluate Claude Agent SDK vs AWS Strands (+
  AgentCore) vs LangGraph, recommend one. Must integrate cleanly with the CockroachDB memory backend.
- **Languages:** Python for agent + backend + consolidation; TypeScript/React for the web console.
  (Owners may adjust with reason.)
- **Embeddings:** normalize to unit length (CockroachDB vector index is metric-specific; cosine opclass
  exists from v25.3 — `01`/`04` to confirm on target cluster version).

## 8. Success metrics (instrument + show these — from synthesis §6)

| Metric | Target |
|--------|--------|
| Read-your-write staleness | 0 ms (immediately recallable after commit) |
| Region-failover data loss (RPO) | 0 rows |
| Region-failover recovery (RTO) | < 10 s, automatic |
| Memory-write + remediation atomicity | 1 ACID transaction |
| Vector recall quality | recall@k ≥ 95% |
| Cross-agent memory visibility | no lag |
| MTTR (simulated) with memory vs cold | show a clear before/after delta |

## 9. Demo thesis (the <3-min video must land, in order)

1. Memory is **load-bearing**: agent recalls a past incident and it changes the action it takes.
2. Memory + action are **one transaction** (show it).
3. **Kill a region live** → agent keeps remembering + acting, **0 data loss** (money shot).
4. (stretch) overnight **consolidation** turned raw incidents into a runbook the agent now uses.

## 10. Constraints

- Team: 2–4, polyglot, AI/ML-strong. Timeline: ~4 weeks (deadline 19 Aug 2026).
- Deliverables: public repo + MIT/Apache license + demo URL + <3-min video + written tool-usage +
  architecture diagram.
- Every build decision must map to a judging criterion (Memory Design / Technical Impl / Real-World
  Impact / Production Readiness / Creativity).

---

### What each planner must deliver
A rigorous, SOTA, clearly-defined plan for their slice: decisions **with rationale**, concrete
specifics (schemas/APIs/configs/sequence), how it satisfies the wedge + metrics, **risks + mitigations**,
an explicit **"decisions & recommendations"** section for any open choice they own, and an
**"interfaces I depend on / expose"** section. **No implementation code** — this is planning. Keep it
consistent with this charter.
