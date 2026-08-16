# 01 — AWS Infrastructure Security

Implements the "AWS infrastructure security" row of [`00-security-charter.md`](./00-security-charter.md)
in the CDK (`infra/`). Every control below is **synthesized and asserted** by the suites in `infra/tests/`
(run inside `scripts/verify_phase2.sh`) unless tagged **[deploy-time]**.

Status tags: **[IT]** implemented + synth-tested · **[DT]** deploy-time (needs the real account).

## Stacks

| Stack | Responsibility |
|-------|----------------|
| `SharedStack` | The CMK, VPC (private/isolated), PrivateLink endpoints, artifact bucket, Secrets, incl. the SQL reader/writer/consolidator DSN secrets, the Bedrock Guardrail |
| `AppStack` | Fargate responder + console, WAF, scoped IAM role |
| `ConsolidationStack` | Receiver/consolidator Lambdas, KMS+TLS SQS + DLQ, scoped roles |
| `SecurityStack` | Account detection plane: CloudTrail, GuardDuty, Config, security alarms |

## Identity & least privilege (R1, T2)

- **[IT] No wildcard actions.** `test_no_action_star_anywhere` walks every rendered IAM policy across
  all four stacks and fails if any `Allow` statement contains `Action: "*"`.
- **[IT] Bedrock is resource-scoped.** `bedrock:InvokeModel` is granted only on the specific model
  ARNs (Sonnet/Haiku/Titan), never `*`; `bedrock:ApplyGuardrail` only on the Guardrail ARN
  (`test_bedrock_invoke_is_resource_scoped`, `test_no_broad_bedrock_wildcard`).
- **[IT] One scoped role per compute unit** — the Fargate task, the receiver Lambda, and the
  consolidator Lambda each get their own role with only the grants they use (`grant_read` on exactly
  the one secret they need, `grant_send_messages` to exactly their queue, etc.).
- **[IT] Two SQL identities, injected as two separate secrets.** The task definition receives
  `POSTMORTEM_READER_DATABASE_URL` and `POSTMORTEM_WRITER_DATABASE_URL` from **different** Secrets
  Manager secrets, and the task role is granted `GetSecretValue` on exactly those. The backend fails
  closed (`RoleScopeViolation`) if the two DSNs turn out identical or same-principal, so R7 role
  separation cannot silently degrade to one connection string (audit C3/B1). Asserted by
  `RoleScopedDatabaseCredentialTests`.

## Data protection — encryption (R8)

- **[IT] One customer-managed KMS key** (rotation enabled) encrypts S3 artifacts, all Secrets, the SQS
  queues, and the CloudWatch log groups. CloudTrail gets its own dedicated CMK. (`EncryptionTests`.)
- **[IT] Secrets Manager for all credentials** — 5 secrets: the Managed-MCP reader **token**, three
  distinct CockroachDB **DSNs** (reader / writer / consolidator), and the changefeed webhook secret;
  every one CMK-encrypted (`KmsKeyId` asserted). No secret in code, env files, or the changefeed URI
  (charter R2).
- **[IT] SQS is KMS-encrypted and TLS-only** — a queue policy denies `aws:SecureTransport=false`
  (`test_sqs_queues_are_kms_encrypted_and_tls_only`); DLQs on both the ingest and nightly paths.
- **[IT] All S3 buckets block public access** (all four flags) and use KMS/SSE
  (`test_all_buckets_block_public_access`, `test_artifacts_bucket_uses_kms_and_blocks_public`).

## Network (T8)

- **[IT] Private, no internet egress (default: `crdb_egress_mode=privatelink`)** — the VPC has **no
  NAT gateways**; compute lives in isolated subnets and reaches AWS services over **≥8 PrivateLink
  interface endpoints** (Bedrock Runtime, Secrets Manager, KMS, ECR + ECR-Docker, CloudWatch Logs,
  SQS, STS) plus an S3 gateway endpoint, and reaches CockroachDB over a ninth, **mandatory** interface
  endpoint. VPC Flow Logs are on. (`NetworkTests`.)
