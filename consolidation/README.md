# Postmortem consolidation

Phase 2 sleep-time pipeline:

```text
CockroachDB changefeed
  → API Gateway webhook
  → receiver Lambda (authenticate, type, fast-ack)
  → FIFO SQS
  → consolidator Lambda
      → S3 pending window
      → resolved watermark
      → group completed incident episodes
      → Bedrock-compatible distillation
      → idempotent CockroachDB runbook write
      → S3 prompt/response archive
```

The changefeed is at-least-once. FIFO deduplication handles short duplicate bursts; durable
idempotency is based on incident provenance, so replaying a closed window cannot increment runbook
counters twice. Successful recurrences reinforce an identical runbook. The first success creates a
draft; by default two further successful reinforcements promote it to active. Failed/no-effect
recurrences weaken it and deprecate it when counterexamples equal supporting successes. A changed
successful procedure creates a new draft version and deprecates the prior one.

Every candidate is embedded separately from distillation. Production uses normalized Titan Text
Embeddings V2 at exactly 1024 dimensions; local execution uses a stable normalized 1024-vector. A
runbook is never written without the embedding required by the C-SPANN recall path.

Incomplete incidents remain buffered in S3 until an `outcome` episode arrives in a later closed
window.

## Local verification

The local model and stores use no AWS credentials:

```bash
python -m pytest -q
```

## Deployed configuration

CDK configures the production boundaries. Relevant Lambda variables:

- `CHANGEFEED_WEBHOOK_SECRET_ARN`
- `CONSOLIDATION_QUEUE_URL`
- `CONSOLIDATION_DATABASE_SECRET_ARN`
- `ARTIFACTS_BUCKET`
- `CONSOLIDATION_MODEL_MODE=bedrock|deterministic`
- `CONSOLIDATION_MODEL_ID`
- `CONSOLIDATION_EMBEDDING_MODEL_ID`
- `RUNBOOK_REINFORCEMENTS_TO_ACTIVATE`
- optional Bedrock guardrail ID/version

The Lambda image packages `psycopg`; local tests deliberately do not require it. The database secret
may be a raw CockroachDB URL or a JSON object with `url`, `database_url`, or
`POSTMORTEM_DATABASE_URL`.

The CockroachDB external connection should POST webhook batches to the CDK output
`ChangefeedWebhookUrl` and send the secret as `X-Postmortem-Webhook-Secret` or a Bearer token.
