# Postmortem — Devpost Submission Checklist

Hackathon: **Build with Agentic Memory** (CockroachDB × AWS). Deliverable status uses three states:
**done** · **pending-AWS** (needs the live AWS deployment, not yet performed) · **pending-record**
(needs the video capture, not yet recorded). Accuracy over hype — nothing below claims live AWS is done.

## Required deliverables

| Deliverable | Status | Notes / where |
|---|---|---|
| **Public repository** | **done** | Public at <https://github.com/yrevash/ckdb-aws>. |
| **Visible OSS license** | **done** | MIT at repo root: [`LICENSE`](../LICENSE). Referenced from README. |
| **README (what/why, architecture, tool + service writeups, setup)** | **done** | [`README.md`](../README.md) — overhauled, submission-grade, honest status table. |
| **Architecture diagram** (optional but recommended) | **done** | Mermaid in [`README.md`](../README.md) + full version in [`docs/ARCHITECTURE.md`](ARCHITECTURE.md). |
| **CockroachDB tool-usage writeup** (≥2 tools, "how") | **done** (writeup) | README "CockroachDB tools used & HOW" — all **4/4**: C-SPANN, Managed MCP, ccloud, Agent Skills. C-SPANN/MCP/Agent-Skills usage is code-backed locally; ccloud region-kill capture is pending-AWS. |
| **AWS service writeup** (≥1 service, "how") | **done** (writeup) | README "AWS services used & HOW" — Bedrock, Lambda, S3, ECS/Fargate. Consolidation logic tested locally; **live AWS deploy pending**. |
| **Functional demo URL** | pending-AWS | Requires the ECS/Fargate + S3/CloudFront deploy (not yet performed). |
| **<3-min public video** | pending-record | Script ready: [`docs/DEMO_SCRIPT.md`](DEMO_SCRIPT.md). 178s budget; failover pre-recorded from a real kill. |
| **Devpost submission form** | pending-AWS | Repo is public; complete once the demo URL + video are live. Submit with buffer. |

## Judging-criteria coverage map

| Criterion | Covered by | Evidence status |
|---|---|---|
| **Agentic Memory Design** | 3 memory types (episodic / semantic-bitemporal / procedural), C-SPANN recall, bitemporal "facts evolve, not overwrite" | Locally proven (Phase 1/2); bitemporal individually verified (Phase 3 Track B) |
| **Technical Implementation** | Single-store one-transaction `remediate_and_record`; real C-SPANN vectors; MCP read vs. direct-SQL write RBAC split | Locally proven — live serializable proof green (Phase 1/2) |
| **Real-World Impact** | Retrieval quality (real) + MTTR delta | Measured: recall@1=0.85 (hard negatives), nDCG@10=0.94. **MTTR delta pending the real-agent run** — no rigged number claimed (Reality Charter R7) |
| **Production Readiness** | RPO=0 / RTO<10s live region survival; audit logging; PITR/backup; least-privilege roles; Guardrails; observability | RPO=0/RTO locally proven on 9-node sim cluster (Track A); audit/PITR/roles individually verified (Track C); Bedrock Guardrails + AWS observability **pending-AWS** |
| **Creativity & Originality** | Sleep-time consolidation (changefeed→SQS→Lambda distills raw incidents into runbooks) + bitemporal runbook evolution | Consolidation logic tested locally; live pipeline **pending-AWS** |

## Tool / service requirement summary

- **CockroachDB tools (requirement: ≥2):** using **4/4** — C-SPANN vector index, Managed MCP server,
  ccloud CLI, Agent Skills. Writeup in README; hardening detail in [`docs/HARDENING.md`](HARDENING.md).
- **AWS services (requirement: ≥1):** using **Bedrock + Lambda + S3 + ECS/Fargate** (plus API Gateway,
  SQS, Secrets Manager, CloudWatch). Writeup in README; full design in
  [`research/postmortem/03-aws-infrastructure.md`](../research/postmortem/03-aws-infrastructure.md).

## What must still happen (the pending-AWS / pending-record items)

1. Deploy the AWS stack (CDK): ECS/Fargate agent+backend, S3/CloudFront console, Bedrock access
   (Sonnet 4.6 + Haiku + Titan V2 + Guardrails), API GW → receiver Lambda → SQS → consolidator Lambda.
   The synth now **fails fast** without `-c agent_image_uri=...` and, in the default `privatelink`
   egress mode, without `-c crdb_privatelink_service_name=...`; teams on a CockroachDB Cloud tier
   without PrivateLink deploy with `-c crdb_egress_mode=public` plus a Cloud IP allowlist.
2. Stand up / connect the multi-region CockroachDB cluster for the real failover capture; confirm
   Managed MCP service-account wiring and the changefeed external connection. Create the
   `postmortem_agent_reader` and `postmortem_agent_writer` SQL service accounts
   (`db/migrations/0007_audit_logging.sql`) and populate their two DSNs into the **separate**
   reader/writer secrets — the backend refuses to start in aws mode if the two DSNs are identical or
   share a username (audit C3). On a managed tier that withholds `MODIFYCLUSTERSETTING`, apply the
   schema with `POSTMORTEM_BOOTSTRAP_STRICT=0` and treat the printed `BOOTSTRAP_DEGRADED` lines as the
   tier's capability report (see [`db/README.md`](../db/README.md)).
3. Bring up the public demo URL and test it end to end (including CSP/fonts/assets).
4. Record the <3-min video per [`docs/DEMO_SCRIPT.md`](DEMO_SCRIPT.md), capturing a **real** region
   kill off-camera for the money shot.
5. Complete and submit the Devpost form with buffer before the deadline (the repo is already public).

## Pre-submission integration note

Phase 3 Tracks A/B/C are stitched into one verifier: `scripts/verify_phase3.sh` runs A + B + C and was
recorded green — with a real `docker compose kill us-east-2` — in
[`docs/SESSION_2026-08-02.md`](SESSION_2026-08-02.md). The earlier "not yet stitched / one known
failing test" note in [`docs/SESSION_2026-07-31.md`](SESSION_2026-07-31.md) §4 is superseded by that
session and is kept only as a historical record. The remaining Phase 3 item is **Track D** (console UI
surfaces for the resilience/temporal telemetry). Re-run the verifiers before recording; no status
above is claimed from anything but a real run.
