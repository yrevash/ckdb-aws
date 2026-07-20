# 04 — AWS Capabilities (the agentic stack, July 2026)

We must use ≥ 1 AWS service. In 2026 the obvious home for agents is **Amazon Bedrock AgentCore**,
now **GA** with **12 components** (CDK L2 stable). But a lean Lambda + Bedrock path is also valid and
sometimes better for a small team. Both are covered.

## Amazon Bedrock AgentCore (GA, 2026)

A serverless platform to build, deploy, and operate agents at scale with **any framework and any
foundation model**. The 12 GA components:

| Component | What it does | Relevance to us |
|-----------|--------------|-----------------|
| **Runtime** | Serverless agent hosting. Each session = isolated **microVM** (own CPU/mem/fs), prevents cross-session contamination. Real-time **and** long-running workloads up to **8 hours**. | Hosts our agent; the isolation is a production-readiness talking point. |
| **Memory (3 types)** | Managed short-term working memory + long-term memory. **Episodic memory now GA** — agent learns from experience across sessions. Self-managed strategy = full control over extraction/consolidation. | **Decision point:** use AgentCore Memory *or* build memory on CockroachDB. For this hackathon, **CockroachDB must be the memory layer** — so we use CockroachDB as the store and, at most, AgentCore Memory for ephemeral working memory. |
| **Gateway** | Turns APIs/services into agent tools via one MCP interface. Features: **Security Guard, Translation, Composition, Target extensibility, Semantic Tool Selection**, and a built-in **Web Search** connector (MCP). | Can front the CockroachDB MCP + other tools behind one governed gateway. |
| **Identity** | Agent identity + scoped access to AWS/third-party resources. | Maps to per-agent memory scopes + auth. |
| **Policy** | **Bedrock Guardrails** in-policy: screens gateway inputs/outputs for prompt injection, harmful content, sensitive-data exposure. | Safety story for the demo + governance idea. |
| **Observability** | Tracing/metrics for agent runs. | Production-readiness points. |
| **Code Interpreter** | Sandboxed code execution. | Useful for dev-tools / SRE ideas. |
| **Browser** | Managed headless browsing. | Optional. |
| **Harness / Evaluations / Payments / Registry** | Orchestration scaffolding, agent evals, agent payments, agent/tool registry. | Evaluations is nice for showing memory-quality metrics. |

Sources: [AgentCore overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html),
[AgentCore GA announcement](https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/),
[AgentCore Memory blog](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-agentcore-memory-building-context-aware-agents/),
[AgentCore Gateway blog](https://aws.amazon.com/blogs/machine-learning/introducing-amazon-bedrock-agentcore-gateway-transforming-enterprise-ai-agent-tool-development/),
[New AgentCore capabilities](https://www.aboutamazon.com/news/aws/aws-amazon-bedrock-agent-core-ai-agents)

## The lean path (often better for a small team)

- **Amazon Bedrock** — foundation models (Claude etc.) for the agent's reasoning + embeddings.
- **AWS Lambda** — serverless functions for: (a) the **sleep-time consolidation** worker, (b) webhook/
  alert ingestion, (c) memory-write handlers. Pairs perfectly with CockroachDB **changefeeds**.
- **Amazon S3** — raw artifact/document storage (postmortems, transcripts, uploads) with only the
  embeddings + metadata living in CockroachDB.
- **Amazon EventBridge / SQS** — event plumbing to trigger consolidation or alert flows.
- **ECS/EKS** — only if we need a long-running custom agent service; usually overkill for a hackathon.

## Recommended AWS footprint for us

Minimum to satisfy + impress:
1. **Bedrock** for reasoning + embeddings (core).
2. **Lambda** for the async consolidation / memory-write path (shows the "async writes" production
   requirement from `02`).
3. Optionally **AgentCore Runtime** to host the agent (strong production-readiness story) **or**
   **AgentCore Gateway** to expose tools via MCP with Guardrails.

Keep CockroachDB as the **unambiguous memory layer** — do not let AgentCore Memory blur that story,
or we weaken the "CockroachDB is the system of record" narrative the judges want.
