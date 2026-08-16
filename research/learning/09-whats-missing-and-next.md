# 09 — What's missing & what's next

An honest accounting of what is **not** done. Phases 1–3 + the security layer are complete and verified
**locally**. The remaining work is almost entirely **"make it real on AWS" + submission**. Target
deadline: **19 Aug 2026**.

## The one-line status

Everything provable without a live cloud account is **done and green**. What's left is **deploying to a
real AWS account, recording the demo, and submitting** — plus a few security deploy-time hardening items.

---

## A. Critical path to submission (must-do)

### 1. Real AWS deployment — the big one
The whole system runs locally on a **fake runtime** (no AWS creds). To go real:
- [ ] **Provision CockroachDB Cloud** cluster on AWS (decision: cheap single-region for the app + a
  self-hosted 3-region cluster for the failover demo, vs. paid multi-region Advanced ~$260/day).
- [ ] **Confirm Bedrock model access** in the chosen region — Claude **Sonnet 4.6** + **Haiku** +
  **Titan Text Embeddings V2** enabled.
- [ ] **Deploy the CDK stacks** (`infra/`) — Shared (VPC/KMS/Secrets/Guardrail), App (Fargate + WAF),
  Consolidation (SQS + Lambdas), Security (CloudTrail/GuardDuty/Config) — synth now requires
  `-c agent_image_uri=...` and, in the default privatelink mode, `-c crdb_privatelink_service_name=...`;
  it fails fast rather than deploying a service that never passes /healthz (audit B4) or a VPC with no
  path to the database (audit B2).
- [ ] **Populate real secrets** in Secrets Manager: the Managed-MCP reader token, and **three
  distinct** CockroachDB DSNs (`postmortem_agent_reader`, `postmortem_agent_writer`, consolidator) —
  the backend refuses to start if reader and writer are the same identity — plus the changefeed HMAC
  secret.
- [ ] **Wire the real Managed MCP** recall path (service account + keys) and the **CockroachDB
  PrivateLink** endpoint service. Smoke-test `langchain-cockroachdb` early (it's young — verify it
  behaves). PrivateLink is **Advanced-tier only**; on a lower tier deploy with
  `-c crdb_egress_mode=public` and add a Cloud IP allowlist for the NAT address.
- [ ] **Swap the fake runtime → real Bedrock/Strands/MCP/SQL** and run the full end-to-end flow live.
- [ ] **Stand up the changefeed → API Gateway → SQS → Lambda** consolidation pipeline against the real
  cluster (flip `kv.rangefeed.enabled`, already handled in bootstrap) — if the tier permits
  `SET CLUSTER SETTING` at all; `db/apply.sh` now reports rather than aborts when it is refused
  (audit B3).
- [ ] **Get the public demo URL** (the console on Fargate behind the ALB/WAF, or a Vercel preview).

### 2. Record the <3-minute demo video
- [ ] Follow `docs/DEMO_SCRIPT.md` (the shot-by-shot script is ready).
- [ ] Capture the **real region-failover money-shot**: either **Tier A** (`ccloud cluster disruption`,
  needs limited-access enrollment) or the guaranteed **Tier B** (self-hosted 3-region EC2 cluster we
  can kill nodes on). Keep the deterministic-replay fallback as camera-safe backup.
- [ ] Upload to YouTube/Vimeo, public.

### 3. Devpost submission
- [x] **GitHub repo is public** — <https://github.com/yrevash/ckdb-aws>. Still confirm the **MIT
  license is visible** in the repo's About section.
- [ ] Submit: public repo URL, demo URL, video URL, the **CockroachDB tool-usage** + **AWS service**
  writeups (already in the README), and the architecture diagram.
- [ ] Optional: submit feedback on the CockroachDB AI tools (we hit several real gaps worth reporting —
  see `docs/HARDENING.md`).

---

## B. Security deploy-time items (from `docs/security/`)
These are marked **[deploy-time]** / **[planned]** and become real at/after deploy:
- [ ] Populate real secret values; accept the CockroachDB PrivateLink connection.
- [ ] Enable GuardDuty/Config at the account level if not already on.
- [ ] **CSP nonces** — tighten the console's `script-src`/`style-src` from `'unsafe-inline'` to
  per-request nonces; append the real backend origin to `connect-src`.
- [ ] **CIS 6.4** — configure idle-session timeout on CockroachDB (flagged open).
- [ ] **CI vulnerability-scan gate** on the container image (planned, not wired).
- [ ] **Scheduled S3 backups** — PITR is proven on `nodelocal://`; wire the scheduled S3 backup +
  log-export for production.

---

## C. Small cleanups (quick, do before recording)
- [x] **Region names unified** — the console/storyboard and the real cluster both use
  `us-east-1 / us-east-2 / us-west-2`. `Region` in `web/lib/events.ts` is now an open `string` with
  `CLUSTER_REGIONS` naming the demo topology; the backend's `console_region()` no longer truncates
  (a truncated label mislabelled where the agent actually ran — charter R6).
- [x] **Phase 2 evaluation report** — `web/public/phase2-evaluation.json` is a **generated build
  artifact** (`scripts/verify_phase2.sh` writes it from a real `python -m postmortem_eval` run) and is
  gitignored on purpose. Run the verifier before recording, or the Memory view shows em-dashes
  (Reality Charter R6). Note `phase3-resilience.json` **is** committed — decide deliberately whether
  both should be, and never hand-author either.

---

## D. Optional / stretch (not required to win, but strong)
- [ ] **Mobile "command from your pocket" console** — a single-column, thumb-reachable view (incident +
  proposed action + big Approve/Hold/Escalate) for the 3am-on-your-phone use case you flagged. Great
  real-world-impact story; needs its own responsive layout.
- [ ] **Multi-agent split** — separate detector / responder / consolidator agents (currently one
  responder + the consolidation job). Designed in the charter; not built.
- [ ] **`REGIONAL BY ROW` multi-tenant homing** — pin each tenant's memory to its region for
  latency/residency. Partially designed.
- [ ] **AgentCore Runtime hosting** — an upgrade over Fargate (per-session microVM isolation); Fargate
  is the MVP choice.

---

## E. Open decisions that need YOU
1. **Budget ceiling** for ~the deployment window — decides multi-region Advanced (~$260/day) vs.
   single-region-Cloud-app + self-hosted-EC2-for-the-demo. *Recommendation: the cheaper split.*
2. **Enroll in `ccloud cluster disruption`?** (free; unlocks the Tier-A native failover demo — we're
   covered by Tier-B either way).
3. **Accounts** — is there a CockroachDB Cloud org + an AWS account with Bedrock access ready?
4. **The learning session** — you wanted a walkthrough before deploying. This folder is the written
   version; a live Q&A whenever you want.

---

## F. Where we are, honestly
- ✅ **Design, build, and local proof: complete.** Phases 1–3 green; enterprise security layer green;
  submission docs written.
- ⏳ **Real AWS + video + submission: remaining**, and gated mostly on accounts/budget, not on unknowns
  — the hard engineering (the wedge, the failover proof, the guardrails) is done and demonstrated.

The safest next move is to lock the **E** decisions, then execute **A** (deploy → record → submit) with
**B/C** folded in along the way.
