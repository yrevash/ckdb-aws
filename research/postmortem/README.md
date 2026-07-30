# Postmortem — Design & Plan

**Postmortem** = an on-call SRE agent with persistent, self-improving memory in CockroachDB, on AWS.
It remembers every incident, recalls the proven fix when one recurs, **acts** on the live system, and
consolidates raw incidents into runbooks overnight — on a memory layer that survives a region outage
with zero data loss.

This folder is the matured design, produced by 6 specialized planning agents against a shared charter.

## Read order
1. ⭐ **[`07-master-plan.md`](./07-master-plan.md)** — the synthesis: locked decisions, resolved
   conflicts, unified architecture, 4-week schedule, risks, cost, and open team decisions. **Start here.**
2. [`00-charter.md`](./00-charter.md) — the foundation contract every plan obeys (problem, scope, wedge,
   architecture spine, success metrics).

## The six specialized plans
| Doc | Owns |
|-----|------|
| [`01-memory-architecture.md`](./01-memory-architecture.md) | Memory data model — episodic/semantic/**procedural** (3-part runbooks), bitemporal facts, C-SPANN, co-location wedge, recall design, TTL decay. |
| [`02-agent-orchestration.md`](./02-agent-orchestration.md) | The agent — reasoning loop, tool interface, the `remediate_and_record` one-txn wedge, **Strands framework choice**, sleep-time consolidator. |
| [`03-aws-infrastructure.md`](./03-aws-infrastructure.md) | AWS — **Fargate hosting**, Bedrock models, changefeed→SQS→Lambda, IAM/secrets, CDK, cost (~$60–160/mo). |
| [`04-cockroachdb-deployment-resilience.md`](./04-cockroachdb-deployment-resilience.md) | CockroachDB — multi-region topology, C-SPANN/MCP setup, **the 3-tier failover-demo plan**, prod-readiness. |
| [`05-data-and-evaluation.md`](./05-data-and-evaluation.md) | Data — the SUM simulator + fault conductor, seed corpus (recurrence + drift), 3-layer eval, metrics instrumentation. |
| [`06-demo-and-ux.md`](./06-demo-and-ux.md) | The web incident console (Recall Thread) + the 178-second demo storyboard. |

## The three things this build must prove (the wedge)
1. **Single store** — memory + operational data commit in one ACID transaction; they can never disagree.
2. **Read-your-own-writes at global scale** — a memory just written is instantly recalled, no lag.
3. **RPO=0 region survival** — kill a region live; memory + agent keep working, zero data loss.

## Status
Design complete. **Next:** confirm the open team decisions in `07` §8 (budget, ccloud-disruption
enrollment, account/Bedrock access), then take it into the `writing-plans` skill for the granular,
checkpointed implementation plan.
