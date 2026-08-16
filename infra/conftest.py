import os
import sys

# Ensure the infra package root is importable when pytest collects tests/ so the
# synthesis-based security tests can `import postmortem_infra`.
sys.path.insert(0, os.path.dirname(__file__))
# CDK context is supplied per-test by tests/test_security.py::_DEFAULT_TEST_CONTEXT;
# cdk.json's context block is merged by the CDK CLI and is never visible here, and
# adding context at this level would silently defeat the audit B2/B4 fail-fast
# tests, which exist to assert that required context is ABSENT.
os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")
