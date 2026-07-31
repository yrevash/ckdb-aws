# 01 — What is Postmortem (and why every choice was made)

## The problem, in human terms

When something breaks in production at 3am, an **on-call engineer** (SRE = Site Reliability Engineer)
gets paged. Increasingly, an **AI agent** does the first response. But today's on-call agents have a
fatal flaw: **they start every incident from zero.** They don't remember that this exact failure
happened three months ago, or what actually fixed it. So teams re-solve the same outages again and
again, and **MTTR** (Mean Time To Recovery — the key metric every engineering org watches) stays high.

The knowledge exists — in old incident tickets, in runbooks, in senior engineers' heads — but it's
scattered and the agent can't reach it mid-incident. **Postmortem fixes that by making memory the thing
that drives the fix.**

## What Postmortem is

An **on-call SRE agent with persistent, self-improving memory in CockroachDB.** Concretely, it:

1. **Perceives** an alert (e.g. "checkout latency spiked after deploy #5120").
2. **Recalls** similar past incidents from memory ("this is a 0.94 match to CASE-1878 from March").
3. **Reasons** using a large language model (via AWS Bedrock).
4. **Acts** on the live system to remediate (e.g. roll back the bad deploy) — with human approval for
   risky actions.
5. **Records** what it did and the outcome — *in the same database transaction as the action.*
6. **Consolidates** overnight: a background job distills raw incidents into clean, reusable runbooks,
   so the agent gets smarter while it "sleeps."

The scenario we chose: a **cloud-native microservices SaaS platform with a checkout/payments critical
path**, whose operational data (services, deploys, incidents, orders) lives in CockroachDB. So the
agent's *memory* and the *operational data it acts on* live in the same database.

## The "wedge" — why CockroachDB, and the whole point

A hackathon sponsored by CockroachDB wants projects that prove CockroachDB is the best home for agentic
memory. But the trap is building something where the database is *replaceable* ("this would work the
same on Postgres + Pinecone"). To avoid that, we built the app around the three things **only
CockroachDB does well** — we call this the **wedge**:

1. **Single store.** The agent's memory (embeddings + facts) and the operational data it acts on live
   in **one database**, so a memory write and the real-world action commit in **one atomic
   transaction**. They can *never* disagree. (A separate vector DB like Pinecone can't do this — its
   embeddings drift out of sync with your real data.)
2. **Read-your-own-writes at global scale.** A memory the agent just wrote is *instantly* recallable by
   any agent in any region — no lag. (This kills the classic "I saved a memory and can't find it" bug
   that eventually-consistent stores suffer.)
3. **RPO=0 region survival.** You can **kill an entire cloud region** and the memory + agent keep
   working with **zero data loss**. (RPO = Recovery Point Objective; RPO=0 means zero committed data
   lost.)

The beautiful part: the agent's memory lives on the same never-down substrate, so **when a region fails
— the very kind of incident it's fighting — its memory survives the outage.**

## What we deliberately do NOT compete on (and why it matters)

Being honest about weaknesses made the design sharper:

- **Not raw vector-search speed.** Specialized vector DBs (Pinecone, Milvus) are faster at pure
  nearest-neighbor search. We don't race them; we win on *consistency + survivability*.
- **Not "memory-only convenience."** AWS has a managed agent-memory service (Bedrock AgentCore Memory).
  For a *chatbot* that only needs conversation memory, it's the easy choice and CockroachDB would be
  over-engineering. **So we made the agent *act* on real operational data** — the moment you need that,
  AgentCore Memory (which has no SQL, no transactions) can't be the store, and CockroachDB is necessary.

That single decision — *the agent acts on real data* — is what makes CockroachDB the right answer
instead of a nice-to-have. Every other choice flows from it.

## The hackathon strategy (the "why" behind the polish)

The sponsor's real goal isn't to acquire a product — it's **go-to-market proof** that developers can
build production-grade agentic memory on CockroachDB. So we optimized to be *their favorite case study*:

- **Real-world impact** (the criterion you chose to lead with): SRE toil / MTTR is a pain every
  engineering org recognizes and pays for.
- **Production readiness**: the live region-failover proof, the audit trail, and the enterprise
  security layer (see file 06) are exactly what an enterprise buyer watching the demo wants to see.
- **Memory design + creativity**: we use all three memory types including **procedural** (learned
  runbooks — the under-built frontier) and **sleep-time consolidation** (a 2025-frontier idea).
- **Technical implementation**: we use **all four** CockroachDB agent tools (vector index, MCP, ccloud,
  Agent Skills) and multiple AWS services, correctly and safely.

The deeper strategy write-ups are in `research/postmortem/00-charter.md` and
`research/deep-dive/08-synthesis-where-cockroachdb-wins.md`.
