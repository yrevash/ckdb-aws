# AWS infrastructure

The Phase 1 CDK application establishes the original master plan's AWS foundation:

- an ECS Fargate service for the always-on responder/backend, running in private compute subnets with
  no public IP behind an internet-facing ALB;
- separately stored credentials: a Managed-MCP reader token plus three distinct SQL identities
  (reader / writer / consolidator), one Secrets Manager secret each;
- least-privilege Bedrock invoke permissions;
- an encrypted/versioned S3 artifact bucket;
- CloudWatch logs and an application load balancer.

Later phases add the changefeed receiver, SQS consolidation pipeline, scheduled consolidator, and
multi-region demo resources without changing the responder contract.

## Required context

The app **fails fast at synth** rather than deploying something that provably cannot work (audit
B2/B4). These values are supplied per-deploy with `-c`, deliberately **not** defaulted in `cdk.json`
— a default is exactly what made the broken deploys silent.

| Context key | Required? | Meaning |
|---|---|---|
| `agent_image_uri` | **required** | ECR image for the responder/backend. Synth fails without it; there is no stock-image default (a stock image deploys green and never passes `/healthz`). |
| `crdb_egress_mode` | optional, default `privatelink` | How compute reaches CockroachDB: `privatelink` or `public`. Any other value is refused. |
| `crdb_privatelink_service_name` | **required in `privatelink` mode** | The CockroachDB Cloud endpoint-service name. Synth fails without it rather than deploying a VPC with no path to the database. |
| `account` / `region` | optional | Target environment. |
| `console_origin` | optional | Console origin appended to the backend's CORS/CSP allowances. |
| `consolidation_model_id` / `consolidation_model_mode` | optional | Bedrock model wiring for the consolidator. |

## Egress modes

- **`privatelink` (default, strictest).** No NAT gateways; compute in isolated subnets; CockroachDB
  reached over a mandatory interface endpoint. Requires a CockroachDB Cloud endpoint-service name,
  which only **Advanced-tier** clusters can mint.
- **`public` (opt-in).** Provisions a NAT gateway and moves compute to private-with-egress subnets,
  for CockroachDB Cloud tiers where PrivateLink is not offered. This is a **documented relaxation of
  T8**, never the default. Pair it with a CockroachDB Cloud **IP allowlist** on the NAT egress
  address; TLS `verify-full`, the 26257/443-only security-group egress, no inbound to compute, and
  the Fargate task's absent public IP are the compensating controls. See
  [`docs/security/01-aws-infrastructure-security.md`](../docs/security/01-aws-infrastructure-security.md).

> Switching `crdb_egress_mode` changes the VPC's subnet layout (CIDRs, route tables, endpoint
> placement, logical IDs). Nothing is deployed yet, so there is no replacement risk today — but once
> a stack **is** deployed, flipping the mode is a **VPC-replacing** change, not an in-place update.

## Synth

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# privatelink mode (default, strictest): requires the CockroachDB Cloud
# endpoint-service name, which only Advanced-tier clusters can mint.
cdk synth \
  -c agent_image_uri=000000000000.dkr.ecr.us-east-1.amazonaws.com/postmortem-agent:latest \
  -c crdb_privatelink_service_name=com.amazonaws.vpce.us-east-1.vpce-svc-EXAMPLE

# public mode (opt-in): NAT egress for clusters without PrivateLink. Pair it
# with a CockroachDB Cloud IP allowlist on the NAT EIP.
cdk synth \
  -c agent_image_uri=000000000000.dkr.ecr.us-east-1.amazonaws.com/postmortem-agent:latest \
  -c crdb_egress_mode=public
```

The CockroachDB secret values are intentionally not accepted as CDK context or committed configuration.
Populate the created Secrets Manager secrets after deployment — including the **two distinct** SQL
DSNs (`postmortem_agent_reader` / `postmortem_agent_writer`): the backend refuses to start if they are
identical or share a username (audit C3).
