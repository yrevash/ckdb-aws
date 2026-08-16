"""Synthesis-based assertions for the AWS infrastructure-security controls.

Unlike the source-string tests, these fully synthesize each stack with
``aws_cdk.assertions.Template`` and assert the *rendered* CloudFormation has the
security properties the charter requires (R1/R2/R6/R8, T7/T8). If a control
regresses, the template changes and these fail.
"""

from __future__ import annotations

import json
import os
import unittest

os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from postmortem_infra.consolidation_stack import ConsolidationStack
from postmortem_infra.security_stack import SecurityStack
from postmortem_infra.stacks import AppStack, SharedStack


# CDK context the app now REQUIRES (audit B2/B4): the fail-fast in SharedStack /
# AppStack refuses to synth a deploy that cannot work. Supplying it here is the
# test harness playing the role of `cdk deploy -c ...`; it must NOT be moved into
# cdk.json, which would restore the silent default the audit removed.
_DEFAULT_TEST_CONTEXT = {
    "agent_image_uri": "000000000000.dkr.ecr.us-east-1.amazonaws.com/postmortem-agent:test",
    "crdb_egress_mode": "privatelink",
    "crdb_privatelink_service_name": "com.amazonaws.vpce.us-east-1.vpce-svc-EXAMPLE",
}


