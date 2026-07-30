# AWS infrastructure

The Phase 1 CDK application establishes the original master plan's AWS foundation:

- a public-subnet ECS Fargate service for the always-on responder/backend;
- separately stored CockroachDB reader and writer credentials;
- least-privilege Bedrock invoke permissions;
- an encrypted/versioned S3 artifact bucket;
- CloudWatch logs and an application load balancer.

Later phases add the changefeed receiver, SQS consolidation pipeline, scheduled consolidator, and
multi-region demo resources without changing the responder contract.

## Synth

```bash
cd infra
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cdk synth \
  -c agent_image_uri=000000000000.dkr.ecr.us-east-1.amazonaws.com/postmortem-agent:latest
```

The CockroachDB secret values are intentionally not accepted as CDK context or committed configuration.
Populate the created Secrets Manager secrets after deployment.
