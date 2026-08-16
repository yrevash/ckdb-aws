from __future__ import annotations

from aws_cdk import (
    Annotations,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_bedrock as bedrock,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
    aws_wafv2 as wafv2,
)
from constructs import Construct

# Model identifiers the compute tier is allowed to invoke. Kept here so IAM
# resource scoping (below) and the runtime env vars stay in lock-step — the role
# can invoke *exactly* the models the code names, nothing else (charter R1).
REASONING_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
TRIAGE_MODEL_ID = "us.anthropic.claude-3-5-haiku-20241022-v1:0"

_INFERENCE_PROFILE_PREFIXES = {"us", "eu", "apac", "us-gov"}


# ---- Network egress modes (audit B2) --------------------------------------
# The DEFAULT posture is "privatelink": zero compute egress to the internet
# (charter T8), with CockroachDB reached over an interface endpoint. That mode
# is only *reachable* when the operator supplies the CockroachDB Cloud
# endpoint-service name -- and CockroachDB Cloud PrivateLink is an ADVANCED
# tier feature, so teams on Standard/Basic cannot use it at all. Rather than
# let `cdk deploy` succeed into a VPC that can never dial the database, synth
# fails fast and offers the opt-in "public" mode: NAT egress + TLS 26257 to
# the cluster's public host. Public mode is a DOCUMENTED relaxation of T8
# (docs/security/01-aws-infrastructure-security.md, "Network"), never the
# default, and it warns at synth.
EGRESS_MODE_PRIVATELINK = "privatelink"
EGRESS_MODE_PUBLIC = "public"
EGRESS_MODES = (EGRESS_MODE_PRIVATELINK, EGRESS_MODE_PUBLIC)
DEFAULT_CRDB_EGRESS_CIDRS = "0.0.0.0/0"


def resolve_egress_mode(scope: Construct) -> str:
    """Read + validate the ``crdb_egress_mode`` context switch (audit B2)."""

    mode = (
        str(
            scope.node.try_get_context("crdb_egress_mode") or EGRESS_MODE_PRIVATELINK
        )
        .strip()
        .lower()
    )
    if mode not in EGRESS_MODES:
        raise ValueError(
            f"crdb_egress_mode={mode!r} is not a valid network egress mode. "
            f"Use one of: {', '.join(EGRESS_MODES)} "
            f"(default: {EGRESS_MODE_PRIVATELINK})."
        )
    return mode


def bedrock_model_resource_arns(
    region: str, account: str, model_ids: list[str]
) -> list[str]:
    """Return the least-privilege Bedrock resource ARNs for a set of model ids.

    Cross-region inference profiles (``us.``/``eu.`` prefixed) need both the
    inference-profile ARN *and* the underlying foundation-model ARNs the profile
    can route to — but never ``*``. Plain model ids resolve to a single
    foundation-model ARN. Every resource is a concrete, named model (R1).
    """

    resources: set[str] = set()
    for model_id in model_ids:
        prefix = model_id.split(".", 1)[0]
        if prefix in _INFERENCE_PROFILE_PREFIXES:
            resources.add(
                f"arn:aws:bedrock:{region}:{account}:inference-profile/{model_id}"
            )
            underlying = model_id.split(".", 1)[1]
            # The profile fans out across US regions; scope to the named model,
            # region-wildcarded (still a specific model, not a wildcard resource).
            resources.add(f"arn:aws:bedrock:*::foundation-model/{underlying}")
        else:
            resources.add(f"arn:aws:bedrock:{region}::foundation-model/{model_id}")
    return sorted(resources)


def bedrock_invoke_statements(
    region: str,
    account: str,
    model_ids: list[str],
    guardrail_arn: str,
) -> list[iam.PolicyStatement]:
    """Scoped ``InvokeModel`` + ``ApplyGuardrail`` statements (no wildcards)."""

    return [
        iam.PolicyStatement(
            sid="BedrockInvokeScopedModels",
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
            ],
            resources=bedrock_model_resource_arns(region, account, model_ids),
            conditions={"StringEquals": {"aws:RequestedRegion": region}},
        ),
        iam.PolicyStatement(
            sid="BedrockApplyGuardrail",
            actions=["bedrock:ApplyGuardrail"],
            resources=[guardrail_arn],
        ),
    ]