def _synth(**context_overrides) -> dict[str, Template]:
    """Synthesize all four stacks under ``_DEFAULT_TEST_CONTEXT``.

    Overrides are merged on top; passing ``key=None`` DELETES that key, which is
    how the fail-fast tests express "this required context is absent".
    """

    context = {**_DEFAULT_TEST_CONTEXT, **context_overrides}
    context = {key: value for key, value in context.items() if value is not None}
    app = cdk.App(context=context)
    env = cdk.Environment(region="us-east-1")
    security = SecurityStack(app, "PostmortemSecurity", env=env)
    shared = SharedStack(app, "PostmortemShared", env=env)
    appstack = AppStack(
        app,
        "PostmortemApp",
        shared=shared,
        agent_image_uri=context.get("agent_image_uri"),
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


def _subnet_types(template: Template) -> list[str]:
    """CDK tags every subnet with aws-cdk:subnet-type Public/Private/Isolated."""

    types = []
    for subnet in template.find_resources("AWS::EC2::Subnet").values():
        for tag in subnet.get("Properties", {}).get("Tags", []) or []:
            if tag.get("Key") == "aws-cdk:subnet-type":
                types.append(tag.get("Value"))
    return types


def _default_routes_via_nat(template: Template) -> list[dict]:
    return [
        route.get("Properties", {})
        for route in template.find_resources("AWS::EC2::Route").values()
        if route.get("Properties", {}).get("DestinationCidrBlock") == "0.0.0.0/0"
        and "NatGatewayId" in route.get("Properties", {})
    ]


def _crdb_sg_egress(template: Template) -> list[dict]:
    """Egress rules on the CockroachDB *client* security group only.

    Scoped deliberately: the eight AWS interface endpoints each get a CDK-default
    allow-all-outbound SG, but those are attached to endpoint ENIs, not to
    compute, and predate the egress-mode switch. The SG this project authors --
    the one the consolidator Lambda and (by peering) the Fargate service use --
    is the one whose rules the mode actually changes (audit B2).
    """

    rules: list[dict] = []
    for sg in template.find_resources("AWS::EC2::SecurityGroup").values():
        props = sg.get("Properties", {})
        if "CockroachDB" not in str(props.get("GroupDescription", "")):
            continue
        rules.extend(props.get("SecurityGroupEgress", []) or [])
    return rules


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
        # 5 secrets after audit B1: MCP reader token, SQL reader DSN, SQL writer
        # DSN, consolidator DSN, changefeed webhook (charter R2/R7).
        self.assertGreaterEqual(len(secrets), 5)
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
    """T8 in the DEFAULT (privatelink) egress mode: private compute, no public
    S3, WAF on the console, no NAT egress. The opt-in ``crdb_egress_mode=public``
    relaxation is asserted separately in PublicEgressModeTests.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = _synth()

    def test_privatelink_mode_has_no_nat_gateways(self) -> None:
        self.templates["shared"].resource_count_is("AWS::EC2::NatGateway", 0)

    def test_isolated_subnets_and_interface_endpoints_exist(self) -> None:
        template = self.templates["shared"]
        # Interface endpoints (PrivateLink) for the services private compute needs.
        endpoints = template.find_resources("AWS::EC2::VPCEndpoint")
        # 8 AWS interface endpoints + the S3 gateway endpoint + the CockroachDB
        # PrivateLink endpoint, which is now mandatory in privatelink mode
        # (audit B2: it used to be silently optional).
        self.assertGreaterEqual(len(endpoints), 10)
        service_names = [
            e.get("Properties", {}).get("ServiceName") for e in endpoints.values()
        ]
        self.assertIn(
            _DEFAULT_TEST_CONTEXT["crdb_privatelink_service_name"],
            service_names,
            "privatelink mode did not create the CockroachDB interface endpoint",
        )
        template.resource_count_is("AWS::EC2::FlowLog", 1)

    def test_privatelink_mode_has_no_egress_off_the_vpc(self) -> None:
        # T8's actual claim: in the default mode the CockroachDB client SG's
        # every egress rule is pinned to the VPC CIDR. Asserting "0 NAT
        # gateways" alone would not catch a rule widened to 0.0.0.0/0 (audit B2).
        rules = _crdb_sg_egress(self.templates["shared"])
        self.assertTrue(rules, "CockroachDB client security group not found")
        for rule in rules:
            self.assertNotEqual(
                rule.get("CidrIp"),
                "0.0.0.0/0",
                msg=f"privatelink mode opened egress to the internet: {rule}",
            )

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


class RoleScopedDatabaseCredentialTests(unittest.TestCase):
    """audit B1 / charter R7-T2: the Fargate task must receive two DISTINCT SQL
    DSNs. runtime.py fails closed (RoleScopeViolation) when the reader and writer
    DSNs are identical or share a username -- injecting one secret twice, or only
    POSTMORTEM_DATABASE_URL, crashloops the task before /healthz ever answers.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = _synth()

    def _container(self) -> dict:
        (definition,) = (
            self.templates["app"]
            .find_resources("AWS::ECS::TaskDefinition")
            .values()
        )
        (container,) = definition["Properties"]["ContainerDefinitions"]
        return container

    def _secret_sources(self) -> dict[str, str]:
        return {
            entry["Name"]: json.dumps(entry["ValueFrom"], sort_keys=True)
            for entry in self._container()["Secrets"]
        }

    def test_reader_and_writer_dsns_come_from_two_different_secrets(self) -> None:
        sources = self._secret_sources()
        self.assertIn("POSTMORTEM_READER_DATABASE_URL", sources)
        self.assertIn("POSTMORTEM_WRITER_DATABASE_URL", sources)
        self.assertNotEqual(
            sources["POSTMORTEM_READER_DATABASE_URL"],
            sources["POSTMORTEM_WRITER_DATABASE_URL"],
            "both DSNs resolve to one secret -- audit C3: role separation is "
            "cosmetic and runtime.py refuses to start",
        )

    def test_database_url_is_still_injected_for_config_validate(self) -> None:
        # config.py validate() still requires POSTMORTEM_DATABASE_URL to be
        # non-empty in aws mode; dropping it trades one crashloop for another.
        self.assertIn("POSTMORTEM_DATABASE_URL", self._secret_sources())

    def test_task_role_reads_exactly_its_own_secrets(self) -> None:
        reads = 0
        for statement in _iter_policy_statements(self.templates["app"]):
            actions = _as_list(statement.get("Action"))
            if "secretsmanager:GetSecretValue" in actions:
                resources = _as_list(statement.get("Resource"))
                self.assertNotIn("*", resources)
                reads += 1
        self.assertGreaterEqual(reads, 1)

    def test_the_sql_reader_secret_is_its_own_cmk_encrypted_secret(self) -> None:
        # audit B1 root cause: CockroachReaderSecret holds the Managed-MCP
        # URL/token, NOT a SQL DSN. The reader pool needs a secret of its own.
        shared = self.templates["shared"]
        descriptions = [
            secret.get("Properties", {}).get("Description", "")
            for secret in shared.find_resources(
                "AWS::SecretsManager::Secret"
            ).values()
        ]
        self.assertTrue(
            any("postmortem_agent_reader" in text for text in descriptions),
            "no dedicated read-only SQL DSN secret in SharedStack",
        )
        shared.has_output(
            "SqlReaderSecretArn", Match.object_like({"Value": Match.any_value()})
        )


class SynthFailFastTests(unittest.TestCase):
    """audit B2/B4: a deploy that provably cannot work must fail at synth, not
    deploy green and then never pass a health check.
    """

    def test_app_stack_refuses_to_synth_without_an_agent_image(self) -> None:
        app = cdk.App(context=_DEFAULT_TEST_CONTEXT)
        env = cdk.Environment(region="us-east-1")
        shared = SharedStack(app, "PostmortemShared", env=env)
        with self.assertRaises(ValueError) as raised:
            AppStack(
                app,
                "PostmortemApp",
                shared=shared,
                agent_image_uri=None,
                console_origin=None,
                env=env,
            )
        self.assertIn("agent_image_uri", str(raised.exception))

    def test_privatelink_mode_refuses_to_synth_without_a_service_name(self) -> None:
        with self.assertRaises(ValueError) as raised:
            _synth(crdb_privatelink_service_name=None)
        self.assertIn("crdb_privatelink_service_name", str(raised.exception))

    def test_the_default_mode_is_the_fail_closed_one(self) -> None:
        # No crdb_egress_mode at all must behave exactly like privatelink: the
        # secure posture is what you get by forgetting to choose (audit B2).
        with self.assertRaises(ValueError) as raised:
            _synth(crdb_egress_mode=None, crdb_privatelink_service_name=None)
        self.assertIn("crdb_privatelink_service_name", str(raised.exception))

    def test_unknown_egress_mode_is_refused(self) -> None:
        with self.assertRaises(ValueError) as raised:
            _synth(crdb_egress_mode="whatever")
        self.assertIn("crdb_egress_mode", str(raised.exception))


class PublicEgressModeTests(unittest.TestCase):
    """audit B2: ``crdb_egress_mode=public`` is the OPT-IN relaxation for teams
    whose CockroachDB Cloud tier has no PrivateLink. It is never the default; it
    widens egress and nothing else (charter T8 -- documented in
    docs/security/01-aws-infrastructure-security.md).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.templates = _synth(
            crdb_egress_mode="public", crdb_privatelink_service_name=None
        )

    def test_public_mode_provisions_nat_and_routes_private_subnets_through_it(
        self,
    ) -> None:
        shared = self.templates["shared"]
        self.assertGreaterEqual(len(shared.find_resources("AWS::EC2::NatGateway")), 1)
        self.assertTrue(_default_routes_via_nat(shared))

    def test_public_mode_has_no_isolated_compute_subnets(self) -> None:
        types = _subnet_types(self.templates["shared"])
        self.assertIn("Private", types)
        self.assertNotIn("Isolated", types)

    def test_privatelink_mode_has_isolated_compute_and_no_nat_route(self) -> None:
        shared = _synth()["shared"]
        types = _subnet_types(shared)
        self.assertIn("Isolated", types)
        self.assertNotIn("Private", types)
        self.assertEqual(_default_routes_via_nat(shared), [])

    def test_public_mode_keeps_the_fargate_task_without_a_public_ip(self) -> None:
        self.templates["app"].has_resource_properties(
            "AWS::ECS::Service",
            {
                "NetworkConfiguration": {
                    "AwsvpcConfiguration": {"AssignPublicIp": "DISABLED"}
                }
            },
        )

    def test_public_mode_still_has_no_wildcard_iam(self) -> None:
        for name, template in self.templates.items():
            for statement in _iter_policy_statements(template):
                if statement.get("Effect") != "Allow":
                    continue
                self.assertNotIn("*", _as_list(statement.get("Action")), msg=name)

    def test_public_mode_widens_only_the_sql_port(self) -> None:
        # The whole security claim of the opt-in mode: SQL (26257) is the ONLY
        # thing that may leave the VPC. Everything else still rides the in-VPC
        # interface endpoints. A widened 443 or an all-traffic rule here would
        # turn a documented relaxation into general internet egress.
        rules = _crdb_sg_egress(self.templates["shared"])
        self.assertTrue(rules, "CockroachDB client security group not found")
        off_vpc_ports = {
            (rule.get("FromPort"), rule.get("ToPort"))
            for rule in rules
            if isinstance(rule.get("CidrIp"), str)
        }
        self.assertEqual(off_vpc_ports, {(26257, 26257)})

    def test_public_mode_egress_narrows_to_an_operator_allowlist(self) -> None:
        # -c crdb_egress_cidrs=... must actually replace the 0.0.0.0/0 default,
        # so an operator who knows their cluster's addresses can close the hole.
        templates = _synth(
            crdb_egress_mode="public",
            crdb_privatelink_service_name=None,
            crdb_egress_cidrs="203.0.113.10/32",
        )
        cidrs = {
            rule.get("CidrIp")
            for rule in _crdb_sg_egress(templates["shared"])
            if rule.get("FromPort") == 26257
        }
        self.assertEqual(cidrs, {"203.0.113.10/32"})

    def test_public_mode_keeps_the_aws_interface_endpoints(self) -> None:
        # Bedrock/Secrets/KMS/SQS/Logs/ECR/STS traffic must NOT start flowing
        # over the NAT gateway just because the mode changed.
        endpoints = self.templates["shared"].find_resources("AWS::EC2::VPCEndpoint")
        self.assertGreaterEqual(len(endpoints), 8)

    def test_public_mode_synthesizes_the_consolidation_lambdas(self) -> None:
        # The Lambdas hardcoded PRIVATE_ISOLATED; if they are not moved onto
        # shared.compute_subnets this synth fails with 'no isolated subnets'.
        self.assertTrue(
            self.templates["consolidation"].find_resources("AWS::Lambda::Function")
        )

    def test_public_mode_declares_itself_in_the_stack_outputs(self) -> None:
        # An auditor reads the posture off the deployed stack, not off a doc.
        self.templates["shared"].has_output(
            "CrdbEgressMode", Match.object_like({"Value": "public"})
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
