# 10 — What we're still missing, technically

This is the honest "engineering maturity" gap list — the things that separate **"verified locally"**
from **"battle-tested in production."** It's distinct from file 09 (which is deploy + submission
logistics); this is about robustness of the system itself. Written for you to know exactly where the
edges are.

## A. Real integration is still unproven (the biggest unknown)
- **Bedrock and MCP are *faked* locally.** The real model calls (streaming, token limits, cost,
  throttling/429s) and the real Managed MCP + `langchain-cockroachdb` path have **never actually run.**
  This is the #1 technical risk — everything downstream of "the agent reasons" is untested against the
  real services. (Resolves on the AWS deploy — not yet performed.)
- Because of that, the **agent's decision quality** (does memory actually make it resolve incidents
  better?) is **not yet measured** — see file 05. It needs the real agent.

## B. Reliability under failure (partly being fixed in the audit pass)
- **Transaction retries:** the write path retries CockroachDB serialization errors (40001); the audit
  found the **recall read path did not** — fixed in the 2026-08-02 audit pass.
- **Idempotency:** a duplicate remediation used to error instead of replaying — fixed in audit batch 1.
- **Graceful degradation:** what happens when Bedrock is down, MCP is down, or a tool fails mid-action?
  There are no **circuit breakers / fallbacks** yet.
- **Runaway-agent protection:** the loop is single-turn by construction, but there's no explicit
  **max-actions-per-incident** cap.
- **Connection pooling** is sized for the demo, not load-tested for Fargate + Lambda concurrency.

## C. Observability (thin)
- Security alarms exist (CloudTrail → CloudWatch), but there's **no app-level tracing** (OpenTelemetry /
  X-Ray) of the agent's decisions and latency, **no structured decision log** to a dashboard, and **no
  cost metering** of Bedrock tokens. In production you'd want to *see* why the agent did what it did.

## D. Scale & performance (unmeasured)
- **Zero QPS / latency-at-scale numbers** for C-SPANN recall. The index params (beam size, partition
  sizes) are tuned for the demo corpus, not validated at millions of vectors. We honestly cannot claim
  "fast at scale" yet — and per the Reality Charter, we don't.

## E. Testing & CI (missing the automation)
- Unit + integration tests pass and the three verifiers are green, but there is **no CI pipeline**
  (GitHub Actions running the verifiers on every PR), **no load/chaos tests**, **no coverage
  measurement**, and **no end-to-end test against real AWS.** Today the verifiers are run by hand.
- A **container-image vulnerability-scan gate** is planned, not wired (flagged in the security review).

## F. Data lifecycle & cost controls
- TTL/decay exists, but **memory-growth management**, **embedding re-generation when the model
  changes**, and vacuuming aren't built.
- **The real quality of Bedrock-distilled runbooks** (hallucination, dedup correctness) is unvalidated —
  the consolidation logic is tested, but its *output quality* needs the real model + human spot-checks.
- **Rate limiting** beyond the WAF, and **secrets rotation** actually wired (currently "where
  possible"), remain.

## G. Security deploy-time items (from `docs/security/`)
- Real secret values, live PrivateLink to CockroachDB, GuardDuty/Config account enablement, **CSP
  nonces** (tighten from `unsafe-inline`), **CIS 6.4** idle-session timeout, and scheduled S3 backups
  (PITR is proven on `nodelocal://`, not yet on a schedule to S3), the CockroachDB-Cloud-tier
  constraint on PrivateLink (the CDK now offers an opt-in NAT egress mode instead), and managed tiers
  that refuse `SET CLUSTER SETTING` during schema apply.

## The honest one-liner
The **core is real and proven locally**; the gaps are **real-integration (pending the AWS deploy), production
hardening (retries/observability/CI), and scale numbers we haven't earned yet.** None are unknowns that
threaten the design — they're the normal distance between a strong hackathon build and a shipped
product. We track them here rather than pretend they're done.
