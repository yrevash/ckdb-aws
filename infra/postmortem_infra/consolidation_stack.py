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

from .stacks import SharedStack


class ConsolidationStack(Stack):
    """Changefeed → FIFO SQS → sleep-time procedural-memory consolidation."""

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

        dead_letter_queue = sqs.Queue(
            self,
            "ConsolidationDeadLetterQueue",
            fifo=True,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            retention_period=Duration.days(14),
        )
        queue = sqs.Queue(
            self,
            "ConsolidationQueue",
            fifo=True,
            content_based_deduplication=False,
            deduplication_scope=sqs.DeduplicationScope.QUEUE,
            fifo_throughput_limit=sqs.FifoThroughputLimit.PER_QUEUE,
            encryption=sqs.QueueEncryption.SQS_MANAGED,
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
            environment={
                "CONSOLIDATION_QUEUE_URL": queue.queue_url,
                "CHANGEFEED_WEBHOOK_SECRET_ARN": (
                    shared.changefeed_webhook_secret.secret_arn
                ),
            },
        )
        queue.grant_send_messages(receiver)
        shared.changefeed_webhook_secret.grant_read(receiver)

        worker_logs = logs.LogGroup(
            self,
            "ConsolidatorLogs",
            retention=logs.RetentionDays.ONE_MONTH,
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
            environment={
                "ARTIFACTS_BUCKET": shared.artifacts.bucket_name,
                "CONSOLIDATION_DATABASE_SECRET_ARN": (
                    shared.consolidator_secret.secret_arn
                ),
                "CONSOLIDATION_MODEL_MODE": consolidation_model_mode,
                "CONSOLIDATION_MODEL_ID": model_id,
                "CONSOLIDATION_EMBEDDING_MODEL_ID": (
                    "amazon.titan-embed-text-v2:0"
                ),
                "RUNBOOK_REINFORCEMENTS_TO_ACTIVATE": "2",
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
        worker.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=["*"],
                conditions={
                    "StringEquals": {
                        "aws:RequestedRegion": self.region,
                    }
                },
            )
        )

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
            encryption=sqs.QueueEncryption.SQS_MANAGED,
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

        CfnOutput(
            self,
            "ChangefeedWebhookUrl",
            value=f"{webhook.api_endpoint}/changefeed",
        )
        CfnOutput(self, "ConsolidationQueueArn", value=queue.queue_arn)
        CfnOutput(self, "ConsolidationDlqArn", value=dead_letter_queue.queue_arn)
        CfnOutput(self, "ConsolidationDlqAlarmArn", value=dlq_alarm.alarm_arn)
