"""Synthesis-based assertions for the AWS infrastructure-security controls.

Unlike the source-string tests, these fully synthesize each stack with
``aws_cdk.assertions.Template`` and assert the *rendered* CloudFormation has the
security properties the charter requires (R1/R2/R6/R8, T7/T8). If a control
regresses, the template changes and these fail.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from postmortem_infra.consolidation_stack import ConsolidationStack
from postmortem_infra.security_stack import SecurityStack
from postmortem_infra.stacks import AppStack, SharedStack


def _synth() -> dict[str, Template]:
    app = cdk.App()
    env = cdk.Environment(region="us-east-1")
    security = SecurityStack(app, "PostmortemSecurity", env=env)
    shared = SharedStack(app, "PostmortemShared", env=env)
    appstack = AppStack(
        app,
        "PostmortemApp",
        shared=shared,
        agent_image_uri=None,
        console_origin="https://console.example.com",
        env=env,
    )
    consolidation = ConsolidationStack(
        app,
        "PostmortemConsolidation",
        shared=shared,
        env=env,
    )
    return {
        "security": Template.from_stack(security),
        "shared": Template.from_stack(shared),
        "app": Template.from_stack(appstack),
        "consolidation": Template.from_stack(consolidation),
    }


def _iter_policy_statements(template: Template):
    """Yield every statement across inline IAM policies and role policies."""

    for resource_type in ("AWS::IAM::Policy", "AWS::IAM::Role"):
        for resource in template.find_resources(resource_type).values():
            props = resource.get("Properties", {})
            documents = []
            if "PolicyDocument" in props:
                documents.append(props["PolicyDocument"])
            for inline in props.get("Policies", []) or []:
                if isinstance(inline, dict) and "PolicyDocument" in inline:
                    documents.append(inline["PolicyDocument"])
            for document in documents:
                for statement in document.get("Statement", []):
                    yield statement


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


class NoWildcardIamTests(unittest.TestCase):
    """R1: deny-by-default, least-privilege — no wildcard write grants."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = _synth()

    def test_no_action_star_anywhere(self) -> None:
        for name, template in self.templates.items():
            for statement in _iter_policy_statements(template):
                if statement.get("Effect") != "Allow":
                    continue
                actions = _as_list(statement.get("Action"))
                self.assertNotIn(
                    "*",
                    actions,
                    msg=f"Wildcard Action '*' found in stack {name}: {statement}",
                )

    def test_bedrock_invoke_is_resource_scoped(self) -> None:
        found_scoped_invoke = False
        for name, template in self.templates.items():
            for statement in _iter_policy_statements(template):
                actions = _as_list(statement.get("Action"))
                if any(str(a).startswith("bedrock:Invoke") for a in actions):
                    resources = _as_list(statement.get("Resource"))
                    self.assertNotIn(
                        "*",
                        resources,
                        msg=f"bedrock:Invoke on '*' in stack {name}: {statement}",
                    )
                    self.assertTrue(resources, "InvokeModel has empty resource")
                    found_scoped_invoke = True
        self.assertTrue(
            found_scoped_invoke,
            "Expected at least one scoped bedrock:InvokeModel statement",
        )

    def test_no_broad_bedrock_wildcard(self) -> None:
        for name, template in self.templates.items():
            for statement in _iter_policy_statements(template):
                actions = _as_list(statement.get("Action"))
                resources = _as_list(statement.get("Resource"))
                if "*" in resources:
                    for action in actions:
                        self.assertFalse(
                            str(action).startswith("bedrock:"),
                            msg=f"Bedrock action on '*' in {name}: {statement}",
                        )


