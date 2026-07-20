# Research — CockroachDB × AWS Hackathon (Agentic Memory)

This folder is the research + planning base for our hackathon submission.
It is deliberately split into parts so any teammate can read one file and be productive.

**Read in this order:**

| # | File | What it answers |
|---|------|-----------------|
| 00 | [`00-context-and-strategy.md`](./00-context-and-strategy.md) | The hackathon rules, deadline, prizes, judging, and *our* team profile + win strategy. |
| 01 | [`01-problem-space.md`](./01-problem-space.md) | What the real problem is. Why "agentic memory" matters now, what's broken today. |
| 02 | [`02-core-concepts.md`](./02-core-concepts.md) | The SOTA vocabulary: memory types, temporal knowledge graphs, sleep-time compute, governance, benchmarks. |
| 03 | [`03-cockroachdb-capabilities.md`](./03-cockroachdb-capabilities.md) | Exactly what CockroachDB gives us (C-SPANN, MCP, ccloud, skills, multi-region, CDC). |
| 04 | [`04-aws-capabilities.md`](./04-aws-capabilities.md) | The AWS agentic stack we can pull from (Bedrock AgentCore, Lambda, etc.). |
| 05 | [`05-ideas-shortlist.md`](./05-ideas-shortlist.md) | **The payoff:** 3 SOTA, real-world-impact build ideas, scored against judging criteria. |
| 06 | [`06-open-questions.md`](./06-open-questions.md) | Decisions the team needs to make before we lock a design spec. |
| 07 | [`07-landscape-july-2026.md`](./07-landscape-july-2026.md) | Real-time July 2026 snapshot: MCP goes stateless, latest frameworks, current CRDB/AWS versions. |

**TL;DR of our situation** — small team (2–4), AI/ML-strong + polyglot, optimizing for
**real-world impact / usefulness**, leaning toward enterprise ops/support + dev/coding agents
and genuinely new impactful angles. Deadline **19 Aug 2026**.

**Next step after this folder:** pick one idea in `05`, then move it into a formal design
spec and implementation plan.
