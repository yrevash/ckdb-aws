from __future__ import annotations

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_cloudtrail as cloudtrail,
    aws_cloudwatch as cloudwatch,
    aws_config as config,
    aws_guardduty as guardduty,
    aws_iam as iam,
    aws_kms as kms,
    aws_logs as logs,
    aws_s3 as s3,
)
from constructs import Construct


class SecurityStack(Stack):
    """Account-wide detection plane: CloudTrail, GuardDuty, Config, alarms.

    GuardDuty detectors and the Config recorder are account/region singletons —
    they synthesize cleanly here and are marked [deploy-time] in the doc because
    a real account may already have them enabled.
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs: object) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ---- CloudTrail: management events, log-file validation, CMK, alarms --
        trail_key = kms.Key(
            self,
            "TrailKey",
            description="CMK encrypting the Postmortem CloudTrail trail",
            enable_key_rotation=True,
            removal_policy=RemovalPolicy.RETAIN,
            alias="alias/postmortem-cloudtrail",
        )
        # CloudTrail must be able to encrypt log files and describe the key.
        trail_key.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowCloudTrailEncrypt",
                principals=[iam.ServicePrincipal("cloudtrail.amazonaws.com")],
                actions=["kms:GenerateDataKey*", "kms:DescribeKey"],
                resources=["*"],
                conditions={
                    "StringLike": {
                        "kms:EncryptionContext:aws:cloudtrail:arn": (
                            f"arn:aws:cloudtrail:*:{self.account}:trail/*"
                        )
                    }
                },
            )
        )
        # The trail also fans out to a CMK-encrypted CloudWatch Logs group. Logs
        # is not an IAM identity, so — like SharedStack's key — the CloudWatch
        # Logs service principal must be granted use of the key directly in the
        # key policy, or the encrypted log group fails to deliver at deploy time.
        trail_key.add_to_resource_policy(
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

        trail_log_group = logs.LogGroup(
            self,
            "TrailLogGroup",
            retention=logs.RetentionDays.ONE_YEAR,
            encryption_key=trail_key,
            removal_policy=RemovalPolicy.RETAIN,
        )
        # Explicit, locked-down trail bucket — do not rely on the construct
        # default, which omits the public-access block (T8 / R10).
        trail_bucket = s3.Bucket(
            self,
            "TrailBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        trail = cloudtrail.Trail(
            self,
            "PostmortemTrail",
            bucket=trail_bucket,
            is_multi_region_trail=True,
            include_global_service_events=True,
            enable_file_validation=True,
            encryption_key=trail_key,
            send_to_cloud_watch_logs=True,
            cloud_watch_log_group=trail_log_group,
        )

        # Security alarms driven off CloudTrail → CloudWatch metric filters
        # (charter principle 12 / R10: alert on security-relevant events).
        self._alarm_on_pattern(
            "UnauthorizedApiCalls",
            trail_log_group,
            '{ ($.errorCode = "*UnauthorizedOperation") || '
            '($.errorCode = "AccessDenied*") }',
            "PostmortemUnauthorizedApiCalls",
        )
        self._alarm_on_pattern(
            "RootAccountUsage",
            trail_log_group,
            '{ $.userIdentity.type = "Root" && '
            '$.userIdentity.invokedBy NOT EXISTS && '
            '$.eventType != "AwsServiceEvent" }',
            "PostmortemRootAccountUsage",
        )
        self._alarm_on_pattern(
            "IamPolicyChanges",
            trail_log_group,
            "{ ($.eventName = DeleteGroupPolicy) || "
            "($.eventName = DeleteRolePolicy) || "
            "($.eventName = DeleteUserPolicy) || "
            "($.eventName = PutGroupPolicy) || "
            "($.eventName = PutRolePolicy) || "
            "($.eventName = PutUserPolicy) || "
            "($.eventName = CreatePolicy) || "
            "($.eventName = AttachRolePolicy) || "
            "($.eventName = DetachRolePolicy) }",
            "PostmortemIamPolicyChanges",
        )

        # ---- GuardDuty (threat detection) ------------------------------------
        detector = guardduty.CfnDetector(
            self,
            "GuardDutyDetector",
            enable=True,
            finding_publishing_frequency="FIFTEEN_MINUTES",
        )

        # ---- AWS Config (continuous compliance) ------------------------------
        # [deploy-time] SSE-S3 (AES-256) is retained here deliberately rather than
        # the CMK: the bucket already blocks all public access, enforces TLS in
        # transit, and is versioned, and its only contents are AWS Config's own
        # compliance snapshots. Moving to SSE-KMS would additionally require
        # granting the AWS Config delivery principal kms:GenerateDataKey on the
        # key (otherwise delivery silently fails at runtime), so the CMK upgrade
        # is deferred to a deploy-time change with that grant in place.
        config_bucket = s3.Bucket(
            self,
            "ConfigBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        config_role = iam.Role(
            self,
            "ConfigRole",
            assumed_by=iam.ServicePrincipal("config.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWS_ConfigRole"
                )
            ],
        )
        config_bucket.grant_read_write(config_role)
        config_bucket.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AWSConfigBucketDelivery",
                principals=[iam.ServicePrincipal("config.amazonaws.com")],
                actions=["s3:PutObject"],
                resources=[config_bucket.arn_for_objects("*")],
                conditions={
                    "StringEquals": {
                        "s3:x-amz-acl": "bucket-owner-full-control"
                    }
                },
            )
        )

        recorder = config.CfnConfigurationRecorder(
            self,
            "ConfigRecorder",
            role_arn=config_role.role_arn,
            recording_group=config.CfnConfigurationRecorder.RecordingGroupProperty(
                all_supported=True,
                include_global_resource_types=True,
            ),
        )
        delivery_channel = config.CfnDeliveryChannel(
            self,
            "ConfigDeliveryChannel",
            s3_bucket_name=config_bucket.bucket_name,
        )
        delivery_channel.add_dependency(recorder)

        # Managed Config rules covering the charter's data-protection posture.
        managed_rules = {
            "S3PublicReadProhibited": config.ManagedRuleIdentifiers.S3_BUCKET_PUBLIC_READ_PROHIBITED,
            "S3PublicWriteProhibited": config.ManagedRuleIdentifiers.S3_BUCKET_PUBLIC_WRITE_PROHIBITED,
            "S3EncryptionEnabled": config.ManagedRuleIdentifiers.S3_BUCKET_SERVER_SIDE_ENCRYPTION_ENABLED,
            "CloudTrailEnabled": config.ManagedRuleIdentifiers.CLOUD_TRAIL_ENABLED,
            "EncryptedVolumes": config.ManagedRuleIdentifiers.EBS_ENCRYPTED_VOLUMES,
            "IamNoInlineUserPolicy": config.ManagedRuleIdentifiers.IAM_USER_NO_POLICIES_CHECK,
        }
        for rule_id, identifier in managed_rules.items():
            rule = config.ManagedRule(
                self,
                rule_id,
                identifier=identifier,
            )
            rule.node.add_dependency(recorder)

        CfnOutput(self, "TrailArn", value=trail.trail_arn)
        CfnOutput(self, "TrailKeyArn", value=trail_key.key_arn)
        CfnOutput(self, "GuardDutyDetectorId", value=detector.ref)
        CfnOutput(self, "ConfigBucketName", value=config_bucket.bucket_name)

    def _alarm_on_pattern(
        self,
        construct_id: str,
        log_group: logs.LogGroup,
        pattern: str,
        metric_name: str,
    ) -> None:
        metric_filter = logs.MetricFilter(
            self,
            f"{construct_id}Filter",
            log_group=log_group,
            filter_pattern=logs.FilterPattern.literal(pattern),
            metric_namespace="Postmortem/Security",
            metric_name=metric_name,
            metric_value="1",
            default_value=0,
        )
        cloudwatch.Alarm(
            self,
            f"{construct_id}Alarm",
            metric=metric_filter.metric(
                statistic="Sum", period=Duration.minutes(5)
            ),
            threshold=1,
            evaluation_periods=1,
            datapoints_to_alarm=1,
            comparison_operator=(
                cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD
            ),
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
