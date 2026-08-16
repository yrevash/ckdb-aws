from __future__ import annotations

from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    Stack,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_integrations as apigwv2_integrations,
    aws_cloudwatch as cloudwatch,
    aws_events as events,
    aws_events_targets as event_targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_event_sources as event_sources,
    aws_logs as logs,
    aws_sqs as sqs,
)
from constructs import Construct

from .stacks import (
    EMBEDDING_MODEL_ID,
    SharedStack,
    bedrock_invoke_statements,
)


class ConsolidationStack(Stack):
    """Changefeed → FIFO SQS → sleep-time procedural-memory consolidation.

    Both Lambdas run in SharedStack's compute subnets (isolated with no
    internet egress by default; NAT-routed only under the opt-in
    ``crdb_egress_mode=public`` -- audit B2), the queues and logs are
    CMK-encrypted and TLS-only, and the consolidator's Bedrock grant is
    scoped to the exact distillation/embedding models plus the shared guardrail.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        shared: SharedStack,
        consolidation_model_id: str | None = None,
        consolidation_model_mode: str = "bedrock",
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        source = Path(__file__).resolve().parents[2] / "consolidation"
        model_id = (
            consolidation_model_id
            or "us.anthropic.claude-3-5-haiku-20241022-v1:0"
        )

        # Both Lambdas follow SharedStack's mode-selected compute subnets
        # (audit B2) -- PRIVATE_ISOLATED by default, PRIVATE_WITH_EGRESS only
        # under the opt-in crdb_egress_mode=public. Hardcoding the subnet type
        # here would fail synth in public mode ("no Isolated subnet groups").
        vpc_subnets = shared.compute_subnets

        dead_letter_queue = sqs.Queue(
            self,
            "ConsolidationDeadLetterQueue",
            fifo=True,
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=shared.key,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        queue = sqs.Queue(
            self,
            "ConsolidationQueue",
            fifo=True,
            content_based_deduplication=False,
            deduplication_scope=sqs.DeduplicationScope.QUEUE,
            fifo_throughput_limit=sqs.FifoThroughputLimit.PER_QUEUE,
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=shared.key,
            enforce_ssl=True,
            retention_period=Duration.days(7),
            visibility_timeout=Duration.minutes(60),
            dead_letter_queue=sqs.DeadLetterQueue(
                queue=dead_letter_queue,
                max_receive_count=5,
            ),
        )

        receiver_logs = logs.LogGroup(
            self,
            "ReceiverLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=shared.key,
        )
        receiver = lambda_.DockerImageFunction(
            self,
            "ChangefeedReceiver",
            code=lambda_.DockerImageCode.from_image_asset(
                str(source),
                cmd=["postmortem_consolidation.handlers.receiver_handler"],
            ),
            description="Authenticates CockroachDB changefeed webhooks and fast-acks to SQS",
            timeout=Duration.seconds(10),
            memory_size=256,
            reserved_concurrent_executions=5,
            log_group=receiver_logs,
            vpc=shared.vpc,
            vpc_subnets=vpc_subnets,
            environment={
                "CONSOLIDATION_QUEUE_URL": queue.queue_url,
                "CHANGEFEED_WEBHOOK_SECRET_ARN": (
                    shared.changefeed_webhook_secret.secret_arn
                ),
            },
        )
        # Least privilege: send to the one queue, read the one secret. No Bedrock,
        # no DB write, no wildcards.
        queue.grant_send_messages(receiver)
        shared.changefeed_webhook_secret.grant_read(receiver)

        worker_logs = logs.LogGroup(
            self,
            "ConsolidatorLogs",
            retention=logs.RetentionDays.ONE_MONTH,
            encryption_key=shared.key,
        )
        worker = lambda_.DockerImageFunction(
            self,
            "Consolidator",
            code=lambda_.DockerImageCode.from_image_asset(
                str(source),
                cmd=["postmortem_consolidation.handlers.consolidator_handler"],
            ),
            description="Distills closed incident windows into procedural memory",
            timeout=Duration.minutes(10),
            memory_size=1024,
            reserved_concurrent_executions=1,
            log_group=worker_logs,
            vpc=shared.vpc,
            vpc_subnets=vpc_subnets,
            security_groups=[shared.crdb_security_group],
            environment={
                "ARTIFACTS_BUCKET": shared.artifacts.bucket_name,
                "CONSOLIDATION_DATABASE_SECRET_ARN": (
                    shared.consolidator_secret.secret_arn
                ),
                "CONSOLIDATION_MODEL_MODE": consolidation_model_mode,
                "CONSOLIDATION_MODEL_ID": model_id,
                "CONSOLIDATION_EMBEDDING_MODEL_ID": EMBEDDING_MODEL_ID,
                "RUNBOOK_REINFORCEMENTS_TO_ACTIVATE": "2",
                # Guardrail screens the distillation output (R6).
                "POSTMORTEM_GUARDRAIL_ID": shared.guardrail.attr_guardrail_id,
                "POSTMORTEM_GUARDRAIL_VERSION": (
                    shared.guardrail_version.attr_version
                ),
                "POSTMORTEM_DB_SSLMODE": "verify-full",
            },
        )
        worker.add_event_source(
            event_sources.SqsEventSource(
                queue,
                batch_size=10,
                report_batch_item_failures=True,
            )
        )
        shared.artifacts.grant_read_write(worker)
        shared.consolidator_secret.grant_read(worker)
        for statement in bedrock_invoke_statements(
            self.region,
            self.account,
            [model_id, EMBEDDING_MODEL_ID],
            shared.guardrail_arn,
        ):
            worker.add_to_role_policy(statement)

        webhook = apigwv2.HttpApi(
            self,
            "ChangefeedWebhook",
            description="Fast-ack endpoint for CockroachDB episodic-event changefeeds",
        )
        webhook.add_routes(
            path="/changefeed",
            methods=[apigwv2.HttpMethod.POST],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "ChangefeedReceiverIntegration",
                receiver,
            ),
        )

        nightly = events.Rule(
            self,
            "NightlyConsolidation",
            description="Sleep-time fallback that closes and processes pending episodes nightly",
            schedule=events.Schedule.cron(minute="15", hour="3"),
        )
        scheduler_dead_letter_queue = sqs.Queue(
            self,
            "NightlyConsolidationDeadLetterQueue",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=shared.key,
            enforce_ssl=True,
            retention_period=Duration.days(14),
        )
        nightly.add_target(
            event_targets.LambdaFunction(
                worker,
                retry_attempts=2,
                dead_letter_queue=scheduler_dead_letter_queue,
            )
        )

        dlq_alarm = cloudwatch.Alarm(
            self,
            "ConsolidationDlqDepthAlarm",
            metric=dead_letter_queue.metric_approximate_number_of_messages_visible(
                period=Duration.minutes(1)
            ),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        # Security-relevant alarms (charter principle 12): a spike in receiver
        # errors is the signature of forged/unauthenticated webhook attempts.
        receiver_error_alarm = cloudwatch.Alarm(
            self,
            "ReceiverErrorAlarm",
            metric=receiver.metric_errors(period=Duration.minutes(5)),
            threshold=5,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        consolidator_error_alarm = cloudwatch.Alarm(
            self,
            "ConsolidatorErrorAlarm",
            metric=worker.metric_errors(period=Duration.minutes(5)),
            threshold=3,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )

        CfnOutput(
            self,
            "ChangefeedWebhookUrl",
            value=f"{webhook.api_endpoint}/changefeed",
        )
        CfnOutput(self, "ConsolidationQueueArn", value=queue.queue_arn)
        CfnOutput(self, "ConsolidationDlqArn", value=dead_letter_queue.queue_arn)
        CfnOutput(self, "ConsolidationDlqAlarmArn", value=dlq_alarm.alarm_arn)
        CfnOutput(
            self,
            "ReceiverErrorAlarmArn",
            value=receiver_error_alarm.alarm_arn,
        )
        CfnOutput(
            self,
            "ConsolidatorErrorAlarmArn",
            value=consolidator_error_alarm.alarm_arn,
        )
