#!/usr/bin/env python3
from aws_cdk import App, Environment

from postmortem_infra.consolidation_stack import ConsolidationStack
from postmortem_infra.security_stack import SecurityStack
from postmortem_infra.stacks import AppStack, SharedStack


app = App()
environment = Environment(
    account=app.node.try_get_context("account"),
    region=app.node.try_get_context("region") or "us-east-1",
)

SecurityStack(app, "PostmortemSecurity", env=environment)

shared = SharedStack(app, "PostmortemShared", env=environment)
AppStack(
    app,
    "PostmortemApp",
    shared=shared,
    agent_image_uri=app.node.try_get_context("agent_image_uri"),
    console_origin=app.node.try_get_context("console_origin"),
    env=environment,
)
ConsolidationStack(
    app,
    "PostmortemConsolidation",
    shared=shared,
    consolidation_model_id=app.node.try_get_context("consolidation_model_id"),
    consolidation_model_mode=(
        app.node.try_get_context("consolidation_model_mode") or "bedrock"
    ),
    env=environment,
)

app.synth()