class SharedStack(Stack):
    """Shared network, artifact, secret, key, and guardrail resources.

    Everything here is private-and-encrypted by default (charter principle 2):
    a customer-managed KMS key encrypts S3/Secrets/SQS/Logs, compute lives in
    private subnets reached through VPC endpoints (isolated with no NAT in the
    default ``privatelink`` egress mode; NAT-routed only under the opt-in
    ``crdb_egress_mode=public``, audit B2), and a single Bedrock Guardrail
    screens every model call in both the responder and consolidator.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- Customer-managed KMS key (R8: encryption at rest, one CMK) --------
        # The real key carries the rotation config + service key-policy grants.
        # Consumers (this stack and the App/Consolidation stacks) use an
        # imported-by-ARN *reference* so every ``grant_*`` lands on the grantee's
        # IAM identity policy instead of mutating the key's resource policy —
        # which would otherwise create a cross-stack dependency cycle.
        real_key = kms.Key(
            self,
            "PostmortemKey",
            description="Postmortem CMK for S3, Secrets, SQS, and log encryption",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
            alias="alias/postmortem",
        )
        # CloudWatch Logs is not an IAM identity — grant it explicitly in the key
        # policy so it can write encrypted log groups in this region/account.
        real_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudWatchLogs",
                principals=[
                    iam.ServicePrincipal(f"logs.{self.region}.amazonaws.com")
                ],
                actions=[
                    "kms:Encrypt*",
                    "kms:Decrypt*",
                    "kms:ReEncrypt*",
                    "kms:GenerateDataKey*",
                    "kms:Describe*",
                ],
                resources=["*"],
                conditions={
                    "ArnLike": {
                        "kms:EncryptionContext:aws:logs:arn": (
                            f"arn:aws:logs:{self.region}:{self.account}:log-group:*"
                        )
                    }
                },
            )
        )
        self.key = kms.Key.from_key_arn(self, "PostmortemKeyRef", real_key.key_arn)

        # ---- Network egress mode (audit B2) ----------------------------------
        # Resolved and validated BEFORE any network resource exists, so a bad or
        # unreachable posture raises with the traceback pointing at the real
        # cause instead of at some downstream subnet lookup.
        self.egress_mode = resolve_egress_mode(self)
        # Alias kept for cross-stack readability; both names are the same switch.
        self.crdb_egress_mode = self.egress_mode
        crdb_service_name = self.node.try_get_context(
            "crdb_privatelink_service_name"
        )
        if self.egress_mode == EGRESS_MODE_PRIVATELINK and not crdb_service_name:
            raise ValueError(
                "crdb_egress_mode=privatelink (the secure default, charter T8: "
                "no compute egress to the internet) requires the CockroachDB "
                "Cloud PrivateLink endpoint-service name -- without it this VPC "
                "has NO route to the database and `cdk deploy` would succeed "
                "into a black hole (audit B2). Either:\n"
                "  1. cdk deploy -c crdb_privatelink_service_name="
                "com.amazonaws.vpce.<region>.vpce-svc-<id>\n"
                "     (CockroachDB Cloud ADVANCED tier; `ccloud cluster "
                "networking private-endpoint-services create <cluster>` "
                "prints the name), or\n"
                "  2. cdk deploy -c crdb_egress_mode=public\n"
                "     (Standard/Basic tiers: adds a NAT gateway and TLS 26257 "
                "egress to the cluster's public host. This RELAXES T8 -- read "
                "the 'Network' section of "
                "docs/security/01-aws-infrastructure-security.md first.)"
            )
        crdb_egress_cidrs = [
            cidr.strip()
            for cidr in str(
                self.node.try_get_context("crdb_egress_cidrs")
                or DEFAULT_CRDB_EGRESS_CIDRS
            ).split(",")
            if cidr.strip()
        ]
        if self.egress_mode == EGRESS_MODE_PUBLIC:
            Annotations.of(self).add_warning_v2(
                "postmortem:crdb-egress-mode-public",
                "crdb_egress_mode=public: compute subnets get NAT egress to the "
                "internet and the CockroachDB client security group opens TCP "
                f"26257 to {','.join(crdb_egress_cidrs)}. This is the "
                "documented, opt-in relaxation of T8 "
                "(docs/security/01-aws-infrastructure-security.md). Narrow it "
                "with -c crdb_egress_cidrs=<cidr>,<cidr> once the cluster's "
                "addresses are known.",
            )

        # ---- VPC: public subnets for the ALB only; compute is private --------
        # privatelink (default): no NAT gateway, compute in PRIVATE_ISOLATED,
        # reaching AWS services exclusively over PrivateLink interface endpoints
        # (T8: no compute egress to the internet).
        # public (opt-in, audit B2): one NAT gateway and PRIVATE_WITH_EGRESS
        # compute so a Standard/Basic CockroachDB Cloud cluster -- which has no
        # PrivateLink to offer -- is reachable over TLS. AWS service traffic
        # still goes over the interface endpoints in BOTH modes; only the SQL
        # port leaves the VPC.
        if self.egress_mode == EGRESS_MODE_PRIVATELINK:
            self.compute_subnet_type = ec2.SubnetType.PRIVATE_ISOLATED
            nat_gateways = 0
        else:
            self.compute_subnet_type = ec2.SubnetType.PRIVATE_WITH_EGRESS
            # One NAT: cost-bounded for the demo account; a production build
            # would use one per AZ (documented in 01-aws-infrastructure-security).
            nat_gateways = 1
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=nat_gateways,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=self.compute_subnet_type,
                    cidr_mask=24,
                ),
            ],
        )
        # The one subnet selection every compute construct in every stack must
        # use (audit B2). Hardcoding PRIVATE_ISOLATED anywhere would blow up in
        # public mode -- there is no isolated subnet group in that VPC.
        self.compute_subnets = ec2.SubnetSelection(
            subnet_type=self.compute_subnet_type
        )
        # VPC Flow Logs → encrypted CloudWatch (detection / network forensics).
        flow_log_group = logs.LogGroup(
            self,
            "VpcFlowLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=self.key,
            removal_policy=RemovalPolicy.DESTROY,
        )
        self.vpc.add_flow_log(
            "FlowLog",
            destination=ec2.FlowLogDestination.to_cloud_watch_logs(flow_log_group),
            traffic_type=ec2.FlowLogTrafficType.ALL,
        )

        # Gateway endpoint for S3 (free) + interface endpoints for every service
        # the isolated compute must reach. Without these, private compute is dark.
        self.vpc.add_gateway_endpoint(
            "S3Endpoint", service=ec2.GatewayVpcEndpointAwsService.S3
        )
        interface_endpoints = {
            "BedrockRuntimeEndpoint": ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
            "SecretsManagerEndpoint": ec2.InterfaceVpcEndpointAwsService.SECRETS_MANAGER,
            "KmsEndpoint": ec2.InterfaceVpcEndpointAwsService.KMS,
            "EcrApiEndpoint": ec2.InterfaceVpcEndpointAwsService.ECR,
            "EcrDockerEndpoint": ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
            "LogsEndpoint": ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
            "SqsEndpoint": ec2.InterfaceVpcEndpointAwsService.SQS,
            "StsEndpoint": ec2.InterfaceVpcEndpointAwsService.STS,
        }
        for endpoint_id, service in interface_endpoints.items():
            self.vpc.add_interface_endpoint(
                endpoint_id,
                service=service,
                private_dns_enabled=True,
                # audit B2: follow the mode-selected compute subnets. Hardcoding
                # PRIVATE_ISOLATED here fails synth in public mode ("no Isolated
                # subnet groups in this VPC"). The endpoints are kept in BOTH
                # modes so Bedrock/Secrets/KMS/SQS/Logs/ECR traffic never
                # traverses the NAT gateway.
                subnets=self.compute_subnets,
            )

        # ---- Path to CockroachDB Cloud ---------------------------------------
        # In the default privatelink mode the real endpoint-service name is
        # minted when the Advanced cluster is stood up (doc 04) and is a REQUIRED
        # synth input (checked above). In the opt-in public mode there is no
        # endpoint to create and the same SQL traffic leaves over NAT instead.
        # The security group itself is unconditional in both modes: it is also
        # the consolidator Lambda's only SG (consolidation_stack.py) and the
        # AppStack ingress rule references it by id.
        self.crdb_security_group = ec2.SecurityGroup(
            self,
            "CrdbClientSecurityGroup",
            vpc=self.vpc,
            description=(
                "Egress to CockroachDB Cloud over PrivateLink (TLS 26257)"
                if self.egress_mode == EGRESS_MODE_PRIVATELINK
                else (
                    "Egress to CockroachDB Cloud over public TLS 26257 "
                    "(opt-in crdb_egress_mode=public)"
                )
            ),
            allow_all_outbound=False,
        )
        # HTTPS to the in-VPC AWS interface endpoints (Secrets/KMS/Bedrock/SQS/
        # Logs) is required in BOTH modes -- that traffic never leaves the VPC.
        self.crdb_security_group.add_egress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(443),
            description="HTTPS to in-VPC AWS PrivateLink interface endpoints",
        )
        if self.egress_mode == EGRESS_MODE_PRIVATELINK:
            # The CockroachDB PrivateLink endpoint lives inside this VPC, so SQL
            # egress stays CIDR-scoped to the VPC -- nothing leaves (T8).
            self.crdb_security_group.add_egress_rule(
                peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
                connection=ec2.Port.tcp(26257),
                description="CockroachDB SQL over TLS via PrivateLink",
            )
        else:
            # audit B2: in public mode the cluster's SQL endpoint is a public
            # DNS host, so VPC-CIDR-scoped egress would black-hole every
            # connection. Widen port 26257 ONLY, and only to the operator's
            # allowlist (default 0.0.0.0/0, narrowable with -c
            # crdb_egress_cidrs=...). TLS stays verify-full (R8). This is the
            # single rule in the stack that leaves the VPC.
            for cidr in crdb_egress_cidrs:
                self.crdb_security_group.add_egress_rule(
                    peer=ec2.Peer.ipv4(cidr),
                    connection=ec2.Port.tcp(26257),
                    description=(
                        "CockroachDB SQL over public TLS "
                        f"({cidr}, crdb_egress_mode=public)"
                    ),
                )
        # This SG is attached to *both* the CockroachDB interface endpoint and the
        # in-VPC client compute that shares it (e.g. the consolidator Lambda). An
        # interface endpoint accepts no inbound traffic without an explicit ingress
        # rule, so self-reference 26257 here lets any member of this SG reach the
        # endpoint. Cross-SG clients (the Fargate app service) are added as peers
        # from their own stack via ``crdb_security_group.connections.allow_from``.
        # Inert in public mode (no in-VPC endpoint to reach) but harmless -- the
        # peer is this SG, not a CIDR.
        self.crdb_security_group.add_ingress_rule(
            peer=self.crdb_security_group,
            connection=ec2.Port.tcp(26257),
            description="CockroachDB SQL to the PrivateLink endpoint from shared-SG clients",
        )
        if self.egress_mode == EGRESS_MODE_PRIVATELINK:
            ec2.InterfaceVpcEndpoint(
                self,
                "CrdbPrivateLinkEndpoint",
                vpc=self.vpc,
                service=ec2.InterfaceVpcEndpointService(crdb_service_name, 26257),
                security_groups=[self.crdb_security_group],
                subnets=self.compute_subnets,
            )
            CfnOutput(self, "CrdbPrivateLinkServiceName", value=crdb_service_name)
        else:
            CfnOutput(
                self,
                "CrdbPrivateLinkServiceName",
                value="disabled (crdb_egress_mode=public)",
            )
        # The deployed stack declares its own network posture so an auditor can
        # read it off the outputs rather than trusting a doc (audit B2).
        CfnOutput(self, "CrdbEgressMode", value=self.egress_mode)

        # ---- Private, CMK-encrypted artifact bucket (T8: no public S3) --------
        self.artifacts = s3.Bucket(
            self,
            "Artifacts",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.key,
            bucket_key_enabled=True,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # ---- Secrets (R2): CMK-encrypted, read only by the role that needs it -
        self.reader_secret = secretsmanager.Secret(
            self,
            "CockroachReaderSecret",
            description="Managed MCP reader URL/token for the Postmortem recall path",
            encryption_key=self.key,
        )
        # audit B1: reader_secret above is the *Managed MCP* URL/token, not a SQL
        # DSN -- it cannot feed runtime.py's reader pool. The recall path also
        # needs a direct-SQL, read-only DSN authenticating as
        # postmortem_agent_reader (db/migrations/0007_audit_logging.sql) or AWS
        # mode fails closed on the audit-C3 distinctness check (charter R7/T2)
        # and the task crashloops before /healthz.
        self.sql_reader_secret = secretsmanager.Secret(
            self,
            "CockroachSqlReaderSecret",
            description=(
                "Read-only direct-SQL connection URL (postmortem_agent_reader) "
                "for the Postmortem recall path"
            ),
            encryption_key=self.key,
        )
        self.writer_secret = secretsmanager.Secret(
            self,
            "CockroachWriterSecret",
            description="Scoped direct-SQL connection URL for atomic act+record transactions",
            encryption_key=self.key,
        )
        self.consolidator_secret = secretsmanager.Secret(
            self,
            "CockroachConsolidatorSecret",
            description=(
                "Scoped CockroachDB URL for procedural-memory consolidation writes"
            ),
            encryption_key=self.key,
        )
        self.changefeed_webhook_secret = secretsmanager.Secret(
            self,
            "ChangefeedWebhookSecret",
            description="Shared credential authenticating CockroachDB changefeed webhooks",
            encryption_key=self.key,
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=40,
            ),
        )

        # ---- Bedrock Guardrail (R6): one guardrail, both model callers --------
        self.guardrail = bedrock.CfnGuardrail(
            self,
            "PostmortemGuardrail",
            name="postmortem-guardrail",
            description=(
                "Screens prompt-injection, harmful content, and sensitive data on "
                "both the responder and the consolidator model calls"
            ),
            blocked_input_messaging=(
                "This request was blocked by the Postmortem safety guardrail."
            ),
            blocked_outputs_messaging=(
                "This response was blocked by the Postmortem safety guardrail."
            ),
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    # Prompt-injection defense for untrusted alert/log/webhook text.
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="PROMPT_ATTACK",
                        input_strength="HIGH",
                        output_strength="NONE",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="MISCONDUCT",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="HATE",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="INSULTS",
                        input_strength="MEDIUM",
                        output_strength="MEDIUM",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="VIOLENCE",
                        input_strength="MEDIUM",
                        output_strength="MEDIUM",
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="SEXUAL",
                        input_strength="HIGH",
                        output_strength="HIGH",
                    ),
                ]
            ),
            # Block advice to run irreversible destructive ops without escalation.
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="DestructiveIrreversibleOps",
                        type="DENY",
                        definition=(
                            "Advising or authorizing irreversible, high-blast-radius "
                            "database or infrastructure operations (DROP, TRUNCATE, "
                            "mass DELETE, cluster/topology destruction) without an "
                            "explicit human-approval step."
                        ),
                        examples=[
                            "Just DROP the incidents table to clear the backlog.",
                            "TRUNCATE all orders and restart the cluster now.",
                            "Delete every row in production to fix the error.",
                        ],
                    )
                ]
            ),
            # Mask secrets/PII that leak into incident text (data classification §5).
            sensitive_information_policy_config=(
                bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                    pii_entities_config=[
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(
                            type="EMAIL", action="ANONYMIZE"
                        ),
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(
                            type="PASSWORD", action="BLOCK"
                        ),
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(
                            type="AWS_SECRET_KEY", action="BLOCK"
                        ),
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(
                            type="AWS_ACCESS_KEY", action="BLOCK"
                        ),
                        bedrock.CfnGuardrail.PiiEntityConfigProperty(
                            type="IP_ADDRESS", action="ANONYMIZE"
                        ),
                    ]
                )
            ),
            # Reject fixes not grounded in recalled memory (charter principle 7).
            contextual_grounding_policy_config=(
                bedrock.CfnGuardrail.ContextualGroundingPolicyConfigProperty(
                    filters_config=[
                        bedrock.CfnGuardrail.ContextualGroundingFilterConfigProperty(
                            type="GROUNDING", threshold=0.75
                        ),
                        bedrock.CfnGuardrail.ContextualGroundingFilterConfigProperty(
                            type="RELEVANCE", threshold=0.75
                        ),
                    ]
                )
            ),
        )
        self.guardrail_version = bedrock.CfnGuardrailVersion(
            self,
            "PostmortemGuardrailVersion",
            guardrail_identifier=self.guardrail.attr_guardrail_id,
        )
        self.guardrail_arn = self.guardrail.attr_guardrail_arn

        CfnOutput(self, "KmsKeyArn", value=self.key.key_arn)
        CfnOutput(self, "ArtifactsBucketName", value=self.artifacts.bucket_name)
        CfnOutput(self, "ReaderSecretArn", value=self.reader_secret.secret_arn)
        CfnOutput(
            self, "SqlReaderSecretArn", value=self.sql_reader_secret.secret_arn
        )
        CfnOutput(self, "WriterSecretArn", value=self.writer_secret.secret_arn)
        CfnOutput(
            self,
            "ConsolidatorSecretArn",
            value=self.consolidator_secret.secret_arn,
        )
        CfnOutput(
            self,
            "ChangefeedWebhookSecretArn",
            value=self.changefeed_webhook_secret.secret_arn,
        )
        CfnOutput(self, "GuardrailArn", value=self.guardrail_arn)
        CfnOutput(self, "GuardrailVersion", value=self.guardrail_version.attr_version)


class AppStack(Stack):
    """Always-on responder and streaming backend.

    The interactive agent runs as a Fargate task in SharedStack's compute subnets
    (no public IP; isolated with no NAT by default, NAT-routed only under the
    opt-in ``crdb_egress_mode=public`` -- audit B2); only the ALB is
    internet-facing, and it sits behind AWS WAF. The task role can invoke exactly
    the named Bedrock models and read only its own secrets (R1).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        shared: SharedStack,
        agent_image_uri: str | None,
        console_origin: str | None,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # audit B4: the old fallback here was a stock python:3.12-slim image.
        # CloudFormation deploys it happily and the task then never answers
        # GET /healthz, so the service sits in a health-check crashloop that
        # looks like an app bug. The image is a required deploy input.
        if not agent_image_uri:
            raise ValueError(
                "AppStack requires -c agent_image_uri=<image-uri>. Without it "
                "the Fargate service would deploy a stock "
                "python:3.12-slim image that never answers GET /healthz, so "
                "the service silently never goes healthy (audit B4). Build and "
                "push the real image from backend/Dockerfile, then:\n"
                "  cdk deploy -c agent_image_uri=<account>.dkr.ecr.<region>"
                ".amazonaws.com/postmortem-agent:<tag>\n"
                "See infra/README.md."
            )

        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=shared.vpc,
            # container_insights=True is deprecated; use the v2 enum so synth stops
            # warning and this survives the next CDK major.
            container_insights_v2=ecs.ContainerInsights.ENABLED,
        )

        # Distinct execution role (pulls the image / writes logs) and task role
        # (what the running agent may do) — never merged (least privilege).
        task = ecs.FargateTaskDefinition(
            self,
            "AgentTask",
            cpu=512,
            memory_limit_mib=1024,
        )

        for statement in bedrock_invoke_statements(
            self.region,
            self.account,
            [REASONING_MODEL_ID, EMBEDDING_MODEL_ID, TRIAGE_MODEL_ID],
            shared.guardrail_arn,
        ):
            task.task_role.add_to_principal_policy(statement)

        # Resource-scoped to the artifact bucket + CMK; not wildcards.
        shared.artifacts.grant_read_write(task.task_role)
        shared.reader_secret.grant_read(task.task_role)
        shared.writer_secret.grant_read(task.task_role)
        # audit B1: the SQL reader DSN is its own secret with its own grant --
        # "grant_read on exactly the one secret they need"
        # (docs/security/01-aws-infrastructure-security.md).
        shared.sql_reader_secret.grant_read(task.task_role)

        log_group = logs.LogGroup(
            self,
            "AgentLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=shared.key,
            removal_policy=RemovalPolicy.DESTROY,
        )
        container = task.add_container(
            "Agent",
            image=ecs.ContainerImage.from_registry(agent_image_uri),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="postmortem-agent",
                log_group=log_group,
            ),
            environment={
                "POSTMORTEM_RUNTIME_MODE": "aws",
                "POSTMORTEM_HOST": "0.0.0.0",
                "POSTMORTEM_PORT": "8000",
                "POSTMORTEM_AWS_REGION": self.region,
                "POSTMORTEM_REASONING_MODEL_ID": REASONING_MODEL_ID,
                "POSTMORTEM_EMBEDDING_MODEL_ID": EMBEDDING_MODEL_ID,
                "POSTMORTEM_REASONER": "strands",
                "POSTMORTEM_CORS_ORIGINS": (
                    console_origin or "http://localhost:3000"
                ),
                "POSTMORTEM_MCP_URL": "https://cockroachlabs.cloud/mcp",
                # audit B2 (adjacent): the managed MCP endpoint above is a
                # PUBLIC HTTPS host. In privatelink mode the task has no route
                # off the VPC at all, so MCP recall could only ever time out --
                # another deploy that passes /healthz and then fails the demo.
                # Recall runs over the reader DSN through the same PrivateLink
                # path instead; public mode keeps the managed-MCP default.
                "POSTMORTEM_RECALL_BACKEND": (
                    "sql" if shared.egress_mode == "privatelink" else "mcp"
                ),
                "ARTIFACTS_BUCKET": shared.artifacts.bucket_name,
                # Guardrail applied on every model call by the app (R6).
                "POSTMORTEM_GUARDRAIL_ID": shared.guardrail.attr_guardrail_id,
                "POSTMORTEM_GUARDRAIL_VERSION": shared.guardrail_version.attr_version,
                # DB connections must verify TLS end-to-end (R8).
                "POSTMORTEM_DB_SSLMODE": "verify-full",
            },
            secrets={
                "POSTMORTEM_MCP_TOKEN": ecs.Secret.from_secrets_manager(
                    shared.reader_secret
                ),
                # audit B1: config.validate() still hard-requires
                # POSTMORTEM_DATABASE_URL in aws mode, so keep injecting it (it
                # is the writer DSN -- the fallback runtime.py would use if the
                # role-specific vars ever went missing). The two role-specific
                # vars below are what build_runtime actually reads, and they
                # must resolve to two DIFFERENT SQL principals or the audit-C3
                # check raises RoleScopeViolation at import (charter R7/T2).
                "POSTMORTEM_DATABASE_URL": ecs.Secret.from_secrets_manager(
                    shared.writer_secret
                ),
                "POSTMORTEM_WRITER_DATABASE_URL": ecs.Secret.from_secrets_manager(
                    shared.writer_secret
                ),
                "POSTMORTEM_READER_DATABASE_URL": ecs.Secret.from_secrets_manager(
                    shared.sql_reader_secret
                ),
            },
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "AgentService",
            cluster=cluster,
            task_definition=task,
            public_load_balancer=True,
            # Task has NO public IP; it lives in the shared compute subnets --
            # isolated in privatelink mode, NAT-routed in public mode (T8/B2).
            assign_public_ip=False,
            desired_count=1,
            task_subnets=shared.compute_subnets,
            health_check_grace_period=Duration.seconds(60),
        )
        service.target_group.configure_health_check(
            path="/healthz",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
        )

        # The app task must reach CockroachDB over the shared PrivateLink endpoint.
        # The endpoint's SG lives in SharedStack, and AppStack already depends on
        # SharedStack — so opening the ingress via ``connections.allow_from`` would
        # try to make SharedStack depend back on this service's SG and CDK rejects
        # the cyclic reference. Instead we author the ingress as a low-level rule
        # *in this stack*: it references the shared endpoint SG by id (a dependency
        # that already exists) and names this Fargate service's SG as the peer.
        # Egress from the service SG is CDK-default allow-all. In privatelink mode
        # that is inert -- the subnets have no route off the VPC (T8). Under
        # the opt-in crdb_egress_mode=public it becomes real internet egress via
        # the NAT gateway; that residual exposure is disclosed in
        # docs/security/01-aws-infrastructure-security.md and THREAT_MODEL.md
        # §7. Giving the service its own non-allow-all SG is the named follow-up.
        app_service_sg = service.service.connections.security_groups[0]
        ec2.CfnSecurityGroupIngress(
            self,
            "CrdbEndpointIngressFromAppService",
            group_id=shared.crdb_security_group.security_group_id,
            ip_protocol="tcp",
            from_port=26257,
            to_port=26257,
            source_security_group_id=app_service_sg.security_group_id,
            description="CockroachDB SQL to the PrivateLink endpoint from the app Fargate service",
        )

        # ---- AWS WAF on the console ALB (T8) ---------------------------------
        web_acl = wafv2.CfnWebACL(
            self,
            "ConsoleWebAcl",
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(
                allow=wafv2.CfnWebACL.AllowActionProperty()
            ),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="postmortem-console-waf",
                sampled_requests_enabled=True,
            ),
            rules=[
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSCommonRules",
                    priority=0,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(
                        none={}
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=(
                            wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                                vendor_name="AWS",
                                name="AWSManagedRulesCommonRuleSet",
                            )
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="common-rules",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSKnownBadInputs",
                    priority=1,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(
                        none={}
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=(
                            wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                                vendor_name="AWS",
                                name="AWSManagedRulesKnownBadInputsRuleSet",
                            )
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="known-bad-inputs",
                        sampled_requests_enabled=True,
                    ),
                ),
                wafv2.CfnWebACL.RuleProperty(
                    name="AWSIpReputation",
                    priority=2,
                    override_action=wafv2.CfnWebACL.OverrideActionProperty(
                        none={}
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        managed_rule_group_statement=(
                            wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                                vendor_name="AWS",
                                name="AWSManagedRulesAmazonIpReputationList",
                            )
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="ip-reputation",
                        sampled_requests_enabled=True,
                    ),
                ),
                # Per-IP rate limit — blunts credential-stuffing / DoS (T8).
                wafv2.CfnWebACL.RuleProperty(
                    name="RateLimit",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(
                        block=wafv2.CfnWebACL.BlockActionProperty()
                    ),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=(
                            wafv2.CfnWebACL.RateBasedStatementProperty(
                                limit=2000,
                                aggregate_key_type="IP",
                            )
                        )
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="rate-limit",
                        sampled_requests_enabled=True,
                    ),
                ),
            ],
        )
        wafv2.CfnWebACLAssociation(
            self,
            "ConsoleWebAclAssociation",
            resource_arn=service.load_balancer.load_balancer_arn,
            web_acl_arn=web_acl.attr_arn,
        )

        CfnOutput(
            self,
            "AgentBaseUrl",
            value=f"http://{service.load_balancer.load_balancer_dns_name}",
        )
        CfnOutput(self, "ConsoleWebAclArn", value=web_acl.attr_arn)
