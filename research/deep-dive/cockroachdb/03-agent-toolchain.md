# CockroachDB Agent Toolchain — Deep Dive

CockroachDB's "agent-ready" story (announced 2025-2026) rests on four distinct but interoperable tools. This doc covers each in depth, cites official sources, and maps them to hackathon requirements.

> Primary sources: [CockroachDB for AI Agents (overview)](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-agent-ready-database/), [Managed MCP Server blog](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-managed-mcp-server/), [ccloud CLI blog](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-cli-database-automation/), [Agent Skills blog](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-database-lifecycle-automation/), [CockroachDB and AI docs](https://www.cockroachlabs.com/docs/stable/cockroachdb-and-ai), [cockroachdb-skills GitHub repo](https://github.com/cockroachlabs/cockroachdb-skills), [LangChain integration blog](https://www.cockroachlabs.com/blog/agent-development-cockroachdb-langchain/), [LangChain vectorstore docs](https://docs.langchain.com/oss/python/integrations/vectorstores/cockroachdb), [Claude Code plugin page](https://claude.com/plugins/cockroachdb), [Devpost hackathon page](https://cockroachdb-ai.devpost.com/).

---

## 1. Cloud Managed MCP Server

### Endpoint

```
https://cockroachlabs.cloud/mcp
```

This is a **hosted, multi-tenant MCP (Model Context Protocol) endpoint operated by Cockroach Labs** — there is nothing to deploy or run. It sits in front of CockroachDB Cloud's existing control-plane auth, RBAC, and SQL proxy layers, so it inherits your organization's existing security posture rather than introducing a new trust boundary. ([blog](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-managed-mcp-server/))

There is also a **self-hosted `CockroachDB MCP Server`** for on-prem/self-managed clusters (same tool surface, but you run it yourself), and a separate **CockroachDB Docs MCP Server** that exposes published documentation for RAG-style Q&A in your editor. ([docs: CockroachDB and AI](https://www.cockroachlabs.com/docs/stable/cockroachdb-and-ai))

### Getting the config snippet (Cloud Console flow)

1. Log into the CockroachDB Cloud Console.
2. Open your cluster, click the **"Connect"** modal (the same modal used for connection strings).
3. Select the **MCP integration** option.
4. Cockroach Labs generates a **ready-to-paste config snippet** scoped to that specific cluster/organization.
5. Paste it into your MCP client's config file (Claude Code, Cursor, VS Code, Claude Desktop, etc.).

No infrastructure, ports, or certs to manage on your side — the whole point of "managed" is that Cockroach Labs hosts, secures, and operates the MCP layer for you. ([blog](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-managed-mcp-server/))

**Gotcha:** the snippet is cluster/org-scoped — if your hackathon team spins up multiple clusters (e.g., dev vs. demo), each one needs its own snippet regenerated from that cluster's Connect modal.

### Authentication

Two modes, aimed at two different agent postures:

| Mode | Flow | Use case |
|---|---|---|
| **OAuth 2.1** (Authorization Code + PKCE) | Interactive, browser-based consent screen | Human-in-the-loop agent sessions (Claude Code, Cursor, VS Code chat) |
| **Service-account API keys** | Static key issued in Cloud Console, scoped via Cloud RBAC roles | Fully autonomous / headless agents (CI pipelines, background workers) |

OAuth scopes observed: `mcp:read` (read-only) and `mcp:write` (mutations). Read and write permissions are granted at consent time on the **OAuth consent screen** itself — i.e., the human approving the connection explicitly decides whether the agent gets write capability, not just whether it can connect at all. ([search synthesis citing official blog](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-managed-mcp-server/))

Service-account API keys are the same primitive used by the [Cloud API](https://www.cockroachlabs.com/docs/cockroachcloud/authorization) generally — issued via the Console, attached to Cloud RBAC roles, scopable to specific clusters/folders.

**Gotcha:** because service-account keys are long-lived, treat them like any production credential — scope the role tightly (read-only role for anything that doesn't need write) and rotate via the service-account API key management (`ccloud service-account api-key create/delete`, see §2).

### Read-only-by-default + write-consent flow

This is the core safety model, and it's layered:

- **Default posture: read-only.** Out of the box (whether via OAuth `mcp:read` or a read-scoped service account), the agent can only use introspective, non-mutating tools.
- **Write is opt-in, explicit, and separately scoped** — either via the `mcp:write` OAuth scope granted at consent time, or via a service account whose Cloud RBAC role includes write privileges.
- **Destructive SQL stays out of reach regardless of consent.** Per the blog: *"Destructive SQL operations (for example, `DROP` or `TRUNCATE`) remain unsupported"* — this is a hard tool-surface restriction, not a permission you can grant your way around.
- **Every tool call still gets an RBAC check.** Even after write consent is granted, *"every tool invocation performs a Cloud RBAC check before execution... requests are rejected if permissions exceed the expected scope."*

So there are three independent guardrails stacked: (1) what the OAuth scope/service-account role allows, (2) a per-call RBAC check, (3) a hardcoded deny-list of destructive verbs in the tool surface itself.

### RBAC scoping

The MCP server does **not** introduce its own permission model — it reuses **CockroachDB Cloud's existing Access Management / RBAC** (org roles, cluster-level roles, folder-scoped roles). See [Cloud Access Management docs](https://www.cockroachlabs.com/docs/cockroachcloud/authorization) for the underlying role catalog. This means the same roles you'd use to scope a human's Console access are what scope an agent's MCP access.

### System-table deny-listing

Beyond RBAC, there's a content-level guard: **system tables are deny-listed** so an agent can't use introspective tools (or crafted `select_query` calls) to read sensitive internal cluster state (credentials, internal metadata, etc.) even with a broad RBAC role. This is called out explicitly in the read-only description: *"System tables are deny-listed to prevent sensitive access."*

### Audit logging / observability

Per the blog, every MCP request is logged with structure suitable for compliance and debugging:

> *"All requests emit structured logs tagged with `mcp`, including tool name, cluster and organization context, redacted SQL shape, latency and response size."*

Additional observability layers:
- **End-to-end tracing** — spans covering MCP middleware, Cloud API calls, and the underlying SQL query, flowing into Cockroach Labs' internal observability pipeline (this is Cockroach Labs' own ops visibility, not something surfaced to you directly today, but worth knowing it exists).
- **Usage analytics** — tool-invocation events feed an adoption-pattern analytics pipeline.

Note: "redacted SQL shape" strongly implies literal values are stripped from logged query text — good for avoiding PII leakage into logs, but also means you shouldn't rely on MCP audit logs as a literal query-replay mechanism.

**Gotcha:** the doc set does not (as of this research) describe a customer-facing audit-log export UI specific to MCP calls the way `ccloud audit list` exposes control-plane actions (see §2) — if you need to *show* auditability in a demo, pair MCP write actions with `ccloud audit list -o json`, which does expose an organization audit trail.

### MCP tools/capabilities exposed

**Read-only tool set (always available under `mcp:read` / read-scoped keys):**
- `list_databases`
- `list_tables`
- `get_table_schema` (columns, indexes)
- inspect cluster health / running queries
- `select_query` — run read-only SQL
- `explain_query` — run `EXPLAIN` on a statement
(The docs page phrasing: *"list databases and tables, describe schemas and indexes, inspect cluster health and running queries, and run read-only SQL and EXPLAIN statements."* — [docs](https://www.cockroachlabs.com/docs/stable/cockroachdb-and-ai))

**Write tool set (requires explicit write consent):**
- `create_database`
- `create_table`
- `insert_rows`

**Explicitly excluded regardless of consent:**
- `DROP`, `TRUNCATE`, and other destructive DDL/DML

The local Claude Code plugin (§4) additionally layers a Python-based pre-execution SQL-validation hook on top of whatever the MCP tool surface allows, specifically to block destructive statements and catch anti-patterns before they reach the tool call — useful pattern to replicate in your own agent harness even if you're calling the managed MCP server directly.

### Client connectivity

Explicitly called out as working "out of the box" with:
- **Claude Code**
- **Cursor**
- **VS Code**
- Any other MCP-compatible client (the protocol is standard; the CockroachDB-specific part is just the hosted endpoint + auth)

**Representative config shape** (illustrative — always use the exact snippet Cloud Console generates for your cluster, since it embeds cluster/org identifiers and auth details):

```jsonc
// e.g. .cursor/mcp.json, .vscode/mcp.json, or claude_desktop_config.json
{
  "mcpServers": {
    "cockroachdb-cloud": {
      "url": "https://cockroachlabs.cloud/mcp",
      "headers": {
        // OAuth: client performs the Authorization Code + PKCE flow in-browser
        // on first connect, or supply a service-account key directly:
        "Authorization": "Bearer <SERVICE_ACCOUNT_API_KEY>"
      }
    }
  }
}
```

Streamable-HTTP / `url`-based remote server config is the standard MCP client pattern (as opposed to local `command`/`args` stdio servers) — this is what lets Claude Code / Cursor / VS Code talk to a *hosted* endpoint with no local process. Cockroach Labs' generated snippet will fill in the exact header/auth shape for OAuth vs. API-key mode.

---

## 2. ccloud CLI (agent-ready)

### Design philosophy

Cockroach Labs explicitly redesigned `ccloud` with **AI agents as a first-class consumer**, not just a developer convenience CLI:

> *"CockroachDB deliberately redesigned ccloud with AI agents as a first-class consumer. The interface is designed to be reliable for machines: commands follow a consistent structure, outputs are available as structured JSON, and errors are deterministic so agents can react programmatically."* ([blog](https://www.cockroachlabs.com/blog/cockroachdb-ai-agents-cli-database-automation/))

Two concrete design commitments:

1. **Consistent noun-verb structure** across the entire surface — `ccloud cluster create`, `ccloud folder list`, `ccloud replication create`, `ccloud service-account api-key create`, etc. This lets an agent infer available operations purely from `--help` output the same way it would reason about `git`, `docker`, or `kubectl` — no bespoke prompt-engineering needed to teach the model the CLI's shape.
2. **`-o json` on every command** — a global flag giving structured, parseable output instead of human-formatted tables. An agent can pipe straight into `jq`:
   ```bash
   ccloud cluster list -o json | jq -r '.[] | select(.state=="READY") | .name'
   ```

### Control-plane surface (from [ccloud reference docs](https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-reference))

| Noun | Representative verbs |
|---|---|
| `auth` | `login`, `logout`, `whoami` |
| `cluster` | `create` (basic/standard/dedicated), `list`, `info`, `delete`, `sql`, `versions`, `version-deferral get/set` |
| `cluster` (maintenance/DR) | `blackout-window list/create/delete`, `maintenance get/set/delete`, `disruption get/set/clear` (chaos/failover simulation), `cmek get` |
| `cluster` (observability) | `log-export get/enable/disable`, `metric-export cloudwatch/datadog/prometheus` |
| `cluster database` | `list`, `create`, `delete` |
| `cluster user` | `create` (SQL users) |
| `cluster backup` | `list`, `config get/update` (retention policy) |
| `cluster restore` | `list`, `create` |
| `cluster networking` | `allowlist create/list/update/delete`, `egress-rule list/create/delete`, `client-ca-cert get/set/update/delete`, `private-endpoint service/connection/trusted-owner`, `egress-private-endpoint list/get/create/delete` |
| `replication` | `list`, `get`, `create`, `update` — physical cluster replication + failover |
| `service-account` | `list`, `get`, `create`, `delete`; `api-key list/create/delete` |
| `jwt-issuer` | `list`, `get`, `create`, `update`, `delete` (OIDC config) |
| `organization` / `org` | `get` |
| `audit` | `list` — organization action history with time filters |
| `billing` | `invoice list/get` |
| `folder` | `list`, `get`, `create`, `update`, `delete`, `contents` — hierarchical cluster org |
| `settings` | `set` (e.g., `--disable-telemetry`) |

### Example commands (agent-usable patterns)

Pulling a connection string programmatically:
```bash
ccloud cluster connection-string blue-dog --database myapp \
  --sql-user maxroach -o json | jq -r '.connection_url'
```

Cluster info for app configuration:
```bash
ccloud cluster info <name> -o json
```

Maintenance / blackout window management (an agent adjusting operational windows without touching cluster topology — a deliberately bounded operation shown in Cockroach Labs' own example of "safe" agent automation):
```bash
ccloud cluster maintenance update cluster-gamma
ccloud cluster blackout-window create cluster-gamma
```

Service-account + scoped API key provisioning (how you'd mint credentials *for* an agent):
```bash
ccloud service-account create --name hackathon-agent --description "agentic memory demo"
ccloud service-account api-key create --service-account-id <id> -o json
```

Replication / failover surface (for a DR or multi-region demo):
```bash
ccloud replication list -o json
ccloud replication get <replication-id> -o json
ccloud replication update <replication-id> --promote   # illustrative — check exact flag in `ccloud replication update --help`
```

**Gotcha — no canonical "agent triggers failover" example exists in Cockroach Labs' own materials.** Their published example scenarios are deliberately restricted to read + low-risk-write operations (reading cluster state, adjusting maintenance windows) as a demonstration of guardrails, not full autonomy. If your demo wants an agent to *drive* a failover or scale event, you'll be composing `ccloud replication update` / `ccloud cluster` scale verbs yourself — confirm exact flag names against `ccloud <noun> <verb> --help` or `-o json` schema before the demo since this reference was reconstructed from docs pages plus a blog post rather than a single authoritative command table.

### Authentication for the CLI

- Human/interactive: `ccloud auth login` (SSO supported via `--sso`).
- Agent/headless: **service-account API keys** (`ccloud service-account api-key create`), same Cloud RBAC role model as MCP write access above — meaning you can mint one service account and scope it consistently for both an agent's MCP tool calls and its `ccloud` CLI calls.

---

## 3. Agent Skills repo (`cockroachdb-skills`, open source)

Repo: [github.com/cockroachlabs/cockroachdb-skills](https://github.com/cockroachlabs/cockroachdb-skills) — Apache 2.0, actively CI-validated.

### What a "skill" is

> *"A CockroachDB Skill is a structured capability that: encodes operational expertise... follows the Agent Skills Specification... is machine-executable... has clear boundaries... references authoritative sources."*

Concretely: **a skill is a directory containing a `SKILL.md` file** (YAML frontmatter + Markdown body), validated against the open [Agent Skills Specification](https://agentskills.io/specification) — a vendor-neutral spec (not CockroachDB- or Anthropic-specific) that any compliant agent runtime can discover and load.

Example frontmatter (from the `cockroachdb-sql` skill):
```yaml
---
name: cockroachdb-sql
description: Use when writing, generating, or optimizing SQL for CockroachDB,
  designing CockroachDB schemas, or when the user asks about CockroachDB-specific
  SQL patterns, type mappings, and distributed database best practices. Also use
  when encountering CockroachDB anti-patterns like missing primary keys,
  sequential ID hotspots, or incorrect type usage.
compatibility: Can work with or without connection to a database...
metadata:
  author: cockroachdb
  version: "1.0"
---
```

Spec-enforced constraints (validated in CI via `scripts/validate-spec.py`): name ≤64 chars, lowercase-hyphen naming, description ≤1024 chars with clear "when to use" trigger language, no reserved words.

Design principles stated in the repo: **scope discipline** (one task per skill), **progressive disclosure** (metadata loads first, full content loads only on invocation — keeps context usage low), **guardrails by default** (skills that mutate data/availability include confirmation prompts and rollback guidance), and **authoritative references** (link to official docs rather than duplicating them, so skills don't rot when docs change).

### Full skill inventory (verified directly against the repo, 2026-07-30)

The repo organizes skills into **10 domains**; 4 are currently placeholder (`.gitkeep` only — reserved for future skills), 6 are populated with **33 skills total** as of this research:

**1. Onboarding and Migrations** (`skills/cockroachdb-onboarding-and-migrations/`)
- `molt-fetch` — bulk data migration from PostgreSQL/MySQL/Oracle/MSSQL
- `molt-verify` — post-migration row-level consistency verification
- `molt-replicator` — continuous CDC replication during cutover
- `setting-up-local-cluster` — spin up a local dev cluster

**2. Query and Schema Design** (`skills/cockroachdb-query-and-schema-design/`)
- `cockroachdb-sql` — NL-to-SQL generation, schema design, anti-pattern detection (rules split across `00-fundamental-principles.md` through `05-operational.md`; always runs `EXPLAIN` against a live connection before returning a query if one is available)

**3. Performance and Scaling** (`skills/cockroachdb-performance-and-scaling/`) — *currently empty placeholder in the public repo*, but this domain's skills ship bundled in the Claude Code plugin under different naming (see note below)

**4. Operations and Lifecycle** (`skills/cockroachdb-operations-and-lifecycle/`)
- `managing-certificates-and-encryption`
- `managing-cluster-capacity`
- `managing-cluster-settings`
- `performing-cluster-maintenance`
- `provisioning-cluster-for-production`
- `reviewing-cluster-health`
- `upgrading-cluster-version`

**5. Resilience and Disaster Recovery** (`skills/cockroachdb-resilience-and-disaster-recovery/`) — *empty placeholder*

**6. Observability and Diagnostics** (`skills/cockroachdb-observability-and-diagnostics/`)
- `analyzing-range-distribution`
- `analyzing-schema-change-storage-risk`
- `auditing-table-statistics`
- `monitoring-background-jobs`
- `profiling-statement-fingerprints`
- `profiling-transaction-fingerprints`
- `triaging-live-sql-activity`

**7. Security and Governance** (`skills/cockroachdb-security-and-governance/`)
- `auditing-cis-benchmark`
- `auditing-cloud-cluster-security`
- `configuring-audit-logging`
- `configuring-ip-allowlists`
- `configuring-log-export`
- `configuring-private-connectivity`
- `configuring-sso-and-scim`
- `enabling-cmek-encryption`
- `enforcing-password-policies`
- `hardening-user-privileges`
- `managing-tls-certificates`
- `preparing-compliance-documentation`

**8. Integrations and Ecosystem** — *empty placeholder*

**9. Cost and Usage Management** — *empty placeholder*

**10. Application Development** (`skills/cockroachdb-application-development/`)
- `benchmarking-transaction-patterns`
- `designing-application-transactions`
- `designing-multi-region-applications`

> Note: the locally-installed **Claude Code plugin** in this environment exposes what appear to be additional performance/observability-flavored skills (`analyzing-range-distribution`, `profiling-statement-fingerprints`, etc.) directly at the top namespace via the `cockroachdb:` prefix — these are the same skills from this repo, just surfaced through the plugin's skill-loader rather than nested under domain folders. The public repo's "Performance and Scaling" domain folder being an empty placeholder while these skills live under "Observability and Diagnostics" in the actual repo suggests the taxonomy is still being refined; don't be surprised if domain groupings shift.

**Install command:**
```bash
npx skills add cockroachlabs/cockroachdb-skills
```

### Installation / usage across clients

The repo uses the **`skills` CLI** (a third-party, spec-compliant installer — [agentskills.io](https://agentskills.io/home)) as the recommended path, claiming compatibility with **"Claude Code, Cursor, Windsurf, and 40+ agents."**

**Option 1 — one-line installer (recommended):**
```bash
npx skills add cockroachlabs/cockroachdb-skills
```
Interactively detects installed agents, lets you pick skills (or `--all`), and asks project-level vs. `--global` (user-level). Non-interactive form for CI:
```bash
npx skills add cockroachlabs/cockroachdb-skills --agent claude-code --skill '*' --yes
```

**Option 2 — manual project-level (git + symlink):**
```bash
git clone https://github.com/cockroachlabs/cockroachdb-skills.git
mkdir -p .claude/skills
ln -s /path/to/cockroachdb-skills/skills .claude/skills/cockroachdb-skills
```

**Option 3 — manual user/global-level:** same as above but into `~/.claude/skills/`.

**Option 4 — direct copy** (no symlinks) for environments where symlinks don't work (some Windows setups) — same as Option 2/3 but `cp -r` instead of `ln -s`. Downside: doesn't auto-update when the repo changes.

**How discovery works at runtime (Claude Code specifically):** Claude scans each `SKILL.md`'s frontmatter (`name` + `description`) at session start — cheap, low-context — and only loads the full skill body when your request actually matches a trigger description ("progressive disclosure"). You can invoke implicitly ("help me check if my cluster is healthy" → auto-triggers `reviewing-cluster-health`) or explicitly ("use the reviewing-cluster-health skill...").

**Cursor / Windsurf / LangChain / raw MCP:** the `npx skills add` installer explicitly targets Cursor and Windsurf directory conventions alongside Claude Code. For **LangChain or a custom MCP-based agent**, there's no first-party "skills loader" package — you'd treat each `SKILL.md` as a retrievable document (e.g., load them into a vector store or a system-prompt-injection step) since the spec is just structured Markdown with YAML frontmatter, not a binary/runtime format. This is a natural integration point for a hackathon team building "agentic memory": the skills repo is itself a good demonstration corpus for a retrieval-augmented tool-selection layer.

---

## 4. Integrations: LangChain, Claude Code plugin, Cursor plugin

### LangChain (`langchain-cockroachdb`)

Package: **`langchain-cockroachdb`** ([docs](https://docs.langchain.com/oss/python/integrations/vectorstores/cockroachdb), [announcement blog](https://www.cockroachlabs.com/blog/agent-development-cockroachdb-langchain/))

```bash
pip install -qU langchain-cockroachdb
# or
uv add langchain-cockroachdb
```

Key classes:
- `CockroachDBEngine` — connection/engine wrapper, supports `from_connection_string(...)`
- `AsyncCockroachDBVectorStore` — async LangChain vector store implementation

Setup pattern:
```python
from langchain_cockroachdb import AsyncCockroachDBVectorStore, CockroachDBEngine
from langchain_openai import OpenAIEmbeddings

engine = CockroachDBEngine.from_connection_string(
    "cockroachdb://user:pass@host:26257/db?sslmode=verify-full"
)
await engine.ainit_vectorstore_table(
    table_name="documents", vector_dimension=1536
)
vectorstore = AsyncCockroachDBVectorStore(
    engine=engine, embeddings=OpenAIEmbeddings(), collection_name="documents"
)
```

Capabilities: native `VECTOR` type (CockroachDB 24.2+), **distributed C-SPANN indexes** for approximate nearest-neighbor search (25.2+, "optimized for distributed systems" per LangChain docs), metadata filtering, multi-tenancy via prefix columns, document add/delete by ID, similarity search with scores. Async-only surface (no sync `CockroachDBVectorStore` mentioned in current docs — worth double-checking against the package if the team needs sync code).

**This is the most directly relevant integration for "agentic memory":** it lets you keep vector embeddings (agent memory) and transactional application state in the *same* horizontally-scalable, serializable database — avoiding a separate vector-DB dependency. Cockroach Labs frames this explicitly as *"combining vectors with transactional data to simplify the data architecture."*

**Gotcha:** the LangChain docs integration table flags that this vector store does **not** currently pass LangChain's standard vector-store compliance test suite — worth a smoke test early rather than assuming full drop-in parity with e.g. `PGVector`.

### Claude Code plugin

Repo: [github.com/cockroachdb/claude-plugin](https://github.com/cockroachdb/claude-plugin) · [Anthropic plugin page](https://claude.com/plugins/cockroachdb)

Install:
```bash
# From Claude Code marketplace
claude plugin install cockroachdb
# or interactively
/install-plugin cockroachdb
# local dev
claude --plugin-dir /path/to/claude-plugin
```

Prerequisite: **MCP Toolbox for Databases** ≥ v1.0.0 (`brew install mcp-toolbox`) if using the self-hosted stdio backend.

Env vars (self-hosted MCP Toolbox backend):
```
COCKROACHDB_HOST
COCKROACHDB_PORT      # 26257
COCKROACHDB_USER
COCKROACHDB_PASSWORD
COCKROACHDB_DATABASE
COCKROACHDB_SSLMODE   # verify-full
```

Backend options:
- **MCP Toolbox stdio** (default, self-hosted, any cluster) — `execute-sql`, `list-schemas`, `list-tables`
- **MCP Toolbox HTTP** (remote/multi-user) — `http://your-toolbox-host:5000/mcp`
- **CockroachDB Cloud MCP** (managed) — `https://cockroachlabs.cloud/mcp`, OAuth or API key, adds `create_database`/`create_table`/`insert_rows` under write consent

Bundled agents (subagents with domain-specific system prompts, visible in this very environment under the `cockroachdb:` namespace):
- `cockroachdb-dba` — performance tuning, schema review, query plan analysis, multi-region planning
- `cockroachdb-developer` — ORM config, retry logic, transaction design
- `cockroachdb-operator` — cluster ops, monitoring, backup/restore, scaling

Bundled skills: the full `cockroachdb-skills` repo as a git submodule (~22-33 skills depending on version pinned).

Safety hooks: a Python pre-execution SQL-validation hook blocks `DROP DATABASE`/`TRUNCATE`-class statements, plus a post-edit anti-pattern linter for SQL/code files — both dependency-free Python 3 scripts, so no extra install burden.

### Cursor plugin

Repo: [github.com/cockroachdb/cursor-plugin](https://github.com/cockroachdb/cursor-plugin)

Install: Cursor Marketplace, or `/add-plugin cockroachdb`.

Same dual-backend model as the Claude plugin (`cockroachdb-toolbox` stdio + `cockroachdb-cloud` HTTP), same env vars, bundles `ccloud` CLI reference material plus the `cockroachdb-skills` submodule as Cursor rules/skills. OAuth 2.1 PKCE or service-account API key for the Cloud MCP backend, configured via cluster ID + authorization headers in Cursor settings.

### VS Code

No dedicated "plugin" repo found; VS Code connects as a **generic MCP client** directly to `https://cockroachlabs.cloud/mcp` using VS Code's native MCP server config (`.vscode/mcp.json`), the same config shape shown in §1. No CockroachDB-specific VS Code extension beyond the MCP connection itself was found as of this research.

---

## (A) Setup quick-reference

| Tool | Get it | Auth | Minimal config |
|---|---|---|---|
| **Cloud Managed MCP** | Cloud Console → cluster → **Connect** modal → MCP tab → copy snippet | OAuth 2.1 (PKCE, browser consent) *or* service-account API key | Paste snippet into `mcpServers` in Claude Code / Cursor / VS Code MCP config, pointing at `https://cockroachlabs.cloud/mcp` |
| **ccloud CLI** | `brew install cockroachdb/tap/ccloud` (or per [docs](https://www.cockroachlabs.com/docs/cockroachcloud/ccloud-reference)) | `ccloud auth login` (human/SSO) or service-account API key (agent) | `ccloud service-account create` → `ccloud service-account api-key create -o json` → export key; always pass `-o json` for agent consumption |
| **Agent Skills repo** | `npx skills add cockroachlabs/cockroachdb-skills` | none (static files) | Installs symlinked skills into `.claude/skills/` (or Cursor/Windsurf equivalent), project- or user-level |
| **LangChain** | `pip install -qU langchain-cockroachdb` | Standard CockroachDB connection string (`sslmode=verify-full`) | `CockroachDBEngine.from_connection_string(...)` → `AsyncCockroachDBVectorStore` |
| **Claude Code plugin** | `claude plugin install cockroachdb` | Env vars (`COCKROACHDB_*`) for self-hosted, or OAuth/API key for Cloud MCP | Requires `mcp-toolbox` ≥1.0.0 if using self-hosted backend |
| **Cursor plugin** | Cursor Marketplace / `/add-plugin cockroachdb` | Same as Claude plugin | Same dual-backend model |

**Cross-cutting gotchas:**
- Service-account API keys are the single credential type shared across `ccloud`, the Cloud MCP server, and both IDE plugins — mint one per agent identity and scope its Cloud RBAC role tightly (read vs. write) rather than reusing a broad org-admin key.
- Write access to the MCP server is granted twice, independently: once at the OAuth-scope/service-account-role level, and again per-call via RBAC — a demo showing "agent asks for write, human approves" should exercise the OAuth consent screen, not just a pre-configured API key, if you want to show the human-in-the-loop story.
- `DROP`/`TRUNCATE` are unreachable via MCP tools regardless of consent — if your demo narrative needs a destructive-looking action, it has to go through `ccloud` or direct SQL, not MCP.
- The public skills repo's domain taxonomy has empty placeholders (Performance and Scaling, Resilience and DR, Integrations and Ecosystem, Cost and Usage Management) — don't assume skills exist there; check `skills/` directly or via `npx skills add` before building a demo around a specific skill name.

---

## (B) How each tool maps to hackathon requirements + demo opportunities

The hackathon ("Build with Agentic Memory," [Devpost](https://cockroachdb-ai.devpost.com/)) requires teams to use CockroachDB's four agent tools. Suggested mapping:

**1. Cloud Managed MCP Server → the agent's live "hands" on data.**
Use it as the runtime tool-calling surface for your agent to read/write agentic memory (conversation state, retrieved facts, embeddings) directly against CockroachDB, without your team standing up any MCP infrastructure. Demo opportunity: show the OAuth write-consent screen live — "the agent wants to write; a human approves" — as a concrete, visual trust story, then show a blocked `DROP TABLE` attempt to demonstrate the tool-surface guardrail. Also worth showing structured MCP audit logs (tool name, cluster, latency) as evidence of production-readiness.

**2. ccloud CLI → the agent's control-plane / SRE hands.**
Good fit for a "self-healing agent" demo: have an agent observe degraded cluster health (via a skill like `reviewing-cluster-health` or `triaging-live-sql-activity`), then use `ccloud` to react — adjust a maintenance window, check replication status, or provision a scoped service account for a new sub-agent. The noun-verb + `-o json` design means you can literally feed `--help` output to your LLM as a tool-discovery mechanism — a nice "agent teaches itself the CLI" demo beat.

**3. Agent Skills repo → the agent's embedded expertise / "long-term procedural memory."**
This maps unusually well to an *agentic memory* hackathon theme: skills are themselves a form of persistent, structured, retrievable knowledge that an agent loads on demand (progressive disclosure) rather than holding in context at all times — conceptually parallel to what your team is likely building for episodic/semantic memory. Demo opportunity: use `cockroachdb-sql` or `designing-application-transactions` to have the agent self-correct a schema/query anti-pattern live, citing the skill's authoritative-reference links.

**4. LangChain + Claude/Cursor plugins → the integration/dev-experience layer.**
`langchain-cockroachdb`'s `AsyncCockroachDBVectorStore` is arguably the most directly load-bearing piece for "agentic memory" itself — storing embeddings and transactional agent state in one serializable, horizontally-scalable database is a clean answer to "why CockroachDB and not two separate systems (vector DB + app DB)." The Claude Code / Cursor plugins are best framed as *how the hackathon team builds the whole thing*, not part of the shipped product — worth a slide, not a live demo.

**Suggested demo narrative arc:** agent perceives (MCP read) → agent recalls (LangChain vector search over CockroachDB) → agent decides using embedded expertise (Agent Skill) → agent acts, gated by human consent (MCP write) → agent operates/repairs infrastructure if needed (ccloud CLI) → all steps traceable via MCP/ccloud audit logs, showing the safety story end-to-end.
