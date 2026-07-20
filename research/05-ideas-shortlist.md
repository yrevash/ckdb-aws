# 05 — Ideas Shortlist (3 SOTA build options)

Three distinct, real-world-impact ideas, each grounded in the 2026 research (`01`, `02`, `07`) and the
actual tool capabilities (`03`, `04`). Each names the CockroachDB tools (≥2) and AWS services (≥1) it
uses, the frontier concept it exploits, the demo moment, and honest risks.

**Our lens:** optimize **real-world impact/usefulness**; small team; polyglot; enterprise-ops +
dev-tools + new impactful angles. All three are demoable in < 3 min and put CockroachDB memory on the
critical path (not as a cache).

---

## Idea A — "Postmortem" · Self-improving SRE / on-call agent with persistent incident memory

**The pain (quantifiable):** on-call agents restart *cold* every incident. Institutional knowledge —
past incidents, runbooks, what actually fixed it — is scattered across Slack, PagerDuty, and people's
heads. MTTR stays high because the org re-solves the same incidents. This is real, expensive, and
universal.

**The build:** an agent that treats every incident as durable memory in CockroachDB:
- **Episodic memory:** each alert→investigation→resolution stored as a structured event.
- **Semantic memory:** distilled facts about services ("service X depends on queue Y").
- **Procedural memory** *(the 2026 frontier — underdeveloped in tooling)*: learned **runbooks** —
  "when p99 on checkout spikes + queue depth > N, the fix was to scale the workers."
- **Vector recall (C-SPANN):** embed logs/postmortems; on a new alert, semantically recall
  "we've seen this before" incidents.
- **Bi-temporal facts:** service topology changes over time; the agent reasons about *when* a fact was
  true, not just that it was.
- **Sleep-time consolidation (Lambda):** a background job turns noisy raw incidents into clean,
  deduplicated runbooks — the "agent dreams and gets smarter overnight" moment.

**CockroachDB tools:** C-SPANN vector index **+** Managed MCP (agent queries memory read-only, safe) +
(bonus) Agent Skills for DB ops. **AWS:** Bedrock (reasoning/embeddings) + Lambda (consolidation,
triggered by CockroachDB changefeed or CloudWatch alarm) + S3 (raw postmortems).

**Frontier concepts:** procedural memory + sleep-time compute + bi-temporal recall.

**Demo moment (killer):** fire an alert the agent has "seen" before → it instantly recalls the past
incident + proposes the proven runbook. Then optionally **kill a region live** (ccloud) and show the
memory + agent keep working — *"memory that never goes down."*

