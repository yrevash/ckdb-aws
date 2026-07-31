# 01 — AWS Infrastructure Security

Implements the "AWS infrastructure security" row of [`00-security-charter.md`](./00-security-charter.md)
in the CDK (`infra/`). Every control below is **synthesized and asserted** by `infra/tests/test_security.py`
(25 tests, run inside `scripts/verify_phase2.sh`) unless tagged **[deploy-time]**.

Status tags: **[IT]** implemented + synth-tested · **[DT]** deploy-time (needs the real account Aug 1).

## Stacks

| Stack | Responsibility |
|-------|----------------|
| `SharedStack` | The CMK, VPC (private/isolated), PrivateLink endpoints, artifact bucket, Secrets, the Bedrock Guardrail |
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

## Data protection — encryption (R8)

- **[IT] One customer-managed KMS key** (rotation enabled) encrypts S3 artifacts, all Secrets, the SQS
  queues, and the CloudWatch log groups. CloudTrail gets its own dedicated CMK. (`EncryptionTests`.)
- **[IT] Secrets Manager for all credentials** — ≥4 secrets (CockroachDB reader/writer/consolidator
  DSNs + the changefeed webhook secret), every one CMK-encrypted (`KmsKeyId` asserted). No secret in
  code, env files, or the changefeed URI (charter R2).
- **[IT] SQS is KMS-encrypted and TLS-only** — a queue policy denies `aws:SecureTransport=false`
  (`test_sqs_queues_are_kms_encrypted_and_tls_only`); DLQs on both the ingest and nightly paths.
- **[IT] All S3 buckets block public access** (all four flags) and use KMS/SSE
  (`test_all_buckets_block_public_access`, `test_artifacts_bucket_uses_kms_and_blocks_public`).

## Network (T8)

- **[IT] Private, no internet egress** — the VPC has **no NAT gateways**; compute lives in isolated
  subnets and reaches AWS services over **≥8 PrivateLink interface endpoints** (Bedrock Runtime,
  Secrets Manager, KMS, ECR + ECR-Docker, CloudWatch Logs, SQS, STS) plus an S3 gateway endpoint.
  VPC Flow Logs are on. (`NetworkTests`.)
- **[IT] Fargate task has no public IP** (`AssignPublicIp: DISABLED`).
- **[IT] PrivateLink to CockroachDB Cloud** — an interface-endpoint + security group egress to
  CockroachDB over TLS 26257 is defined in `SharedStack`. **[DT]** the real CockroachDB Cloud endpoint
  service name is wired from config at deploy.
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

## What's deploy-time (Aug 1), not live today

- Real secret *values* (DSNs, webhook secret) populated in Secrets Manager.
- The CockroachDB Cloud PrivateLink endpoint-service name + accepting the connection.
- Actual account enablement of GuardDuty/Config if not already on.
- Per-request CSP nonces and the real console origin appended to `connect-src` (see doc 02).
- CI vulnerability-scan gate on the container image (planned).

## Verify

```bash
cd infra && .venv/bin/pytest -q        # 25 security + source tests
.venv/bin/python app.py                # cdk synth, exit 0
```
Both run inside `scripts/verify_phase2.sh`.
