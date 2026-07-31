import os
import sys

# Ensure the infra package root is importable when pytest collects tests/ so the
# synthesis-based security tests can `import postmortem_infra`.
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION", "1")
