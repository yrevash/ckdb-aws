# Postmortem SUM simulator

This is the deterministic system-under-management (SUM) conductor. It
models a mutable checkout/payments path rather than replaying a chat transcript.
Faults change service, SLO, alert, dependency, configuration, deployment, and
order state; remediation actions mutate that state and a separate resolution
oracle decides whether the incident actually recovered.

## Implemented incident families

- `F1_BAD_DEPLOY`: a canary causes an error-rate breach; rollback to the prior
  stable version resolves it.
- `F2_POOL_EXHAUSTION`: latency and connection errors persist until the pool
  configuration is raised and the affected service is restarted.
- `F3_CACHE_OUTAGE`: a cache timeout cascade resolves after the checkout cache
  dependency is moved to its healthy replica.
- `F4_TRAFFIC_SATURATION`: scale capacity, then shed load.
- `F5_RETRY_STORM`: throttle amplified retries.
- `F6_MEMORY_LEAK`: restart the affected worker.
- `F7_CONFIG_REGRESSION`: revert the harmful feature flag.
- `F8_DATASTORE_FAILOVER`: move order writes to the healthy replica.
- `F9_PAYMENT_PROVIDER_OUTAGE`: select the secondary provider and throttle
  recovery traffic.
- `F10_NOVEL`: abstain and page a human.

Every known family has at least three deterministic occurrences in
`fixtures/scenarios.json`; the novel family has two abstention cases.
Canonical actions are
kept separately in `fixtures/oracles.json`, matching the evaluation plan's rule
that the answer key is defined before a run and withheld from the agent.
The F2 catalog includes a slow-query red herring with healthy connection-pool
evidence. Its scenario-specific oracle requires human escalation and rejects
the superficially similar pool-expansion procedure.

## Run

No third-party Python packages are required:

```sh
PYTHONPATH=simulator python3 -m postmortem_sim
python3 -m unittest discover -s simulator/tests -v
```

The module command injects all scheduled incidents and prints a SHA-256 of the
resulting deterministic snapshot. The tests exercise one incident at a time and
verify the closed loop.

## Integration contract

`Conductor.inject_next()` returns the alert-correlated incident visible to the
responder. `Conductor.apply_action()` accepts the action taxonomy from the
research plan and returns whether the action applied and whether the resolution
oracle closed the incident. A `memory_ref` can be carried on every action; the
database writer persists that reference atomically via `remediation_actions`.

The simulator currently holds its live state in memory to keep the Phase 1
conductor fast and deterministic. The database adapter that persists these
objects to the matching tables in `db/migrations/0002_core_schema.sql` belongs
to the backend integration slice. The simulator's public state uses the same
names and enum values as that schema.

## Determinism

- Fixed seed: `20260730`.
- Fixed UTC simulation clock.
- Stable UUIDv5 identifiers derived from `(seed, entity type, fixture key)`.
- No wall-clock calls, network access, or live model generation.
- Incorrect remediations add a deterministic five-minute diagnostic penalty
  and two additional failed orders, making MTTR and business impact measurable.

The Phase 2 objective A/B harness lives in `evaluation/`. Later phases still
expand the topology and history toward 40–60 services and 150–250 incidents,
add Bedrock-generated episode bodies, temporal drift, and database-backed
adapters.
