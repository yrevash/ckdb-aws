# 06 — Open Questions (decisions before we lock a spec)

Answer these and we can write a tight design spec. Grouped by urgency.

## A. Must answer to pick the idea

1. **Which idea?** A (SRE/Postmortem), B (MemGov governed shared memory), or C (Continuum memory API +
   customer success)? Or a blend? — *Recommendation in `05`: lead with A.*
2. **App vs infra taste:** does the team get more excited by shipping a *usable app* (A/C) or a piece
   of *reusable infrastructure* (B/C-SDK)? This breaks ties between the ideas.
3. **Demo centerpiece:** are we willing to do a **live region-failover** on camera (needs a real
   multi-region CockroachDB Cloud cluster + ccloud)? It's the strongest "never goes down" proof but
   adds setup cost.

## B. Accounts & access (blockers if missing)

4. **CockroachDB Cloud account** — do we have one, with the ability to create a cluster and generate
   the **Managed MCP** config + service-account keys? Multi-region needs a paid/serverless tier that
   supports it — confirm.
5. **AWS account** — do we have Bedrock model access (which region? which models — Claude via Bedrock?)
   and permission to run Lambda / (optionally) AgentCore?
6. **Any budget** for cloud spend during the ~4 weeks, or must we stay in free/credit tiers? (Affects
   whether we do true multi-region + AgentCore Runtime, or a leaner Lambda+single-region build.)

## C. Scope & stack choices

7. **Agent framework:** roll our own with the **Claude Agent SDK**, or use a pluggable-memory
   framework (**CrewAI 1.14.6**, **Strands + AgentCore**, LangGraph)? — affects how cleanly
   "CockroachDB = memory backend" reads. (See `07`.)
8. **Language:** Python (AI/ML strength) for the agent + memory service, TS for a thin UI? Or all-TS?
9. **Which memory types for v1?** All three (episodic/semantic/procedural) or start with
   episodic+semantic and add procedural if time allows? (Procedural = the creativity differentiator.)
10. **Do we build the sleep-time consolidation job for v1**, or stub it and add if time permits? It's a
    strong differentiator but not required for a working demo.

## D. Demo & data

11. **Dataset:** for A we need a believable incident/runbook corpus; for C a synthetic multi-year
    customer history; for B a multi-agent task scenario. Do we have/seed data, or generate synthetic?
12. **UI surface:** CLI + terminal recording, a minimal web dashboard, or lean on an existing agent
    client (Claude Code / Cursor talking to our MCP)? Impacts frontend effort.

## E. Things to verify technically (I can research these next)

13. Confirm **C-SPANN** GA status + exact `CREATE VECTOR INDEX` syntax and embedding-dimension/RaBitQ
    params on the **current managed-cloud version**.
14. Confirm the **Managed MCP write-consent** flow for programmatic (non-IDE) agents — can our runtime
    agent get scoped write access, or is it read-only + writes via a separate service account/driver?
15. Confirm **changefeed → Lambda** wiring on CockroachDB Cloud (webhook sink vs. Kafka/CDC options).

---

**Fastest path:** answer A-block (1–3) + B-block (4–6) now. I can research the E-block (13–15) in
parallel while we draft the spec.
