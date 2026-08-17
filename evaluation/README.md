# Postmortem deterministic evaluation

This directory produces the Postmortem evaluation scorecard under the Reality
Charter (`docs/reality/00-reality-charter.md`). Every number it emits is tagged
with how it was produced: `status: "measured"` with a `produced_by` script, or
`status: "pending_real_agent_run"` when it depends on the real Bedrock agent.

The report has three sections:

- **`retrieval` — REAL today.** recall@1/@5/@10, precision@10 and nDCG@10 of the
  memory ranker over a corpus that contains **hard negatives** (semantically
  close but wrong prior incidents, carrying no authorized action). Because the
  correct prior case now competes with look-alikes, recall@1 and nDCG@10 sit
  **below 1.0** — that is the honest, correct measurement. Also includes
  novel-family abstention accuracy and the F2 slow-query near-miss, which is
  rejected purely by the real similarity threshold (no fixture-tuned phrase).
- **`temporal_validity` — REAL today.** Whether the bitemporal-aware ranker
  applies the currently-valid fact rather than a superseded one, across two
  environment-migration families. The expected answer is determined
  **independently** from the simulator oracle's ground-truth required action —
  not by re-running the responder's own valid_from/valid_to predicate — so a
  broken validity window is caught, not masked.
- **`decision_quality` — PENDING the real agent.** MTTR delta, first-action
  accuracy and wrong-action rate are agent decision-quality metrics. Per Reality
  Charter R7 they require the real Bedrock agent reasoning over retrieved memory
  versus a competent memoryless baseline, so **no improvement figure is emitted
  here**. The deterministic `with_memory` and `competent_baseline` arms are
  retained only as a `mechanism_check`: a regression harness confirming the
  simulator/replay is deterministic and that a *competent* (not handicapped)
  baseline reaches resolution on the same stream. On this deterministic toy
  world the baseline ties the memory arm — which is exactly why a real-agent run
  is required before any decision-quality benefit can be claimed.

The `with_memory` arm performs deterministic text retrieval over the procedural
memory fixture and executes the best matching runbook. The `competent_baseline`
arm sees only the current incident observation and applies the best honest
first-line remediation, abstaining (paging a human) on ambiguous or unknown
signals — it never plays a deliberately-wrong action. The simulator's family
labels and resolution oracle are never included in `IncidentObservation`, and
neither responder imports the oracle.

## Run

No third-party packages are required:

```sh
PYTHONPATH=simulator:evaluation python3 -m postmortem_eval \
  --output evaluation/reports/phase2.json
python3 -m unittest discover -s evaluation/tests -v
```

## Measuring decision quality for real (`real_agent.py`)

`postmortem_eval.real_agent` is the harness that answers the one question the
deterministic run cannot: **does retrieved memory make the agent better?** It
replays the identical incident stream twice through the real Bedrock model —
once with the top-k retrieved memories in context, once without — and scores
the difference.

**This is the one thing here that needs an interpreter with `boto3` and live
AWS credentials.** Everything else in this directory runs on a bare system
`python3`. Use the backend virtualenv, which already has `boto3`:

```sh
# 1. authenticate (interactive — do this yourself, once per session)
aws configure                     # or: aws sso login --profile <profile>
export AWS_PROFILE=<profile> AWS_REGION=us-east-1

# 2. one-call smoke test BEFORE the full run — confirms credentials, region
#    and Bedrock model access in ~2s instead of failing 40 incidents deep
backend/.venv/bin/python -c "import boto3; c=boto3.client('bedrock-runtime'); \
r=c.converse(modelId='us.anthropic.claude-sonnet-4-6', \
messages=[{'role':'user','content':[{'text':'reply with OK'}]}], \
inferenceConfig={'maxTokens':16}); \
print(r['output']['message']['content'][0]['text'], r['usage'])"

# 3. the real run (~58 Converse calls)
PYTHONPATH=simulator:evaluation backend/.venv/bin/python -m postmortem_eval.real_agent \
  --output evaluation/reports/decision-quality.json

# 4. fold the measured block into the main scorecard (system python3 is fine)
PYTHONPATH=simulator:evaluation python3 -m postmortem_eval \
  --decision-quality evaluation/reports/decision-quality.json \
  --output evaluation/reports/phase2.json
```

Two things fail *after* the run starts if they are not right, which is what the
smoke test in step 2 exists to catch:

- **Bedrock model access** to `us.anthropic.claude-sonnet-4-6` is request-gated
  per model in the Bedrock console. A fresh account does not have it.
- **`us.anthropic.*` is the US cross-region inference profile**, so `AWS_REGION`
  must be a US region. Point it elsewhere and every call fails.

Override the model with `--model-id` (it defaults to
`POSTMORTEM_REASONING_MODEL_ID`, matching what the backend actually ships).

**The controlled variable is exactly one thing: memory in context.** Both arms
use the same model, the same system prompt, the same action schema, the same
service/dependency catalog, the same temperature, and the same scenario stream
in the same order. The baseline is not handicapped — it is the same model with
the same freedom to abstain, missing only the institutional memory of prior
incidents. So the supported claim is precise:

> Given retrieval measured at the recall@1 this report publishes, putting those
> retrieved memories in the agent's context changes its decisions by *X*.

Not "our vector search is good" (that is the `retrieval` section) and not
"Claude is good" (both arms are Claude).

Three properties worth knowing before quoting the number:

- **MTTR is compared pairwise**, over incidents *both* arms resolved. Incidents
  only one arm resolved are reported separately as a resolution-rate delta
  rather than averaged away — otherwise an arm that only fixes the easy
  incidents would look fast. Neither figure means anything without the other,
  so both are always emitted, and when no shared resolved set exists the
  reduction is emitted as `null` rather than fabricated.
- **Model latency is included in MTTR and also reported separately**
  (`mttr_reduction_percent_excluding_model_latency`), so a memory-heavier
  prompt can never be mistaken for slower remediation.
- **Retrieval is the harness's text ranker, not production C-SPANN.** That is
  deliberate — it keeps the MTTR figure comparable to the recall@1/nDCG@10 in
  the same report — and it is tagged as such in `method.retriever`.

Abstention is a correct answer, not a forfeit: the `F10_NOVEL` oracle *requires*
`no_op_page_human`, so an arm that pages a human on a genuinely novel incident
resolves it and an arm that guesses does not. Unresolved incidents, unexecutable
plans, and unparseable output are all recorded as outcomes rather than raised —
a real agent failing is the measurement, not a harness bug.

## Interpretation

Retrieval and temporal-validity numbers are real properties of the ranker and
may be cited directly, tagged with the producing script. The deterministic MTTR,
token and order figures under `decision_quality.mechanism_check` are simulator
plumbing values for regression only and must **never** be presented as a
performance comparison or an MTTR-improvement headline.

`decision_quality` reports `status: "pending_real_agent_run"` until a real
`real_agent.py` report is supplied via `--decision-quality`. That switch cannot
be flipped any other way: the loader refuses a missing file and refuses a report
that is not itself tagged `measured`, so a verifier that asked for the number
can never silently publish "pending" instead. `evaluation/tests/test_real_agent.py`
doubles the Bedrock client to prove the harness is fair, leak-free and
crash-proof — it never produces a decision-quality figure (Reality Charter R8).
