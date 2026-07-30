# Postmortem backend — Phase 1 + Phase 2 recall

This package is the responder/backend spine from the original Postmortem master plan. It keeps the
load-bearing boundaries explicit:

- Amazon Bedrock/Strands performs reasoning.
- Titan Text Embeddings V2 emits normalized, 1024-dimensional vectors.
- CockroachDB Managed MCP is the read-only recall surface.
- Direct CockroachDB SQL can run the same three-stage recall for local/evaluation proof.
- Direct CockroachDB SQL owns `remediate_and_record`, because only a SQL transaction can atomically
  mutate operational state and append the episodic memory.
- FastAPI exposes health, incident response, incident detail, and SSE event-stream endpoints.

The default `fake` runtime needs no cloud credentials. It contains one seeded prior rollback memory
and an auto-seeded in-memory operational target, so the example request completes the full Phase 1
flow. It follows the same ports used by the AWS/CockroachDB adapters.

## Local setup

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test,strands]'
cp .env.example .env
postmortem-backend
```

The service listens on `http://localhost:8080`. Important endpoints:

- `GET /healthz`
- `POST /v1/incidents/{incident_id}/respond`
- `POST /v1/incidents/{incident_id}/outcomes`
- `GET /v1/incidents/{incident_id}`
- `GET /v1/incidents/{incident_id}/events` (Server-Sent Events)

Example request:

```bash
curl -X POST http://localhost:8080/v1/incidents/10000000-0000-0000-0000-000000000001/respond \
  -H 'content-type: application/json' \
  -d '{
    "session_id": "20000000-0000-0000-0000-000000000001",
    "service_id": "30000000-0000-0000-0000-000000000001",
    "severity": "SEV-1",
    "summary": "checkout 5xx spike immediately after canary deploy",
    "error_signature": "HTTP_5XX_POST_DEPLOY"
  }'
```

## Deployed configuration

Set `POSTMORTEM_RUNTIME_MODE=aws`, provide the database/MCP settings in `.env.example`, and set
`POSTMORTEM_REASONER=bedrock`. In Fargate, inject the CockroachDB and MCP values from Secrets Manager
rather than checking them into task definitions. AWS credentials use the task role.

`POSTMORTEM_REASONER=strands` selects the optional Strands adapter. Install `.[strands]`; its import is
lazy so the core and tests remain usable without the SDK.

Phase 2 recall uses C-SPANN candidate fetches followed by structured temporal/scope/provenance
filtering and deterministic reranking. `POSTMORTEM_RECALL_BACKEND=mcp` keeps Managed MCP as the
production read surface; `sql` selects leaseholder reads through the warm CockroachDB pool.
`POSTMORTEM_COLD_START=true` bypasses all memory reads for the controlled MTTR comparison. Individual
incident requests may also set `"cold_start": true`.

After the simulator verifies a remediation, it records the result with:

```json
{
  "action_id": "the remediation action UUID",
  "service_id": "the affected service UUID",
  "outcome": "success",
  "summary": "Checkout error rate returned to baseline.",
  "error_signature": "HTTP_5XX_POST_DEPLOY"
}
```

The outcome endpoint validates that the action belongs to the same organization, incident, and
service. It updates the action result, resolves the incident on success, and appends an
`event_type='outcome'` episode carrying `consolidation_ready=true` in one transaction. Replaying the
same outcome returns the original event; attempting to replace it with a contradictory result returns
HTTP 409.

## Transaction guarantee

`CockroachAtomicRemediationStore` sends one CTE statement inside an explicit SERIALIZABLE transaction.
The statement:

1. validates the cited episodic memory or runbook;
2. inserts the operational deploy action;
3. updates the service and incident;
4. appends the episodic action memory;
5. inserts `remediation_actions` with a distinct action ID and transaction ID;
6. updates runbook usage when the citation is a runbook.

The result row exists only when the incident, service, and provenance gates all pass. Any exception
leaves the transaction uncommitted; CockroachDB rolls the whole statement back. Serialization
failures (`40001`) retry the complete transaction with bounded exponential backoff.

## Tests

Run the backend suite:

```bash
.venv/bin/pytest -q
```

It covers the responder flow, typed event stream, safety/provenance gates, successful atomic commit,
and failures injected after operational mutation and during memory append to prove both-or-neither
rollback behavior. Set `POSTMORTEM_TEST_DATABASE_URL` to include the opt-in transaction proof against
a live CockroachDB node; the repository-level `scripts/verify_phase1.sh` does this automatically.
