# 05 — Data & Evaluation (SUM simulator, seed corpus, memory-eval methodology)

> **Owner:** Data & Evaluation planner. **Obeys:** `00-charter.md` (single source of truth).
> **Consumes:** memory schema (`01`), agent tool interface (`02`), AWS wiring (`03`), cluster/failover
> mechanics (`04`). **Feeds:** operational tables into `01`, action semantics into `02`, metrics +
> scorecard into `06` (demo/UI).
>
> **This doc is a design, not code.** Schemas, examples, and pseudocode are illustrative. Where the
> charter leaves a choice open, this doc makes a **recommendation with rationale**.
>
> **North star for everything below:** the charter's wedge (§4) says memory must be *load-bearing* and
> the agent must *act on real operational data in one ACID transaction with its memory write*. So our
> data design cannot be a chat transcript — it must be a **live, mutable system-under-management** whose
> state the agent changes. And our evaluation cannot be vibes — it must produce the **numbers** in the
> charter's success-metrics table (§8).

---

## 0. TL;DR (the six-bullet dataset strategy)

1. **Hybrid corpus, not pure-synthetic.** Ground scenario *shapes* in real public postmortems
   (danluu/post-mortems, Awesome Tech Postmortems, VOID-style incident reports) + real SRE runbook
   templates, then use an **LLM generator with a fixed schema** to mass-produce timestamped incident
   episodes. Real anchors keep it believable; templated generation gives us **controlled recurrence**.
2. **A live System-Under-Management (SUM) simulator**, not a transcript. Services, dependencies,
   deploys, SLOs, and a checkout/orders transactional path live in CockroachDB *next to* memory. A
   **fault-injection engine** produces alerts; the **agent's remediation actions mutate SUM rows** — so
   "act" is real and shares a transaction with the memory write.
3. **Recurrence + variation are designed in, not incidental.** ~10 incident families, each with a base
   case and 3–6 variants (same root cause, different surface signals). This is what makes "we've seen
   this before" *demonstrable and measurable* rather than anecdotal.
4. **Temporal drift is a first-class scenario class.** For ≥2 families, a fix that worked at time *T*
   becomes *wrong* at *T+Δ* (topology changed, a dependency was deprecated). This is the bitemporal
   money-shot: the agent must retrieve the **currently-valid** fact, not the stale one it once learned.
5. **Everything is deterministic and seeded.** A fixed RNG seed + a scripted incident timeline means
   the demo is reproducible and the with-memory vs cold-start comparison is a **controlled A/B on the
   identical incident stream** — the only variable is whether memory is enabled.
6. **A small, custom, adjudicated eval set** (SRE-domain adaptation of LongMemEval/LoCoMo/BEAM):
   ~60–80 memory-recall questions + ~20 end-to-end incident-resolution tasks, scored with an LLM judge
   validated against a human-labeled gold subset. We compete on **consistency, temporal correctness,
   and outcome (MTTR) deltas** — not raw ANN QPS (the charter forbids that fight, §4).

Key metrics to prove memory works: **recall@k ≥95% / precision@k**, **temporal-validity accuracy**,
**read-your-write staleness = 0 ms**, **cross-agent visibility lag = 0**, **memory+action atomicity =
1 txn**, and the headline **MTTR(with-memory) vs MTTR(cold-start)** delta on an identical scenario
stream.

---

## 1. The System-Under-Management (SUM) simulator

The SUM is a **controllable mock of a cloud-native SaaS platform** with a checkout/payments critical
path. It is *not* a screen the agent reads — it is a set of CockroachDB tables the agent **queries and
mutates**, co-located with memory so a remediation write and a memory write commit in one transaction.

### 1.1 Design principles

