# Postmortem

Postmortem is an on-call SRE agent with persistent, self-improving memory in CockroachDB, running on
AWS. It recalls proven incident fixes, acts on a controllable system-under-management, records the
action and its outcome atomically, and later consolidates raw incidents into reusable runbooks.

The implementation follows the reconciled design in
[`research/postmortem/07-master-plan.md`](research/postmortem/07-master-plan.md).

## Repository layout

| Path | Purpose |
|---|---|
| `backend/` | Python responder, tool adapters, HTTP/SSE API, and atomic action path |
| `db/` | CockroachDB migrations and database bootstrap |
| `simulator/` | Deterministic system-under-management and incident fixtures |
| `web/` | Next.js incident console |
| `infra/` | AWS CDK application |
| `research/` | Product, architecture, data, evaluation, and demo research |

## Phase 1 target

Phase 1 proves one vertical slice:

```text
fault/alert
  → recall a seeded incident
  → reason/propose
  → remediate_and_record (one CockroachDB transaction)
  → stream transaction and memory events to the console
```

The local development database is a single CockroachDB node. Multi-region `SURVIVE REGION FAILURE`
configuration and the real failover rehearsal are Phase 3 concerns; the schema and application
boundaries are designed for that upgrade.

## Local prerequisites

- Docker
- Python 3.12+
- Node.js 22+ and pnpm 10+

Copy `.env.example` to `.env`, then start CockroachDB:

```bash
docker compose up -d cockroach
docker compose run --rm db-migrate
```

Component-specific commands live in each component's README while Phase 1 is being assembled.

## Verify Phase 1

Install the backend, infrastructure, and web dependencies once, then run:

```bash
./scripts/verify_phase1.sh
```

The verifier starts the local CockroachDB node, applies the migrations idempotently, runs the live
serializable `remediate_and_record` proof, exercises the simulator-to-responder vertical slice, and
checks every backend, database, simulator, infrastructure, and web suite.

The backend's default `fake` runtime contains the single prior successful rollback memory required
by the Phase 1 milestone. A response request therefore produces the complete
perceive → recall → reason → act → record event sequence without AWS credentials. The production
runtime swaps those test doubles for Bedrock/Strands, Managed MCP recall, and direct CockroachDB SQL.

## License

MIT. See [`LICENSE`](LICENSE).
