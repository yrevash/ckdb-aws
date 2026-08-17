# What is left

Single source of truth for the run-up to submission. Everything here is either
**not started** or **blocked on something outside the repo** (credentials, a
camera, a form). The engineering is done: every verifier is green, all four CDK
stacks synth, and no test is failing.

> **Deadline.** `research/postmortem/00-charter.md` §138 says **19 Aug 2026**.
> That has never been checked against the actual Devpost page. **Confirm it
> before planning around it** — every sequencing decision below assumes it is
> close.

---

## 1. The blocking four

Nothing else matters until these are done. Only the first is code.

| # | Item | State | Blocked on |
|---|---|---|---|
| 1 | **Real-agent MTTR run** | Harness built + tested; number does not exist | AWS credentials + Bedrock model access |
| 2 | **Demo URL** | Not deployed | A Vercel account |
| 3 | **<3-min video** | Script written (`docs/DEMO_SCRIPT.md`), nothing recorded | Items 1–2 landing first |
| 4 | **Devpost form** | Untouched | Items 2–3 |

Repo, MIT license, README, architecture diagram, CockroachDB tool writeup (4/4
tools) and AWS service writeup are all **done** — see
[`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md).

---

## 2. The MTTR run (the only unmeasured claim)

`decision_quality` publishes `pending_real_agent_run` today. That is honest, but
"does memory make the agent better" is the **Real-World Impact** judging
criterion, and it is the one question the project currently cannot answer.

**Needs credentials only — no VPC, no deployed stack.** It runs against local
CockroachDB, roughly 58 Bedrock Converse calls.

```sh
# 0. authenticate — interactive, do this yourself
aws configure                      # or: aws sso login --profile <profile>
export AWS_PROFILE=<profile> AWS_REGION=us-east-1

# 1. smoke test FIRST (~2s) — catches the two things that otherwise fail
#    40 incidents into the real run
backend/.venv/bin/python -c "import boto3; c=boto3.client('bedrock-runtime'); \
r=c.converse(modelId='us.anthropic.claude-sonnet-4-6', \
messages=[{'role':'user','content':[{'text':'reply with OK'}]}], \
inferenceConfig={'maxTokens':16}); \
print(r['output']['message']['content'][0]['text'], r['usage'])"

# 2. the run
PYTHONPATH=simulator:evaluation backend/.venv/bin/python -m postmortem_eval.real_agent \
  --output evaluation/reports/decision-quality.json

# 3. fold it into the scorecard
PYTHONPATH=simulator:evaluation python3 -m postmortem_eval \
  --decision-quality evaluation/reports/decision-quality.json \
  --output evaluation/reports/phase2.json
```

**Use `backend/.venv/bin/python` for step 2.** It is the only command in the
repo needing `boto3`; a bare `python3` fails with `ModuleNotFoundError`.
Everything else — including the whole deterministic scorecard and test suite —
runs on system `python3` with no third-party packages.

### Known failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: boto3` | System `python3` | Use `backend/.venv/bin/python` |
| `credentials: NONE` | No `~/.aws`, no `AWS_*` env vars | `aws configure` / `aws sso login` |
| `AccessDeniedException` on first call | Bedrock model access is **request-gated per model** in the console; fresh accounts do not have it | Request access to `us.anthropic.claude-sonnet-4-6` |
| Every call fails, credentials fine | `us.anthropic.*` is the **US cross-region inference profile** | `AWS_REGION` must be a US region |

### Reading the result honestly

Read [`evaluation/README.md`](../evaluation/README.md) before quoting any figure.
Three things constrain what it may claim:

- **MTTR is paired** over incidents *both* arms resolved; one-sided resolutions
  are a separate resolution-rate delta. Quote both or neither — an arm that
  only fixes easy incidents looks fast otherwise. With no shared resolved set
  the reduction is `null`, not a number.
- **Model latency is inside MTTR** and also reported stripped out
  (`mttr_reduction_percent_excluding_model_latency`).
- **Retrieval is the harness text ranker, not production C-SPANN** — deliberate,
  so the figure stays comparable to the recall@1 in the same report.

The result may be **null, small, or negative**. That is a real finding, not a
failed run: the deterministic baseline already ties the memory arm, which is
exactly why this measurement exists. If it comes out flat, say so — the honest
number beats a rigged one, and the Reality Charter is the project's whole
differentiator.

---

## 3. Demo URL

Vercel. **`web/next.config.ts` uses `headers()`, which rules out static export** —
an S3 + CloudFront deploy would silently drop the security headers the hardening
docs claim. Do not "simplify" it to a static bucket.

Test end to end after deploying: CSP, fonts, assets, and every view
(Overview / Incident / Resilience / Memory).

---

## 4. Video

Script and 178s budget: [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md). The region-kill
money shot is pre-recorded off-camera from a real `docker compose kill`.

> **Re-run `scripts/verify_phase3.sh` immediately before recording.** It
> regenerates `evaluation/reports/phase3-resilience.json`, which is what the
> console's Resilience tab reads. **Whatever RTO that run measures is the number
> that must be quoted on camera** — not a figure from an earlier session.

Published RTO band is currently **3.1–4.9s** (`README.md`, `docs/ARCHITECTURE.md`,
`docs/DEMO_SCRIPT.md`, `docs/frontend/00-design-brief.md`,
`research/learning/{02,05,07,11,README}`). If a fresh run lands outside it,
update all nine files — but leave `SESSION_2026-08-02.md` alone; it is a
historical record of what *that* session measured.

---

## 5. AWS deploy — optional for submission, still unexecuted

**No part of the AWS path has ever run against real AWS.** After the
2026-08-16 session it synths clean in both egress modes and fails fast on all
four missing-config paths, but "deployable" is not "deployed."

The README status table already says pending-AWS, so submitting without it is
honest. Judge the time: a four-stack CDK deploy from an account with no
credentials configured is the riskiest possible use of the last day and can
consume all of it while putting nothing on camera. **Bank the submittable
artifact first** (items 1–4), then attempt the deploy with whatever is left.

Deploy notes live in [`SUBMISSION_CHECKLIST.md`](SUBMISSION_CHECKLIST.md) §"What
must still happen". Residual risks the fixes do **not** cover: Bedrock model
approval in the chosen region, PrivateLink acceptance latency on the CockroachDB
side, whichever cluster tier is chosen and what it withholds, and
`langchain-cockroachdb` immaturity if it gets used.

---

## 6. Re-verify before submitting

All offline, all fast. Run from the repo root.

```sh
# deterministic scorecard + simulator (no venv, no deps)
PYTHONPATH=simulator:evaluation python3 -m unittest discover -s evaluation/tests
PYTHONPATH=simulator:evaluation python3 -m unittest discover -s simulator/tests

# backend
(cd backend && .venv/bin/pytest -q)

# infra (CDK synth in both egress modes + fail-fast paths)
scripts/verify_phase2.sh

# resilience / temporal / audit — needs Docker, regenerates the RTO figure
scripts/verify_phase3.sh
```

Expected at last check: backend **136 passed / 5 skipped**, sim+eval **76**,
consolidation **14 + 1 skipped**, infra **48**, db **16**, integration **1**,
web **47** plus `tsc` and lint.

`evaluation/reports/phase2.json` must regenerate **byte-identically** unless a
change was intended — it is the committed scorecard.

---

## 7. Local environment gotchas

Recorded because each one has cost time at least once:

- **`backend/.venv` needs `pip install -e '.[test]'`** — the extra is `test`,
  not `dev`. `.[dev]` fails.
- **`infra/.venv`** is gitignored and must be recreated on a fresh clone;
  `verify_phase2.sh` depends on it.
- **`pnpm` blocks on a Corepack download prompt** non-interactively. Set
  `COREPACK_ENABLE_DOWNLOAD_PROMPT=0`.
- **`boto3` is only in `backend/.venv`**, not system Python (see §2).
- Local cluster data is disposable — everything is reproduced from migrations
  plus bootstrap, so `docker compose down` between sessions is safe.

---

## 8. Deferred — explicitly not doing before submission

Tracked in [`research/learning/10-technical-gaps.md`](../research/learning/10-technical-gaps.md),
listed here so nobody mistakes them for oversights: CI running the verifiers, app-level tracing, C-SPANN scale numbers,
circuit breakers, CSP nonces, CIS 6.4 idle timeout, scheduled S3 backups.
