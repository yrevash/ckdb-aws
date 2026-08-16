# 11 — The audit & the "only real" reset

This file records what a full code audit found, what we fixed, and the decision to make **every number
real** — so you understand why the headline metrics changed and can trust what's left.

## Why we did this
The build moved fast (a lot via agents). Before trusting it, we ran a **6-way read-only code audit**
(backend, security, database, infra, data/eval, frontend) — auditors whose job was to *break* the code
and find where claims didn't match reality. Then you made the call: **no fakeness anywhere, only real.**

## What the audit found (the important part)
The **engineering was real** — the atomic wedge, the schema, the guardrails, least-privilege IAM, no
SQL injection, no secrets — all verified. But two categories of problem surfaced:

**1. Real bugs the tests missed** (many because the *fake* runtime hid them):
- **Memory poisoning:** a *failed* incident could create a "success" runbook and deprecate the good one.
- **Non-idempotent remediation:** a retried request errored instead of replaying.
- **`semantic_current` not unique:** two facts could both be "currently valid" at once.
- **Runbook status not checked:** a draft/deprecated runbook could justify a live action.
- **No retry on the recall path; unguarded model-output parsing → HTTP 500; infra errors mislabeled as
  409; unbounded event history; no Bedrock timeouts.**
- **Security:** the human-approval gate trusted a **model-controlled flag** for what counts as
  destructive; recalled memory text wasn't injection-screened; the "non-bypassable" provenance wrapper
  was dead code; role-scoping was cosmetic under a single DB connection string.
- **Infra:** a CloudTrail-key grant that would **fail at deploy**; a PrivateLink security group with no
  ingress (the DB path was structurally dead).

**2. The metrics were rigged in our favor** — this is the one that mattered most:
- The "memory" the agent recalled **was literally the answer key.**
- The "no-memory" baseline was **hard-coded to make a dumb first move.**
- The "it learns and speeds up" curve was a **hard-coded constant.**
- The "survived a region dying" test **never killed the important node**, so the 0.009s "recovery" was
  just a normal write.

So the impressive numbers (−63.6% MTTR, recall@10 = 1.0, RTO 0.009s) were **baked in, not measured.**

## The "only real" reset (what we did about it)
We wrote a **Reality Charter** (`docs/reality/00-reality-charter.md`): no published number unless a real
reproducible run produced it; no rigged baselines, answer-key leakage, hardcoded outcomes, or staged
UI; and **claims that need the real model wait for the real model.** Then we made it true:

| Area | Fake before | Real now |
|------|-------------|----------|
| Retrieval recall@1 | 1.0 (answer-key) | **0.85** — with 9 hard negatives (a close-but-wrong prior case can outrank the right one; that's real) |
| Retrieval nDCG@10 | — | **0.94** (measured) |
| Cold baseline | hard-coded to fail | a **competent** memoryless baseline (it *ties* on the toy sim — the honest result) |
| Learning curve | hard-coded tuple | removed (no fake trend) |
| RTO under region kill | 0.009s (no real failover) | **3.1–4.9s** — leaseholders pinned into the *killed* region, real lease handoff, probe fails if no failover |
| RPO | 0 (soft) | **0, content-verified *during* the outage** |
| MTTR / wrong-actions / orders | −63.6% / 20→0 / 40 saved | **pending real-agent run** — not claimed until the real Bedrock agent runs |
| UI telemetry | hard-coded literals | derived from real events; absent data shows `—` |

Every surviving number is **tagged with the script that produced it.** The rigged numbers are **gone**
from the README, the learning docs, and the demo materials.

## Why this makes the project *stronger*, not weaker
A number a judge can debunk is worse than no number. By showing **smaller, true** numbers plus an honest
"MTTR pending the real agent," we're defensible under scrutiny — which is exactly what the "Production
Readiness" and integrity-minded judges reward. The engineering is real; now the claims match it exactly.

## Where the fixes live
- Reality framework: `docs/reality/00-reality-charter.md`
- Audit-driven code fixes: commits tagged "Audit fixes" and "Reality Charter" on `main`
- Full security posture (separate, also real): `docs/security/`
- What's still not real *yet* (needs the real agent/AWS): file `09` (deploy) + file `10` (tech gaps)

## The rule going forward
**If it isn't real, it isn't published — it's labeled "pending."** That's how we actually learn whether
Postmortem works.
