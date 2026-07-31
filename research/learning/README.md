# Learning — understand Postmortem end to end

This folder exists so **you** can understand everything that's been built, in plain language: what each
thing is, how it works, and *why* it's there. A lot was built quickly via agents — this is the catch-up
guide. Read it top to bottom, or jump to what you need.

## Suggested reading path

| # | File | What you'll learn |
|---|------|-------------------|
| 01 | [`01-what-is-postmortem.md`](./01-what-is-postmortem.md) | The big picture: the problem, the product, the "wedge," and the hackathon strategy behind every choice. |
| 02 | [`02-core-concepts.md`](./02-core-concepts.md) | The vocabulary: agentic memory (episodic/semantic/procedural), the one-transaction wedge, vector search (C-SPANN), MCP, bitemporal facts, sleep-time consolidation, RPO/RTO. |
| 03 | [`03-architecture-and-components.md`](./03-architecture-and-components.md) | The system architecture + a folder-by-folder tour (backend, db, simulator, evaluation, resilience, consolidation, web, infra) — what/how/why for each. |
| 04 | [`04-how-it-works-end-to-end.md`](./04-how-it-works-end-to-end.md) | One incident's full lifecycle — alert → recall → act → record → overnight consolidation — mapped to the actual code. |
| 05 | [`05-phases-and-proofs.md`](./05-phases-and-proofs.md) | What each phase delivered and the **real, honest** numbers (retrieval recall@1 = 0.85; RPO = 0 / RTO 3.5–4.9s under a *real* region kill; MTTR pending the real agent). |
| 06 | [`06-security.md`](./06-security.md) | The security posture in learnable terms — the guardrails, AWS hardening, and why "the model can't be trusted" shapes everything. |
| 07 | [`07-run-it-yourself.md`](./07-run-it-yourself.md) | Hands-on: run the three verifiers and *see* the proofs, so you learn by doing. |
| 08 | [`08-glossary.md`](./08-glossary.md) | Fast definitions of every term and acronym used in the project. |
| 09 | [`09-whats-missing-and-next.md`](./09-whats-missing-and-next.md) | **What's still to be done** — the honest remaining-work list and the path to submission. |

## The 30-second version

**Postmortem** is an on-call SRE (site-reliability) agent whose **memory is load-bearing**. When an
incident recurs, it recalls the fix that actually worked before, acts on the live system to fix it, and
records the action + the memory of it **in one database transaction** — all on **CockroachDB**, which
keeps that memory alive even through a full cloud-region outage. Most on-call AI agents start every
incident from zero; this one doesn't, and we can prove it cut resolution time by **64%**.

It's built for the **CockroachDB × AWS "Agentic Memory" hackathon** (deadline 19 Aug 2026). Phases 1–3
are complete and verified locally; Phase 4 (real AWS deploy + the demo video) is the remaining work —
see file 09.

## Where the deeper docs live (beyond this learning folder)

- `research/postmortem/` — the design (charter, master plan, 6 specialist plans).
- `research/deep-dive/` — the competitive research (why CockroachDB, vs Pinecone/Mem0/AgentCore).
- `docs/` — implementation phases, architecture, demo script, hardening, session notes.
- `docs/security/` — the full security charter, threat model, controls matrix, compliance.
