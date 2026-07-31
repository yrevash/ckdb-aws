# 02 — Agentic & Application Guardrails

Implements the "Agentic & application guardrails" row of [`00-security-charter.md`](./00-security-charter.md)
in `backend/` and `web/`. These are the **application-layer, structural** defenses — they do **not**
rely on the LLM behaving. Every control has dedicated tests (`backend/tests/test_guardrail_*.py`,
`web/lib/security-headers.test.ts`) run by `scripts/verify_phase2.sh`.

Status: **[IT]** implemented + tested · **[DT]** deploy-time. Module home:
`backend/src/postmortem_backend/guardrails/`.

## Design premise

The agent is a privileged actor. The guiding rule: **free text can never select a tool, its
arguments, or authorize an action.** The reasoner emits a *typed* `DecisionKind` + enum `ActionKind` +
a citation id; tools and args are built from typed domain objects, never parsed from alert/log prose.
Each guardrail below is an independent ring, so a failure in one (or a fully compromised model) does
not defeat the others.

## 1. Tool allowlist + destructive-action gate — `allowlist.py` (R3, R5)

- **[IT] Allowlist.** `ALLOWED_TOOLS` is the complete namespace of tools the agent may invoke; a name
  not in the frozenset is unreachable (`enforce_tool_allowlist` → `ToolNotAllowed`). Decisions map to
  tools through a typed enum (`DECISION_TOOL`), never a free-text name.
- **[IT] Destructive/high-blast-radius gate.** `authorize_action()` refuses irreversible or
  high-blast-radius actions (`SCALE`, `FEATURE_FLAG`, or anything the policy tier flags with
  `requires_human_approval`) unless `human_approved=True` **and** a **named** approver is supplied —
  anonymous approval is refused. Every decision (allow *or* deny) yields an auditable `ApprovalRecord`.
  Enforced in code, so even a bug that routes such a command to the act path cannot execute it
  un-approved. This is the app-layer partner to the DB grant-deny (only the writer role can write, and
  it structurally cannot `DROP`/`TRUNCATE`).

## 2. Provenance gate — `provenance.py` (R4, R7)

- **[IT] No ungrounded action.** `require_grounded_action()` rejects a command with no
  `cited_memory_id` *before* execution, and — when the ids Recall surfaced this turn are known —
  rejects a citation that isn't one of them (a hallucinated citation).
- **[IT] No bypass.** `ProvenanceGuardedRemediation` wraps *any* `AtomicRemediationPort`, so every act
  path (real, fake, future) is forced through the citation check before it can call the store. The
  store's own in-SQL gate (the `remediate_and_record` CTE returns no row unless the cited memory
  resolves, and the memory write commits in the same transaction) remains the final authority — this
  is the belt to the SQL suspenders. `test_guardrail_provenance` asserts the wrapper can't be bypassed.

## 3. Prompt-injection defenses (app layer) — `injection.py` (T1, R6 inner ring)

- **[IT]** Alerts, logs, webhook bodies, and recalled memory text are treated as **untrusted**.
  `guard_untrusted_text()` **fails closed** on: tool-/function-call syntax (`tool_call`,
  `remediate_and_record(...)`, `<tool_use>`…), instruction-override/jailbreak phrasing ("ignore
  previous instructions", "new system prompt"), and fake role/turn markers (`system:`, `<|im_start|>`,
  `[INST]`). Control characters are scrubbed; over-long fields (>8 000 chars, context-stuffing) are
  rejected.
- **[IT]** `sanitize_signal()` screens every untrusted field of an inbound signal; any injection fails
  the whole turn closed (never partially sanitized into the model).
- **[DT]** The **outer ring** is the Bedrock Guardrail (`PROMPT_ATTACK` filter) on the Converse call —
  owned by infra ([`01-*`](./01-aws-infrastructure-security.md)). This module is deliberately
  independent so the control holds even if the Guardrail is misconfigured/unavailable.

## 4. Input validation + authenticated webhook — `validation.py` (R9, T3)

- **[IT]** All external inputs are validated with **pydantic**, deny-by-default (`extra: forbid`,
  bounded lengths). `AlertPayload` also injection-screens its free-text fields; `ChangefeedEnvelope`
  validates CockroachDB's `{payload:[…], length:N}` webhook shape and caps the batch.
- **[IT] Authenticated changefeed webhook.** `verify_hmac_signature()` authenticates the raw body with
  **HMAC-SHA256**, **constant-time** compared. A missing secret (server misconfig), missing signature,
  or mismatch all **fail closed** — because an unauthenticated webhook is a direct memory-poisoning
  vector (fabricated "episode committed" events into consolidation, T3). **[DT]** the shared secret is
  provisioned in Secrets Manager and set on the CockroachDB changefeed sink at deploy.

## 5. Role-scoped DB access in code — `roles.py` (R7, T2)

- **[IT]** The Track C SQL grants (`postmortem_reader` / `writer` / `consolidator`) are the
  authoritative DB boundary. `RoleScopedProvider` tags each connection pool with its identity, and the
  adapters assert it at construction: `require_reader()` refuses a write-capable pool on the recall
  path, `require_writer()` refuses the read-only reader on the act path → `RoleScopeViolation`. A
  wiring mistake fails **in-process, before any statement reaches CockroachDB**. Unscoped/legacy
  providers pass through (the fake runtime and local CI are unaffected); production wiring always wraps
  its pools.

## 6. Web application security — `web/lib/security-headers.ts` + `next.config.ts` (T8)

- **[IT]** Every response carries a deny-by-default **CSP** (`default-src 'self'`, `object-src 'none'`,
  `frame-ancestors 'none'`, `upgrade-insecure-requests`), **HSTS** (2y, preload), **X-Frame-Options:
  DENY**, **X-Content-Type-Options: nosniff**, **Referrer-Policy**, **Permissions-Policy** (camera/mic/
  geo off), and **COOP: same-origin**. Header presence + CSP shape are unit-tested
  (`security-headers.test.ts`) rather than trusted by inspection.
- **[IT]** No secrets in the client bundle (no server-only secret behind `NEXT_PUBLIC_`).
- **[DT]** `connect-src` gets the real backend/SSE origin appended from env at deploy; tightening
  `script-src`/`style-src` from `'unsafe-inline'` to per-request nonces is the documented deploy-time
  hardening step (Next.js inline bootstrap needs it until then).

## Verify

```bash
cd backend && POSTMORTEM_TEST_DATABASE_URL=... .venv/bin/pytest -q   # incl. test_guardrail_*
cd web && pnpm test && pnpm typecheck && pnpm lint && pnpm build     # incl. security-headers.test.ts
```
Both run inside `scripts/verify_phase2.sh` (currently green).
