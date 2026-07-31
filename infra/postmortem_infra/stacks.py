from __future__ import annotations

from aws_cdk import (
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
    isolated subnets reached only through VPC endpoints, and a single Bedrock
    Guardrail screens every model call in both the responder and consolidator.
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

        # ---- VPC: public subnets for the ALB only; compute is isolated --------
        # No NAT gateway. Isolated compute reaches AWS services exclusively over
        # PrivateLink interface endpoints (T8: no compute egress to the internet).
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="private",
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    cidr_mask=24,
                ),
            ],
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
                subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ),
            )

        # ---- PrivateLink to CockroachDB Cloud --------------------------------
        # [deploy-time Aug 1] The real endpoint-service name is minted when the
        # Advanced cluster is stood up (doc 04). We create the security group and
        # wire the endpoint only when the service name is supplied via context;
        # otherwise we emit the placeholder so the deploy step is unmissable.
        self.crdb_security_group = ec2.SecurityGroup(
            self,
            "CrdbClientSecurityGroup",
            vpc=self.vpc,
            description="Egress to CockroachDB Cloud over PrivateLink (TLS 26257)",
            allow_all_outbound=False,
        )
        # Both the CockroachDB PrivateLink endpoint and the AWS interface
        # endpoints (Secrets/KMS/Bedrock/SQS/Logs) live inside this VPC, so all
        # egress is scoped to the VPC CIDR — nothing leaves to the internet.
        self.crdb_security_group.add_egress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(26257),
            description="CockroachDB SQL over TLS via PrivateLink",
        )
        self.crdb_security_group.add_egress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(443),
            description="HTTPS to in-VPC AWS PrivateLink interface endpoints",
        )
        # This SG is attached to *both* the CockroachDB interface endpoint and the
        # in-VPC client compute that shares it (e.g. the consolidator Lambda). An
        # interface endpoint accepts no inbound traffic without an explicit ingress
        # rule, so self-reference 26257 here lets any member of this SG reach the
        # endpoint. Cross-SG clients (the Fargate app service) are added as peers
        # from their own stack via ``crdb_security_group.connections.allow_from``.
        self.crdb_security_group.add_ingress_rule(
            peer=self.crdb_security_group,
            connection=ec2.Port.tcp(26257),
            description="CockroachDB SQL to the PrivateLink endpoint from shared-SG clients",
        )
        crdb_service_name = self.node.try_get_context("crdb_privatelink_service_name")
        if crdb_service_name:
            ec2.InterfaceVpcEndpoint(
                self,
                "CrdbPrivateLinkEndpoint",
                vpc=self.vpc,
                service=ec2.InterfaceVpcEndpointService(crdb_service_name, 26257),
                security_groups=[self.crdb_security_group],
                subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
                ),
            )
            CfnOutput(
                self,
                "CrdbPrivateLinkServiceName",
                value=crdb_service_name,
            )
        else:
            CfnOutput(
                self,
                "CrdbPrivateLinkServiceName",
                value="PLACEHOLDER-set-crdb_privatelink_service_name-[deploy-time Aug 1]",
            )

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

    The interactive agent runs as a Fargate task in *isolated* subnets (no public
    IP); only the ALB is internet-facing, and it sits behind AWS WAF. The task
    role can invoke exactly the named Bedrock models and read only its own
    secrets (R1).
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

        if not agent_image_uri:
            agent_image_uri = "public.ecr.aws/docker/library/python:3.12-slim"

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
                "POSTMORTEM_DATABASE_URL": ecs.Secret.from_secrets_manager(
                    shared.writer_secret
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
            # Task has NO public IP; it lives in isolated subnets (T8).
            assign_public_ip=False,
            desired_count=1,
            task_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_ISOLATED
            ),
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
        # Egress from the service SG is allow-all by default (T8 keeps it in-VPC
        # via isolated subnets + no NAT), so only ingress needs opening.
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