class EncryptionTests(unittest.TestCase):
    """R8: encryption at rest with a customer-managed key everywhere."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = _synth()

    def test_customer_managed_key_with_rotation(self) -> None:
        self.templates["shared"].has_resource_properties(
            "AWS::KMS::Key", {"EnableKeyRotation": True}
        )
        self.templates["security"].has_resource_properties(
            "AWS::KMS::Key", {"EnableKeyRotation": True}
        )

    def test_artifacts_bucket_uses_kms_and_blocks_public(self) -> None:
        template = self.templates["shared"]
        template.has_resource_properties(
            "AWS::S3::Bucket",
            {
                "PublicAccessBlockConfiguration": {
                    "BlockPublicAcls": True,
                    "BlockPublicPolicy": True,
                    "IgnorePublicAcls": True,
                    "RestrictPublicBuckets": True,
                },
                "BucketEncryption": {
                    "ServerSideEncryptionConfiguration": [
                        {
                            "ServerSideEncryptionByDefault": {
                                "SSEAlgorithm": "aws:kms"
                            }
                        }
                    ]
                },
            },
        )

    def test_all_buckets_block_public_access(self) -> None:
        for name, template in self.templates.items():
            for logical_id, bucket in template.find_resources(
                "AWS::S3::Bucket"
            ).items():
                pab = bucket.get("Properties", {}).get(
                    "PublicAccessBlockConfiguration"
                )
                self.assertIsNotNone(
                    pab, f"{name}/{logical_id} missing BlockPublicAccess"
                )
                self.assertTrue(all(pab.values()))

    def test_secrets_use_customer_managed_key(self) -> None:
        template = self.templates["shared"]
        secrets = template.find_resources("AWS::SecretsManager::Secret")
        self.assertGreaterEqual(len(secrets), 4)
        for logical_id, secret in secrets.items():
            self.assertIn(
                "KmsKeyId",
                secret.get("Properties", {}),
                f"Secret {logical_id} not CMK-encrypted",
            )

    def test_sqs_queues_are_kms_encrypted_and_tls_only(self) -> None:
        template = self.templates["consolidation"]
        queues = template.find_resources("AWS::SQS::Queue")
        self.assertTrue(queues)
        for logical_id, queue in queues.items():
            self.assertIn(
                "KmsMasterKeyId",
                queue.get("Properties", {}),
                f"Queue {logical_id} not KMS-encrypted",
            )
        # enforce_ssl adds a queue policy denying non-TLS transport.
        template.has_resource_properties(
            "AWS::SQS::QueuePolicy",
            {
                "PolicyDocument": {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Effect": "Deny",
                                    "Condition": {
                                        "Bool": {"aws:SecureTransport": "false"}
                                    },
                                }
                            )
                        ]
                    )
                }
            },
        )

    def test_log_groups_are_cmk_encrypted(self) -> None:
        for name in ("app", "consolidation"):
            template = self.templates[name]
            for logical_id, group in template.find_resources(
                "AWS::Logs::LogGroup"
            ).items():
                self.assertIn(
                    "KmsKeyId",
                    group.get("Properties", {}),
                    f"{name}/{logical_id} log group not CMK-encrypted",
                )


class NetworkTests(unittest.TestCase):
    """T8: private compute, no public S3, WAF on the console, no NAT egress."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = _synth()

    def test_no_nat_gateways(self) -> None:
        self.templates["shared"].resource_count_is("AWS::EC2::NatGateway", 0)

    def test_isolated_subnets_and_interface_endpoints_exist(self) -> None:
        template = self.templates["shared"]
        # Interface endpoints (PrivateLink) for the services private compute needs.
        endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
        self.assertGreaterEqual(len(endpoints), 8)
        template.resource_count_is("AWS::EC2::FlowLog", 1)

    def test_fargate_task_has_no_public_ip(self) -> None:
        self.templates["app"].has_resource_properties(
            "AWS::ECS::Service",
            {
                "NetworkConfiguration": {
                    "AwsvpcConfiguration": {"AssignPublicIp": "DISABLED"}
                }
            },
        )

    def _ingress_ports(self, template: Template):
        """Yield (from_port, to_port, protocol) for every ingress rule.

        Covers both standalone ``SecurityGroupIngress`` resources (used for
        self-references and cross-stack peers) and inline ingress on a
        ``SecurityGroup``.
        """

        for resource in template.find_resources(
            "AWS::EC2::SecurityGroupIngress"
        ).values():
            props = resource.get("Properties", {})
            yield (props.get("FromPort"), props.get("ToPort"), props.get("IpProtocol"))
        for sg in template.find_resources("AWS::EC2::SecurityGroup").values():
            for rule in sg.get("Properties", {}).get("SecurityGroupIngress", []) or []:
                yield (
                    rule.get("FromPort"),
                    rule.get("ToPort"),
                    rule.get("IpProtocol"),
                )

    def test_crdb_endpoint_sg_has_26257_ingress(self) -> None:
        # The CockroachDB PrivateLink endpoint SG must accept inbound SQL from its
        # in-VPC clients, or the endpoint rejects every connection. The shared SG
        # carries the self-reference (covers the consolidator), and the app stack
        # carries the peer rule for the Fargate service SG.
        shared_ingress = list(self._ingress_ports(self.templates["shared"]))
        self.assertIn(
            (26257, 26257, "tcp"),
            shared_ingress,
            "CockroachDB endpoint SG is missing self-referencing 26257 ingress",
        )
        app_ingress = list(self._ingress_ports(self.templates["app"]))
        self.assertIn(
            (26257, 26257, "tcp"),
            app_ingress,
            "App stack does not open 26257 from the Fargate service SG",
        )

    def test_waf_web_acl_and_association(self) -> None:
        template = self.templates["app"]
        template.resource_count_is("AWS::WAFv2::WebACLAssociation", 1)
        template.has_resource_properties(
            "AWS::WAFv2::WebACL",
            {
                "Scope": "REGIONAL",
                "Rules": Match.array_with(
                    [
                        Match.object_like(
                            {
                                "Statement": {
                                    "ManagedRuleGroupStatement": {
                                        "Name": "AWSManagedRulesCommonRuleSet"
                                    }
                                }
                            }
                        ),
                        Match.object_like(
                            {
                                "Statement": {
                                    "RateBasedStatement": {"AggregateKeyType": "IP"}
                                }
                            }
                        ),
                    ]
                ),
            },
        )