- **[IT] Opt-in egress relaxation, default-secure** — CockroachDB Cloud PrivateLink is an
  **Advanced-tier** feature. Teams without it deploy with `-c crdb_egress_mode=public`, which
  provisions a NAT gateway and moves compute to private-with-egress subnets. This is a **deliberate,
  documented relaxation of T8** and is never the default: the compensating controls are TLS
  `verify-full` on every DB connection (R8), a CockroachDB Cloud **IP allowlist** on the NAT egress
  address, the security-group egress restriction to 26257/443 only, no inbound to compute, and the
  Fargate task still having **no public IP**. Asserted by `PublicEgressModeTests`.
- **[IT] Fargate task has no public IP** (`AssignPublicIp: DISABLED`).
- **[IT] PrivateLink to CockroachDB Cloud is fail-closed** — in the default mode, `cdk synth`
  **raises** if `crdb_privatelink_service_name` is absent (audit B2: it previously emitted a
  PLACEHOLDER output and deployed a VPC with no path to the database at all). **[DT]** the real
  endpoint-service name and accepting the connection.
- **[IT] WAF on the console** — a `REGIONAL` WebACL with **AWSManagedRulesCommonRuleSet** + an
  **IP rate-based rule**, associated to the console (`test_waf_web_acl_and_association`).

## Model guardrails (R6)

- **[IT] A Bedrock Guardrail** defines a **`PROMPT_ATTACK`** content filter, a
  `SensitiveInformationPolicyConfig` (PII), and a `ContextualGroundingPolicyConfig` (anti-hallucination
  for the distillation step). Both the responder and consolidator roles are granted
  `bedrock:ApplyGuardrail` and pass the guardrail id/version on every Converse call
  (`GuardrailAndDetectionTests`). This is the **outer ring**; the app-layer injection defenses in
  [`02-*`](./02-agent-and-app-guardrails.md) are the inner ring (defense in depth).

## Detection & response (T7, principle 12)

- **[IT] CloudTrail** — multi-region, **log-file validation on**, CMK-encrypted, delivered to a
  locked-down versioned bucket and to CloudWatch Logs.
- **[IT] GuardDuty** detector enabled (15-min findings). **[DT]** may already exist account-wide.
- **[IT] AWS Config** recorder + delivery + managed rules (S3 public-read/write prohibited, S3
  encryption, CloudTrail enabled, EBS encryption, IAM no-inline-policy). **[DT]** account singleton.
- **[IT] CloudWatch security alarms** off CloudTrail metric filters: **unauthorized API calls**,
  **root-account usage**, **IAM policy changes** (≥3 alarms asserted).

## What's deploy-time, not live today

- Real secret *values* (DSNs, webhook secret) populated in Secrets Manager.
- The CockroachDB Cloud PrivateLink endpoint-service name + accepting the connection — or, on a tier
  without PrivateLink, `-c crdb_egress_mode=public` plus a CockroachDB Cloud IP allowlist (see Network
  above).
- Actual account enablement of GuardDuty/Config if not already on.
- Per-request CSP nonces and the real console origin appended to `connect-src` (see doc 02).
- CI vulnerability-scan gate on the container image (planned).

## Verify

```bash
cd infra && .venv/bin/pytest -q        # security + source suites

# Both egress modes must synthesize. Context is supplied explicitly because the
# app fails fast on a deploy it knows cannot work (audit B2/B4); it is
# deliberately NOT in cdk.json, which would restore the silent default.
CDK_CONTEXT_JSON='{"agent_image_uri":"000000000000.dkr.ecr.us-east-1.amazonaws.com/postmortem-agent:test","crdb_egress_mode":"privatelink","crdb_privatelink_service_name":"com.amazonaws.vpce.us-east-1.vpce-svc-EXAMPLE"}' \
  .venv/bin/python app.py
CDK_CONTEXT_JSON='{"agent_image_uri":"000000000000.dkr.ecr.us-east-1.amazonaws.com/postmortem-agent:test","crdb_egress_mode":"public"}' \
  .venv/bin/python app.py
```
Both run inside `scripts/verify_phase2.sh`.
