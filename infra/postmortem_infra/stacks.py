from __future__ import annotations

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_iam as iam,
    aws_logs as logs,
    aws_s3 as s3,
    aws_secretsmanager as secretsmanager,
)
from constructs import Construct


class SharedStack(Stack):
    """Shared network, artifact, and secret resources."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

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
                )
            ],
        )

        self.artifacts = s3.Bucket(
            self,
            "Artifacts",
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.reader_secret = secretsmanager.Secret(
            self,
            "CockroachReaderSecret",
            description="Managed MCP reader URL/token for the Postmortem recall path",
        )
        self.writer_secret = secretsmanager.Secret(
            self,
            "CockroachWriterSecret",
            description="Scoped direct-SQL connection URL for atomic act+record transactions",
        )
        self.consolidator_secret = secretsmanager.Secret(
            self,
            "CockroachConsolidatorSecret",
            description=(
                "Scoped CockroachDB URL for procedural-memory consolidation writes"
            ),
        )
        self.changefeed_webhook_secret = secretsmanager.Secret(
            self,
            "ChangefeedWebhookSecret",
            description="Shared credential authenticating CockroachDB changefeed webhooks",
            generate_secret_string=secretsmanager.SecretStringGenerator(
                exclude_punctuation=True,
                password_length=40,
            ),
        )

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


class AppStack(Stack):
    """Always-on responder and streaming backend."""

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

        cluster = ecs.Cluster(self, "Cluster", vpc=shared.vpc, container_insights=True)
        task = ecs.FargateTaskDefinition(
            self,
            "AgentTask",
            cpu=512,
            memory_limit_mib=1024,
        )
        task.task_role.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=["*"],
            )
        )
        shared.artifacts.grant_read_write(task.task_role)
        shared.reader_secret.grant_read(task.task_role)
        shared.writer_secret.grant_read(task.task_role)

        log_group = logs.LogGroup(
            self,
            "AgentLogs",
            retention=logs.RetentionDays.ONE_MONTH,
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
                "POSTMORTEM_REASONING_MODEL_ID": "us.anthropic.claude-sonnet-4-6",
                "POSTMORTEM_EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
                "POSTMORTEM_REASONER": "strands",
                "POSTMORTEM_CORS_ORIGINS": (
                    console_origin or "http://localhost:3000"
                ),
                "POSTMORTEM_MCP_URL": "https://cockroachlabs.cloud/mcp",
                "ARTIFACTS_BUCKET": shared.artifacts.bucket_name,
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
            assign_public_ip=True,
            desired_count=1,
            task_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            health_check_grace_period=Duration.seconds(60),
        )
        service.target_group.configure_health_check(
            path="/healthz",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
        )

        CfnOutput(
            self,
            "AgentBaseUrl",
            value=f"http://{service.load_balancer.load_balancer_dns_name}",
        )