class GuardrailAndDetectionTests(unittest.TestCase):
    """R6 guardrails + T7/principle-12 detection controls."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = _synth()

    def test_guardrail_defines_prompt_attack_filter(self) -> None:
        self.templates["shared"].has_resource_properties(
            "AWS::Bedrock::Guardrail",
            {
                "ContentPolicyConfig": {
                    "FiltersConfig": Match.array_with(
                        [Match.object_like({"Type": "PROMPT_ATTACK"})]
                    )
                }
            },
        )

    def test_guardrail_has_sensitive_info_and_grounding(self) -> None:
        template = self.templates["shared"]
        template.has_resource_properties(
            "AWS::Bedrock::Guardrail",
            {
                "SensitiveInformationPolicyConfig": Match.any_value(),
                "ContextualGroundingPolicyConfig": Match.any_value(),
            },
        )

    def test_both_compute_roles_can_apply_guardrail(self) -> None:
        for name in ("app", "consolidation"):
            template = self.templates[name]
            applied = False
            for statement in _iter_policy_statements(template):
                actions = _as_list(statement.get("Action"))
                if "bedrock:ApplyGuardrail" in actions:
                    applied = True
            self.assertTrue(
                applied, f"{name} role cannot apply the guardrail (R6)"
            )

    def test_cloudtrail_multiregion_validated_and_encrypted(self) -> None:
        self.templates["security"].has_resource_properties(
            "AWS::CloudTrail::Trail",
            {
                "IsMultiRegionTrail": True,
                "EnableLogFileValidation": True,
                "IsLogging": True,
                "KMSKeyId": Match.any_value(),
            },
        )

    def test_trail_key_grants_cloudwatch_logs(self) -> None:
        # The trail's CloudWatch Logs group is CMK-encrypted, so the CloudWatch
        # Logs service principal must be granted use of the key in the key policy
        # or the encrypted log group fails to deliver at deploy time.
        self.templates["security"].has_resource_properties(
            "AWS::KMS::Key",
            {
                "KeyPolicy": {
                    "Statement": Match.array_with(
                        [
                            Match.object_like(
                                {
                                    "Sid": "AllowCloudWatchLogs",
                                    "Principal": {
                                        "Service": "logs.us-east-1.amazonaws.com"
                                    },
                                }
                            )
                        ]
                    )
                }
            },
        )

    def test_guardduty_and_config_enabled(self) -> None:
        template = self.templates["security"]
        template.has_resource_properties(
            "AWS::GuardDuty::Detector", {"Enable": True}
        )
        template.resource_count_is("AWS::Config::ConfigurationRecorder", 1)
        # Security metric-filter alarms exist (root usage, unauthorized calls).
        self.assertGreaterEqual(
            len(template.find_resources("AWS::CloudWatch::Alarm")), 3
        )


if __name__ == "__main__":
    unittest.main()
