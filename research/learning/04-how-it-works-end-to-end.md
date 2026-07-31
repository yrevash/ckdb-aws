# 04 — How it works, end to end (one incident's life)

Follow a single incident from alarm to "the agent got smarter." This is the story the demo tells, mapped
to the real code. (Values match the console screenshot: CASE-2041, a checkout latency spike.)

## Step 0 — the world exists
The **simulator** (`simulator/`) is running a mock platform: services (checkout-api, billing-worker…),
their deploys, SLOs, and a stream of customer orders. CockroachDB holds this operational data **and** the
agent's memory of past incidents, side by side.

## Step 1 — Perceive (an alert arrives)
The simulator injects a fault: deploy **#5120** (a canary) makes checkout slow. An alert fires:
> "p99 latency 4.2s · error rate 18.4% after deploy #5120."

The backend receives it. **Before the model sees it**, the alert text is treated as *untrusted* and run
through injection screening (`guardrails/injection.py`) and typed validation (`guardrails/validation.py`)
— so a malicious log line can't hijack the agent. (Security file 06.)

## Step 2 — Recall (memory does the heavy lifting)
The agent embeds the incident (Bedrock Titan → a 1024-dim vector) and asks memory for similar past
cases. This is **three-stage recall** (`backend/recall.py` + `adapters/recall.py`):
1. **Vector search** via C-SPANN, scoped to this org (a SQL query through the read-only **MCP** path).
2. **Filter** by service scope, tags, error signature, valid-time window, status, min track record.
3. **Rerank** by a blend of similarity + scope match + freshness + historical success.

Result: **CASE-1878 (from 14 March) is a 0.94 match.** Its runbook RB-207 says: "scaling increased
contention; rolling back the canary restored the SLO." The console draws the **Recall Thread** from the
live incident to CASE-1878 with the similarity dial and the component scores (`VECTOR 0.94 · SCOPE 1.00
· FRESH 0.82 · OUTCOME 0.90`). This is memory being *load-bearing* — it's about to change what the agent
does. The recall also surfaces a **semantic fact** ("checkout-api rollback requires approval") and 4
unsafe/stale candidates that got **rejected**.

## Step 3 — Reason (the model decides, safely)
The agent (AWS Strands, calling Bedrock Claude Sonnet 4.6) reasons over the incident + the recalled
evidence and produces a **typed decision**: `REMEDIATE`, action `ROLLBACK`, target `#5119`, **citing**
`ep-8842`. Crucially, the model returns a *typed enum*, never a free-text command — so the plan can only
be one of the allowlisted tools (`guardrails/allowlist.py`).

## Step 4 — Guardrails check (before anything touches the DB)
Three structural gates run:
- **Provenance gate** (`guardrails/provenance.py`): the action *must* cite a memory that was actually
  recalled this turn; an ungrounded/hallucinated action is refused.
- **Allowlist + destructive gate** (`guardrails/allowlist.py`): the tool must be allowlisted; because
  the payments critical path is involved, this action is flagged **"human approval required"** — a
  *named* human must approve, and the approval is recorded.
- **Role scope** (`guardrails/roles.py`): the write must run under the `postmortem_writer` identity, not
  the reader.

## Step 5 — Act + Record (the one-transaction wedge)
The approved action runs as `remediate_and_record` (`db/queries/rollback_and_record.sql`), a **single
CockroachDB transaction**:
```
BEGIN;
  UPDATE deploys       -- activate #5119, retire #5120  (the real fix)
  UPDATE incidents     -- status, runbook_id, mttr, resolved_at
  INSERT remediation_actions  -- rollback · outcome=pending_verification  (audit)
  INSERT episodic_events      -- the memory of the action · provenance=ep-8842
COMMIT;   -- txn=8f2ab471c90e ✓
```
The console shows this as the **Transaction Envelope** with the tagline *"same store · they can never
disagree."* The fix and the memory of the fix are now inseparable. Checkout recovers.

## Step 6 — the memory is instantly visible everywhere
The moment that commits, the new episodic memory (`ep-9217`) is recallable by **any** agent in **any
region** with **zero lag** — the console's memory timeline shows "recalled by responder-02 @eu-west · 0
stale reads." That's read-your-own-writes at global scale.

## Step 7 — Consolidate (overnight, the agent gets smarter)
When the incident resolves, a CockroachDB **changefeed** emits the change → API Gateway → a fast-ack
**Lambda** → **SQS** → the **consolidator Lambda** (`consolidation/`). It groups the full incident
history, and (only from this *successful*, provenance-bearing episode) **distills** or reinforces the
runbook and the semantic facts via Bedrock, writing them back with provenance. Next time this failure
shape appears, the agent's recall is even stronger — and the improvement **compounds** (the eval shows
MTTR dropping 300→180→120s across repeat occurrences).

## Step 8 — and if a whole region dies mid-incident?
Because the memory lives on CockroachDB with `SURVIVE REGION FAILURE`, you can **kill an entire region**
and steps 2–7 keep working — **zero data loss (RPO=0), automatic recovery in <10s.** The console's
Resilience tab shows the RPO counter *holding at 0* while node liveness ticks 9→6→9. This is the demo's
money-shot.

## The whole loop in one sentence
**Alert → recall the proven fix from CockroachDB → decide (typed, guardrailed) → act + remember in one
transaction → the memory is instantly global → consolidate it into a better runbook — all on memory that
survives a region outage.**