**Why it wins:** hits Real-World Impact hard (MTTR is a board-level metric), Production-Readiness
(it's literally an ops tool), and Creativity (procedural memory + consolidation are frontier). Very
demoable.

**Risks:** need a believable incident dataset/scenario; scope the number of services tightly.

---

## Idea B — "MemGov" · Governed shared-memory layer for enterprise multi-agent systems

**The pain:** enterprises are deploying *fleets* of agents, but there's **no shared, governed,
auditable memory**. Today inter-agent messages are **unsigned text in volatile memory, rewritable by
any admin** — yet the EU AI Act and NIST RMF demand **signed, tamper-proof provenance** for
autonomous decisions. This is a foundational infra gap the 2026 literature explicitly calls out.

**The build:** a **memory control plane** on CockroachDB that any agent fleet plugs into:
- **Multi-scope memory:** every write tagged `org_id / agent_id / user_id / session_id` with
  **scoped RBAC** access (via MCP + service accounts).
- **Signed, tamper-evident audit trail:** every memory read/write/decision is an append-only,
  hash-chained record — provenance you can hand a regulator. (CockroachDB audit logging + our own
  hash-chain in SQL.)
- **Bi-temporal, consistent store:** vector recall (C-SPANN) + relational facts + JSONB payloads in
  one ACID, multi-region DB — so shared memory is *consistent across agents and regions*, not
  eventually-consistent guesswork.
- **Geo-partitioning** for data residency (regional-by-row).

**CockroachDB tools:** Managed MCP (RBAC, read-only default, audit) **+** C-SPANN vector index **+**
(bonus) multi-region survivability + Agent Skills (`configuring-audit-logging`,
`hardening-user-privileges`). **AWS:** Bedrock AgentCore Runtime (isolated microVMs per agent) +
Policy/Guardrails + Lambda.

**Frontier concepts:** governed shared memory + provenance/auditability + multi-scope memory
(all directly from 2026 papers).

**Demo moment:** three agents collaborate on a task through shared memory; then show the **audit
trail + provenance graph** proving who wrote/used what — and demonstrate an admin *cannot* silently
rewrite history (hash-chain breaks).

**Why it wins:** strongest **Production-Readiness + Creativity** story; "governed shared memory =
foundational infra layer" is exactly where the field says 2026 is heading. Reusable beyond the demo.

**Risks:** more abstract/infra-y → the demo must make the value *visceral* (the tamper-evidence
moment does that). Slightly harder to make "cute."

---

## Idea C — "Continuum" · A never-down memory API + one killer vertical (long-horizon customer success)

**The pain / the thesis, taken literally:** the hackathon says *"agents need memory that never goes
down."* Today's memory-as-a-service options (Mem0 cloud, OpenMemory MCP) are **not** built on a
multi-region, always-on, strongly-consistent store. Build the **memory backend that literally can't
go down**, expose it over MCP, and prove it with a vertical that *needs* years of continuous memory.

**The build:**
- An **open-source memory SDK/API** (drop-in, MCP-exposed) backed entirely by CockroachDB: episodic +
  semantic + procedural stores, C-SPANN vector recall, bi-temporal facts, staleness/decay handling
  (facts *evolve* — a job change is a *transition*, not an overwrite; directly attacks the 2026
  "staleness" open problem), and sleep-time consolidation.
- **The vertical (new impactful area):** a **customer-success copilot** that remembers a customer's
  *entire multi-year relationship* — every ticket, call, contract change, sentiment shift — across
  regions and sessions, and surfaces "this account is at churn risk, here's the history" with full
  temporal context.

**CockroachDB tools:** C-SPANN + Managed MCP + multi-region survivability + (bonus) ccloud for the
live-failover demo. **AWS:** Bedrock (reasoning/embeddings) + Lambda (consolidation) + S3.

**Frontier concepts:** memory-as-infrastructure over distributed SQL + staleness-as-evolution +
sleep-time consolidation. Answers the hackathon thesis most literally.

**Demo moment:** during a live customer-success query, **kill the primary region** — the copilot keeps
recalling the full history with zero data loss. Plus show a fact *evolving* over time (transition, not
overwrite).

**Why it wins:** biggest vision, most reusable (infra + SDK others could adopt), and the failover demo
is the most literal proof of the hackathon's own thesis. New vertical = impact + originality.

**Risks:** largest scope (SDK **and** an app). For a small team, we'd need to keep the vertical thin
and the SDK focused, or it sprawls.

---

## Scorecard (against judging criteria)

| Idea | Memory Design | Technical Impl | Real-World Impact | Production-Readiness | Creativity | Team fit (2–4, 4 wks) |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| **A · Postmortem (SRE)** | ★★★★☆ | ★★★★☆ | ★★★★★ | ★★★★★ | ★★★★☆ | **Best** — tightest scope, most demoable |
| **B · MemGov (governed shared)** | ★★★★★ | ★★★★☆ | ★★★★☆ | ★★★★★ | ★★★★★ | Good — infra demo needs care |
| **C · Continuum (memory API + CS)** | ★★★★★ | ★★★★☆ | ★★★★☆ | ★★★★★ | ★★★★★ | Riskiest — largest scope |

## Recommendation

**Lead with Idea A (Postmortem / SRE memory), architected so its memory core is reusable (a nod to C),
and borrow B's audit-trail as a stretch feature.** Rationale: for a small team optimizing real-world
impact on a 4-week clock, A has the **tightest scope, the most visceral demo, and the clearest ROI
story** (MTTR), while still exploiting the *procedural-memory + sleep-time consolidation* frontier that
earns creativity points. If the team is more excited by infra than by an app, **B** is the higher-
ceiling, more-original play. **C** is the biggest swing — pick it only if we're confident on scope.

**Two things also work in our favor regardless of choice:** (1) the July-28 MCP "memory is the app's
job" framing (`07`) gives every idea a timely narrative, and (2) CockroachDB's single-store
vector+relational+temporal model means we avoid the Neo4j-plus-vector-DB complexity Zep/Mem0 carry.

> **Next step:** pick A / B / C (or a blend). Then we answer `06-open-questions.md` and move the
> winner into a formal design spec + implementation plan.
