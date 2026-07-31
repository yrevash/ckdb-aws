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

## Interpretation

Retrieval and temporal-validity numbers are real properties of the ranker and
may be cited directly, tagged with the producing script. The deterministic MTTR,
token and order figures under `decision_quality.mechanism_check` are simulator
plumbing values for regression only and must **never** be presented as a
performance comparison or an MTTR-improvement headline. Backend integration can
implement the `Responder` protocol and reuse the same harness with the real
agent to populate the pending decision-quality metrics.