- **Co-located with memory.** SUM operational tables and memory tables live in the **same CockroachDB
  database/cluster** (charter §6: "operational-data tables are co-located with memory … so memory-write
  + action-write share one transaction"). This is the wedge; do not split stores.
- **Mutable and stateful.** Every entity has live state (`healthy`/`degraded`/`down`, current deployed
  version, current config values). Faults change state; remediations change it back (or forward).
- **Deterministic clock.** The sim runs on a **simulated clock** (`sim_time`) so a "4-week history" of
  incidents can be generated in seconds and replayed identically. Real wall-clock is used only for the
  live failover demo. Bitemporal columns record both `sim_time` (valid-time) and `tx_time`
  (transaction-time — when the agent learned it).
- **Closed-loop.** Alert → agent recall/act → SUM state change → SLO recomputation → alert
  clears/escalates. The loop must actually close so MTTR is a real measured quantity, not a label.

### 1.2 Entity model (feeds doc `01`'s operational tables)

> These are the **operational** tables. Memory tables (episodic/semantic/procedural/bitemporal facts)
> are owned by `01`; this doc only specifies the operational side and the join keys memory references
> (`service_id`, `incident_id`, `deploy_id`).

| Entity | Key fields | Purpose |
|--------|-----------|---------|
| `service` | `service_id`, `name`, `tier` (critical/standard), `current_version`, `owner_team`, `runbook_id?`, `health` (healthy/degraded/down), `region` | The microservices. `tier=critical` marks the checkout path. |
| `service_dependency` | `from_service`, `to_service`, `dep_type` (sync/async/datastore), `criticality` | The topology DAG. Drives blast-radius reasoning + is what *drifts* over time. |
| `deploy` | `deploy_id`, `service_id`, `version`, `deployed_at` (sim_time), `change_summary`, `rolled_back` (bool), `deployer` | Deploy history. Many incidents correlate to a recent deploy → "deploy N of service X" is a recurring cause. |
| `slo` | `slo_id`, `service_id`, `kind` (latency/error_rate/availability/saturation), `threshold`, `window`, `current_value`, `burn_rate` | SLOs; breaching one *is* the alert trigger. Error-budget burn drives severity. |
| `metric_sample` | `service_id`, `metric`, `value`, `ts` (sim_time) | Thin time-series (only what alerts need — we do **not** build a full TSDB). Fault engine writes these. |
| `incident` | `incident_id`, `title`, `severity` (SEV1–4), `status` (open/mitigating/resolved), `family_id`, `variant_id`, `root_cause_service`, `opened_at`, `resolved_at`, `mttr_seconds` | The incident record. `family_id`/`variant_id` are the **ground-truth recurrence labels** used by eval (hidden from the agent). |
| `alert` | `alert_id`, `incident_id?`, `service_id`, `slo_id`, `signal`, `fired_at`, `cleared_at` | Raw signal the agent perceives. Correlated into incidents by the agent (or pre-correlated in v1). |
| `order` | `order_id`, `user_id`, `amount`, `status` (pending/authorized/captured/failed), `created_at` | **The checkout critical path.** Orders fail when the payment path is unhealthy → business-impact quantification ("$X of orders stuck"). |
| `remediation_action` | `action_id`, `incident_id`, `action_type`, `target_id`, `params (JSONB)`, `applied_at`, `applied_by` (agent/human), `outcome` (success/failed/no_effect), `memory_ref?` | **The audit log of what the agent did.** `memory_ref` FK links the action to the memory row that justified it → provenance + the atomicity proof. |
| `config_value` | `service_id`, `key`, `value`, `valid_from`, `valid_to` | Feature flags / connection-pool sizes / rate limits the agent can change. Bitemporal so "the right value" changes over time. |

**Ground-truth labels** (`family_id`, `variant_id`, `root_cause_service`, canonical fix) live in the
`incident`/scenario tables but are **withheld from the agent's context** — they are the answer key the
evaluator scores against.

### 1.3 The action set (feeds doc `02`'s agent tools)

Remediation must be *real writes* to SUM, transactional with the memory write. The action taxonomy
(the agent's "hands"):

| `action_type` | Effect on SUM | Typical family it fixes |
|---------------|---------------|-------------------------|
| `rollback_deploy` | Sets `service.current_version` to prior; marks `deploy.rolled_back=true`; service health recomputes | Bad-deploy regressions |
| `scale_service` | Changes a capacity attribute; relieves saturation SLO | Traffic-spike / saturation |
| `restart_service` | Clears transient degraded state | Memory-leak / stuck-process |
| `set_config` | Writes `config_value` (bumps pool size, toggles flag, changes timeout) | Connection-pool exhaustion, bad flag |
| `failover_dependency` | Repoints `service_dependency` to a healthy replica/region | Dependency/datastore outage |
| `throttle_traffic` | Sets a rate limit; sheds load to protect checkout | Thundering-herd / retry storm |
| `open_incident` / `escalate` / `resolve` | Lifecycle transitions on `incident` | Always |
| `no_op_page_human` | Records that the agent chose to escalate to a human (correct for novel/ambiguous cases) | Abstention (see §3) |

**The atomicity contract (the wedge, made concrete):** applying an action is one CockroachDB
transaction that (a) writes the `remediation_action` row, (b) mutates the target SUM row(s), and (c)
writes the episodic memory row + updates/creates the semantic/procedural memory — **all committed
together**. If the memory write fails, the action rolls back, and vice versa. This is the "two systems
can't disagree" proof and must be visible in the demo (show the `BEGIN … COMMIT`).

### 1.4 How alerts/incidents are generated (the fault-injection engine)

A scripted **incident timeline** (the "conductor") drives the sim:

1. **Scenario schedule.** A seeded list of `(sim_time, family_id, variant_id, target_service)` events.
   The conductor injects a fault at each: writes anomalous `metric_sample`s, flips `service.health`,
   fails `order`s on the checkout path, which trips an `slo` and fires an `alert`.
2. **Correlation → incident.** Alerts on connected services within a window roll up into one
   `incident` (v1 can pre-correlate to keep agent scope tight; correlation-as-agent-skill is stretch).
3. **Agent responds.** Perceive → Recall → Reason → Act → Record. Its action mutates SUM.
4. **Resolution check.** The conductor re-evaluates SLOs after the action. If the action matches the
   family's canonical fix (within tolerance), health recovers, `order` failures stop, the alert
   clears, `incident.status=resolved`, and `mttr_seconds` is stamped. If wrong, the incident persists
   / escalates (and burns error budget), which itself becomes a learnable episode.
5. **Recurrence.** The same family reappears later with a variant, so the agent gets a chance to
   *recall and reuse* — the core thing we are measuring.

### 1.5 Recommendation: fidelity level

**Recommend a "medium-fidelity, high-determinism" sim.** Not a real Kubernetes cluster (out of scope
per charter §5), not a toy of 3 rows. Target **~40–60 services**, a **realistic checkout dependency
subgraph** (LB → API gateway → checkout-svc → payment-svc → fraud-svc → orders-db → ledger), and a
**generated 4-week incident history (~150–250 incidents)** as the "past" the agent has memory of, plus
a **live demo timeline (~6–10 incidents)** run during the video. Rationale: enough breadth that recall
is non-trivial (distractors matter), small enough to reason about and reproduce deterministically.

---

## 2. The incident/runbook seed dataset

### 2.1 Sourcing strategy — hybrid (real anchors + LLM generation)

| Layer | Source | Role |
|-------|--------|------|
| **Scenario skeletons** | Real public postmortems: [danluu/post-mortems](https://github.com/danluu/post-mortems), Awesome Tech Postmortems, VOID / public cloud-provider RCAs | Give each *family* a believable root-cause→symptom→fix shape. We copy the *pattern*, not the text. |
| **Runbook templates** | Public SRE runbook templates (Google SRE book patterns, PagerDuty/incident.io runbook structures) | Seed the **procedural** memory format: trigger, diagnosis steps, remediation, verification, rollback. |
| **Episode bodies** | **LLM generator** (Bedrock model) with a strict JSON schema + the skeleton as a prompt anchor | Mass-produce timestamped alert logs, chat transcripts, and postmortem prose with controlled variation. |
| **Distractors** | LLM-generated unrelated incidents + benign deploys | The "noise" recall must survive (LongMemEval-style distractor sessions). |

**Why not pure real data?** Public postmortems are (a) too few, (b) inconsistently structured, (c)
lack the *controlled recurrence* and *temporal drift* we must demonstrate, and (d) don't connect to a
live mutable SUM. **Why not pure synthetic?** It drifts into unrealistic, self-similar text that a
judge (and a sponsor) sees through. Hybrid gets believability *and* control.

### 2.2 Generation pipeline (deterministic, seeded)

```
seed → scenario schedule (family, variant, target, sim_time)
     → for each incident:
         skeleton(family) + variation params
         → Bedrock LLM  → { alert_log[], sre_chat[], actions_taken[], postmortem_md, canonical_fix }
         → validate against JSON schema (retry on fail)
         → write: incident + alerts + metric_samples + (episodic seed rows for "past" incidents)
     → embed episodic/semantic text (Bedrock embeddings, unit-normalized) → C-SPANN
     → snapshot as a fixture (so the demo never depends on live LLM calls)
```

Two corpora come out: the **"past" corpus** (already in memory when the demo starts — this is what the
agent recalls) and the **"live" corpus** (injected during the demo). The past corpus is generated
once, embedded, and **snapshotted to S3** as a reproducible fixture (charter §6 lists S3 for raw
artifacts).

### 2.3 Recurrence & variation design (why memory pays off)

Every family has a **base case** and **variants** that share a root cause but differ on surface signal,
affected service, or blast radius. Example for family **F-POOL (connection-pool exhaustion):**

| Variant | Surface signal | Same root cause? | Same fix? |
|---------|---------------|-------------------|-----------|
| base | checkout-svc p99 latency spike, DB `too many connections` | yes | `set_config pool_size↑` + `restart` |
| v2 | *fraud-svc* timeouts, cascading to checkout | yes (fraud-svc pool) | same shape, different target |
| v3 | latency spike but **no** DB errors (red herring — actually a slow query) | **no** | different fix → tests that agent doesn't over-generalize |
| v4 | pool exhaustion after a traffic spike | yes + saturation | pool bump **and** scale |

Variant **v3 is deliberately a near-miss**: it *looks* like the family but isn't, testing
**precision** (does memory retrieval avoid a false "we've seen this" that leads to a wrong action?).
This directly maps to the precision@k metric in §3.

### 2.4 Temporal drift design (the bitemporal story)

For ≥2 families, the **correct fix changes over time** — the crux of bitemporal reasoning:

- **F-DRIFT-A (dependency deprecation).** At sim-week 1, checkout-svc's cache is `redis-legacy`; the
  proven fix for cache-outage incidents is `failover_dependency → redis-legacy-replica`. At sim-week 3,
  a migration repoints checkout-svc to `redis-cluster-v2` and `redis-legacy` is decommissioned. A
  cache-outage recurrence at week 4 must be fixed by failing over to the **new** cluster. **The old
  memory is now wrong.** The bitemporal fact `checkout-svc.cache = X` has `valid_to` set at the
  migration; the agent must retrieve the fact **valid at incident time**, not the most textually
  similar (older, higher-frequency) memory.
- **F-DRIFT-B (config regression).** A config value (e.g., a timeout) that resolved incidents early on
  becomes harmful after a dependency's latency profile changes; the once-good runbook step is now the
  *cause* of a new incident. Tests **knowledge-update** (LongMemEval category).

These give us the demo beat: *"the agent remembered a fix — and correctly knew it was stale, using the
currently-valid one instead."* No non-bitemporal store can do this cleanly; it's a CockroachDB
differentiator (charter §4, judging "Creativity").

---

## 3. Evaluation methodology — proving memory works, with numbers

We adapt three benchmark philosophies to the SRE domain:

- **LongMemEval** → its five abilities (info extraction, multi-session reasoning, **temporal
  reasoning**, **knowledge update**, **abstention**) become our memory-recall question categories.
- **LoCoMo** → long multi-session recall over a timeline of accumulated incidents.
- **BEAM** → long-horizon / staleness stress: recall a fix buried under many distractor incidents;
  detect the *staleness cliff* (the point where the right memory is drowned out).
- **DMR** → sanity floor for basic dialogue recall.

We run **two evaluation layers**: (L1) **memory-quality** (retrieval + reasoning, isolated) and (L2)
**task-outcome** (end-to-end MTTR on the live sim). Plus (L3) **systems properties** (consistency,
temporal, atomicity, failover) which are pass/fail, not scored.

### 3.1 L1 — Memory-quality eval (the custom SRE eval set)

**Construction.** ~60–80 questions, each = `(query, gold_memory_ids[], gold_answer, category)`.
`gold_memory_ids` are the specific episodic/semantic/procedural rows that *should* be retrieved
(known, because we generated them). Categories mirror LongMemEval:

| Category | SRE-domain question example | What it tests |
|----------|------------------------------|---------------|
| Single-incident recall | "What fixed the checkout latency incident on sim-day 4?" | Basic episodic retrieval |
| Multi-incident reasoning | "Across all pool-exhaustion incidents, which service is the recurring culprit?" | Cross-episode consolidation |
| **Temporal reasoning** | "As of sim-week 4, what is checkout-svc's cache backend?" | Right fact at right time (drift) |
| **Knowledge update** | "The runbook step for cache-outage — is the week-1 fix still correct?" | Superseded-fact handling |
| **Abstention** | "How do we fix a quantum-decoherence alert?" (never seen) | Says "no memory / escalate", doesn't hallucinate |
| Precision / near-miss | The F-POOL-v3 red herring | Doesn't false-positive "we've seen this" |

**Metrics (L1):**

| Metric | Definition | Target (charter §8) |
|--------|-----------|---------------------|
| **recall@k** | fraction of questions where ≥1 gold memory appears in top-k retrieved (k=5,10) | **≥95%** @k=10 |
| **precision@k** | fraction of top-k that are relevant (gold set) | report; watch near-miss family |
| **MRR / nDCG@k** | rank quality of the gold memory | report (rerank effectiveness) |
| **Temporal-validity accuracy** | % of temporal/knowledge-update questions where the agent uses the **currently-valid** fact | **≥90%** (headline for bitemporal) |
| **Abstention accuracy** | % of never-seen queries correctly declined (no hallucinated fix) | **≥95%** |
| **Answer correctness** | LLM-judge score vs gold_answer (0/1) | report by category |

**Retrieval tuning knob:** `vector_search_beam_size` (C-SPANN) is the recall/latency dial; we sweep it
and report the recall@k vs latency curve, choosing the smallest beam that hits ≥95% (per charter §8
note "tune vector_search_beam_size"). Reranking (a cross-encoder or LLM rerank over the top-N vector
hits) is applied before scoring precision — this is production-requirement #2 from `02-core-concepts`.

### 3.2 L2 — Task/outcome eval: simulated MTTR, with-memory vs cold-start

**This is the headline before/after delta.** The experiment is a **controlled A/B on the identical
seeded incident stream**:

- **Arm A (cold-start / no-memory):** memory recall disabled. The agent reasons from the current
  incident + generic runbooks only. (Represents "every incident starts cold" — the charter's problem
  statement §2.)
- **Arm B (with-memory):** full recall over the past corpus + consolidated runbooks.

Both arms face the **same** live incident timeline (same faults, same order, same seed). The only
independent variable is memory.

**How MTTR is measured in the sim (exact definition):**

```
MTTR(incident) = incident.resolved_at (sim_time) − incident.opened_at (sim_time)
```

where `resolved_at` is stamped by the **conductor's resolution check** (§1.4) — i.e., the moment the
agent's action actually returns SLOs to healthy and clears the alert. **Wrong actions cost time**: each
incorrect remediation adds a fixed "diagnostic penalty" of simulated minutes before the next attempt
(modeling a real failed mitigation). So MTTR captures *both* recall speed and action correctness.

**Reported outcome metrics (L2):**

| Metric | Definition |
|--------|-----------|
| **Median & p90 MTTR** per arm | headline delta (expect B ≪ A on recurring families) |
| **MTTR by recurrence rank** | MTTR on 1st vs 2nd vs 3rd occurrence of a family (B should drop sharply after 1st — the *learning curve*) |
| **First-action accuracy** | % incidents where the agent's *first* action is the canonical fix (memory should raise this) |
| **Actions-to-resolution** | count of remediation attempts (fewer = memory guided it) |
| **Escalation rate** | % incidents escalated to human (should fall for *known* families, stay high for novel — good, not bad) |
| **Business impact averted** | count/$ of `order`s that failed during the incident window (shorter MTTR ⇒ fewer failed checkouts) |

**The money chart for the demo:** MTTR vs occurrence-number, two lines (A flat, B decaying) — a visual
proof that *memory makes the agent get faster at things it has seen*. This is the single most
persuasive artifact for "Real-World Impact."

### 3.3 L3 — Systems-property eval (pass/fail, the CockroachDB wedge)

These aren't scored on a curve — they must simply **hold**, and we instrument them to show numbers:

| Property | Test | Pass condition (charter §8) |
|----------|------|------------------------------|
| **Read-your-own-write staleness** | Write a memory in txn; immediately `SELECT` it (same + different session/node) | Found, **0 ms** staleness, 100% of trials |
| **Cross-agent visibility** | Agent A writes memory; Agent B recalls it right after | Visible with **no lag**, 100% |
| **Memory+action atomicity** | Force a fault mid-transaction (kill after action write, before memory write) | Either **both** or **neither** persist; never one — verified over N fault-injection trials |
| **RPO=0 on region kill** | During a live incident, kill a region (`04`'s mechanism); count committed rows before vs after | **0 rows lost** |
| **RTO** | Time from region kill to agent resuming recall+act | **< 10 s**, automatic |

Atomicity is tested with a **chaos harness**: inject process/connection failures at each step boundary
of the write transaction and assert the both-or-neither invariant. This is the empirical backing for
the "two systems can't disagree" claim.

### 3.4 Scoring & judge validation (keeping it credible)

- **LLM-as-judge** (Bedrock model) scores answer correctness and canonical-fix matching, using the
  hidden gold labels. LongMemEval reports >97% judge/human agreement; we **validate our judge** against
  a **human-labeled gold subset** (~20 items scored by a teammate) and report the agreement rate. If
  agreement < ~90%, we tighten the rubric before trusting the judge on the full set.
- **Deterministic checks where possible.** First-action accuracy, recall@k, atomicity, RPO/RTO are
  *mechanical* (exact-match on IDs / row counts / timers) — no judge needed. We lean on these for the
  load-bearing claims and use the judge only for prose correctness.

---

## 4. Metrics instrumentation plan (populate the charter table + the UI)

**Principle:** every number in the charter's success table (§8) has an owning table/log so the demo UI
(`06`) can render it live, not from a slide.

| Charter metric | Instrumented from | Emitted to |
|----------------|-------------------|-----------|
| Read-your-write staleness | `eval_probe` timestamps (write commit ts → read hit ts) | UI "consistency" badge |
| RPO (rows lost) | row-count diff around region kill (`04` harness) | UI failover panel |
| RTO | timer: kill event → first successful post-kill recall | UI failover panel |
| Memory+action atomicity | `remediation_action.memory_ref` non-null + txn log | UI "one transaction" callout on each action |
| Vector recall@k | L1 eval harness output | Scorecard (§B) |
| Cross-agent visibility | dual-session probe log | Scorecard |
| MTTR with vs cold | `incident.mttr_seconds` grouped by arm | **The money chart** in UI |

**Structured event log (drives the memory-timeline panel in `06`):** every agent step emits a typed
event — `perceive`, `recall{query, hits[], scores[], beam_size, latency_ms}`,
`reason{decision, memory_refs[]}`, `act{action_type, target, txn_id}`, `record{memory_ids[]}`. Stored
in an `agent_event` table (also in CockroachDB). This log is the **single source** for: (a) the live
timeline UI, (b) the eval harness (recall hits come straight from `recall` events), and (c) the audit
trail (production-readiness + the stretch "signed provenance"). Production-requirement #6 from
`02-core-concepts` (structured error codes) is honored: failures emit typed codes, not opaque strings.

**Two dashboards for the demo:**
1. **Live incident console** (`06`) — per-incident: what was recalled, what action, the one-txn proof.
2. **Eval scorecard** (static, generated by the harness) — the numbers table + MTTR chart + recall@k
   curve. Generated reproducibly from the snapshotted fixture so judges can rerun it.

---

## 5. Honest note on eval limitations (hackathon credibility)

We will **state these plainly** in the writeup — being honest about them *is* the credibility move:

| Limitation | Why it exists | Mitigation (how we stay credible) |
|-----------|----------------|-----------------------------------|
| **Small N** (~150–250 past incidents, ~60–80 eval Qs) | 4-week hackathon, 2–4 people | Report **confidence intervals / effect sizes**, not just point estimates. Big MTTR deltas on recurring families are robust even at small N; we don't over-claim precision. |
| **Synthetic data** may flatter the agent | We generate both the world and the test | (a) Anchor families in **real** postmortems; (b) include **adversarial near-miss + abstention** cases that punish over-generalization; (c) hold out a **human-authored** mini-set (~10 incidents a teammate writes by hand) the generator never saw. |
| **Judge bias** (LLM judges its own domain) | LLM-as-judge is convenient | Validate against human labels; prefer **mechanical metrics** (ID match, row counts) for load-bearing claims. |
| **We control the resolution oracle** | The conductor decides "resolved" | The oracle checks **canonical fix ∈ family**, defined *before* the agent runs, in a separate file — no post-hoc goalpost moving. |
| **Not a real cluster / real MTTR-in-minutes** | Sim clock, out-of-scope real infra | Frame MTTR as **relative** (with vs cold on identical stream), never as an absolute industry number. The *delta* is the claim, not the absolute. |
| **Recall@95% is on our distribution** | Our corpus, our queries | Report the **recall vs beam-size curve** and the distractor ratio so the number is interpretable, not a bare "95%." |

**One-line honesty statement for the video/README:** *"This is a controlled simulation with synthetic
incidents anchored in real postmortems; our claims are about the **relative** effect of memory (with vs
without) on an identical incident stream and about **systems properties** (consistency, RPO=0,
atomicity) that are database facts, not model opinions."*

---

## A. Scenario catalog (incident families + recurrence design)

Ten families. Each: base + variants, a canonical fix (action_type), recurrence count in the past
corpus, and whether it carries temporal drift. `R` = number of occurrences seeded across the timeline.

| # | Family | Root cause | Surface signal | Canonical fix (action) | Variants | R | Drift? | Tests |
|---|--------|-----------|----------------|------------------------|----------|---|--------|-------|
| F1 | **Bad deploy regression** | New version introduces error/latency | error-rate SLO breach right after a `deploy` | `rollback_deploy` | 4 (diff services) | 6 | no | deploy-correlation recall |
| F2 | **Connection-pool exhaustion** | Pool too small under load | DB "too many connections", p99 spike | `set_config pool_size↑` (+`restart`) | 4 (incl. red-herring v3) | 7 | no | precision / near-miss |
| F3 | **Cache/dependency outage** | Cache backend down | timeouts cascading to checkout | `failover_dependency` | 3 | 5 | **YES (F-DRIFT-A)** | temporal validity |
| F4 | **Traffic-spike saturation** | CPU/mem saturation | saturation SLO, latency | `scale_service` (+`throttle`) | 3 | 5 | no | multi-action plans |
| F5 | **Retry storm / thundering herd** | Client retries amplify a blip | request-rate explosion, cascading | `throttle_traffic` | 2 | 4 | no | blast-radius reasoning |
| F6 | **Memory leak** | Slow leak → OOM | gradual mem climb, periodic crashes | `restart_service` (+ escalate for root fix) | 2 | 4 | no | partial-fix / recurrence |
| F7 | **Config/flag regression** | A flag/timeout value now harmful | errors after a config change | `set_config` (revert) | 2 | 4 | **YES (F-DRIFT-B)** | knowledge-update |
| F8 | **Datastore failover** | Primary DB/region unhealthy | writes failing, orders stuck | `failover_dependency` (region) | 2 | 3 | no | ties to RPO=0 demo |
| F9 | **Upstream 3rd-party (payments) outage** | External payment provider down | checkout auth failures, orders `failed` | `failover_dependency` (secondary provider) + `throttle` | 2 | 3 | no | business-impact ($ orders) |
| F10 | **Novel / unseen** | Genuinely new failure | ambiguous signal, no match | `no_op_page_human` (abstain) | — | 2 | n/a | **abstention** (don't hallucinate) |

**Recurrence design invariants:**
- Every non-novel family recurs **≥3 times** across the past timeline → memory has something to learn
  and the MTTR-by-occurrence learning curve is measurable.
- **First occurrence of each family in the *live* demo timeline is a recurrence** of something in the
  past corpus → the agent visibly recalls-and-reuses on camera.
- **F3 and F7 carry temporal drift** → at least two on-camera opportunities to show "the old fix is now
  wrong, here's the currently-valid one."
- **F2-v3 (red herring)** and **F10 (novel)** guard against over-generalization → protect precision and
  abstention scores from a memory that over-fires.
- **F8/F9** are the families used during the **live region-kill** beat (checkout path, real order
  impact) so the resilience demo and the business-impact story land together.

---

## B. Eval scorecard design (metric · how measured · target)

| # | Metric | Layer | How measured | Target |
|---|--------|-------|--------------|--------|
| 1 | **recall@10** | L1 | top-k contains ≥1 gold memory id (mechanical) | **≥95%** |
| 2 | precision@10 / nDCG@10 | L1 | relevant∩topk / rank quality vs gold | report; near-miss guarded |
| 3 | **Temporal-validity accuracy** | L1 | agent uses currently-valid fact on drift Qs (mechanical + judge) | **≥90%** |
| 4 | **Abstention accuracy** | L1 | novel Qs correctly declined | **≥95%** |
| 5 | Answer correctness | L1 | LLM-judge vs gold, judge validated to humans | report by category |
| 6 | **Median MTTR delta (B vs A)** | L2 | `resolved_at−opened_at`, same seeded stream | **clear, large delta** (headline) |
| 7 | **MTTR-by-occurrence curve** | L2 | MTTR vs Nth occurrence of family, per arm | B decays, A flat |
| 8 | First-action accuracy | L2 | first action == canonical fix | B ≫ A |
| 9 | Actions-to-resolution | L2 | count per incident | B < A |
| 10 | Business impact averted | L2 | failed `order`s in incident window | B < A |
| 11 | **Read-your-write staleness** | L3 | commit-ts → read-hit-ts | **0 ms**, 100% |
| 12 | **Cross-agent visibility lag** | L3 | dual-session probe | **0**, 100% |
| 13 | **Memory+action atomicity** | L3 | chaos harness, both-or-neither invariant | **1 txn**, 0 violations |
| 14 | **RPO** | L3 | row-count diff around region kill | **0 rows** |
| 15 | **RTO** | L3 | kill → first post-kill recall | **< 10 s** |

Rows 1, 6, 11–15 are the **charter §8 table**, fully covered. Rows 3–4, 7 are the differentiators that
make "memory works" a *proven* statement, not a claim. Every mechanical metric is reproducible from the
S3-snapshotted fixture + the `agent_event` log.

---

## C. Interfaces I expose / depend on

**I expose (other docs consume):**
- **SUM operational entity model (§1.2)** → `01` co-locates these tables with the memory schema; join
  keys `service_id`, `incident_id`, `deploy_id`, `memory_ref` are the contract.
- **Action taxonomy (§1.3)** → `02` implements these as agent tools; the atomicity contract (action +
  memory in one txn) is a hard requirement I place on `02`/`01`.
- **Ground-truth labels & resolution oracle (§1.4, §A)** → the answer key; withheld from agent, used by
  my eval harness.
- **Scenario catalog + seeded timeline (§A)** → `06` builds the demo narrative on these beats
  (recall-and-reuse, drift, region-kill on F8/F9).
- **`agent_event` log schema + metrics tables (§4)** → `06` renders the live timeline + scorecard from
  these; `03` ships the eval harness output to S3.
- **Metrics scorecard (§B)** → the numbers `06` displays and the writeup cites.

**I depend on:**
- **`01`** — memory schema (episodic/semantic/procedural/bitemporal facts), the `valid_time`/`tx_time`
  columns (my drift eval needs bitemporal facts), and that operational + memory tables share one DB.
- **`02`** — the recall/act/record tool interface and that each act commits with its memory write in
  one transaction; the `recall` event must expose retrieved ids + scores (my recall@k reads them).
- **`03`** — Bedrock (generation + embeddings + judge), Lambda (consolidation, so procedural runbooks
  exist to recall), S3 (fixture snapshots), and the changefeed that triggers consolidation.
- **`04`** — the region-kill mechanism and cluster topology (my RPO/RTO measurements hook into it);
  C-SPANN `vector_search_beam_size` exposed as a tunable (my recall-vs-beam sweep).

---

## D. Risks & mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|-----------|------------|
| **Synthetic data looks fake** → judges discount the eval | High | Med | Anchor families in real postmortems; hold out a human-authored mini-set; show adversarial near-miss + abstention cases. |
| **Sim resolution oracle is circular** (we grade our own homework) | High | Med | Canonical fixes defined **before** agent runs, in a separate file; lean on mechanical metrics; oracle logic reviewed by a non-eval teammate. |
| **recall@k tuned to hit 95% on a too-easy corpus** | Med | Med | Include heavy distractors (BEAM-style), report recall-vs-beam curve + distractor ratio, include the F2-v3 near-miss. |
| **MTTR delta not dramatic** (memory doesn't obviously help) | High | Low | Ensure ≥3 recurrences/family and a diagnostic penalty for wrong actions so recall speed *and* correctness both show; if delta is small, that itself is an honest finding to report. |
| **Generation is nondeterministic / demo not reproducible** | Med | Med | Fixed seeds; snapshot the past corpus + embeddings to S3; demo replays the fixture, never live-generates. |
| **Bitemporal drift eval is fiddly / breaks** | Med | Med | Keep drift to 2 families (F3, F7); test the "valid-at-incident-time" query in isolation before wiring to the agent. |
| **Judge disagrees with humans** → correctness numbers untrusted | Med | Low | Validate judge on a 20-item human-labeled set; require ≥~90% agreement or tighten rubric; prefer mechanical metrics for load-bearing claims. |
| **Scope creep** (sim grows into a full platform sim) | Med | Med | Cap at ~40–60 services, ~10 families, medium fidelity; anything more is stretch. Charter §5 keeps real integrations out of scope. |
| **Small N → noisy numbers** | Med | High | Report CIs/effect sizes; frame MTTR as relative delta; never cite absolute minutes as an industry figure. |

---

### ⚠️ Charter challenge

None. This doc fits the charter cleanly. One **note for `01`/`02`**: the atomicity contract (§1.3) and
the drift eval (§2.4) both *require* bitemporal fact modeling (`valid_time`/`tx_time`) and a
`memory_ref` FK from `remediation_action` to the memory row. If `01`/`02` don't expose those, metrics
#3 (temporal validity) and #13 (atomicity provenance) can't be measured — flagging early so the
interface is agreed up front.

---

## Sources

- [danluu/post-mortems — public postmortem collection](https://github.com/danluu/post-mortems)
- [LongMemEval (arXiv:2410.10813) — 5 abilities / 6 categories, judge >97% human agreement](https://arxiv.org/pdf/2410.10813)
- [LongMemEval overview (EmergentMind)](https://www.emergentmind.com/topics/longmemeval)
- [Mem0 — State of AI Agent Memory 2026 (LoCoMo/LongMemEval/BEAM landscape)](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [BEAM benchmark (ICLR 2026)](https://github.com/mohammadtavakoli78/BEAM)
- [Runbooks + RAG for an AI SRE agent (HackerNoon)](https://hackernoon.com/runbooks-rag-how-i-gave-my-ai-sre-agent-the-context-it-was-missing)
- [Multi-modal RAG LLMs for cloud instability diagnosis (arXiv:2505.21419)](https://arxiv.org/pdf/2505.21419)
- Internal: `00-charter.md`, `02-core-concepts.md`, `deep-dive/08-synthesis-where-cockroachdb-wins.md`, `deep-dive/competitors/02-agent-memory-frameworks.md`
