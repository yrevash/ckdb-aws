# Postmortem deterministic evaluation

This directory implements the original controlled A/B methodology: two
responder arms face the same fixed incident stream, and the only experimental
variable is persistent procedural memory.

- `with_memory` performs deterministic text retrieval over a procedural-memory
  fixture and executes the best matching runbook.
- `cold_start` sees only the current incident observation and follows generic
  diagnostic rules. It eventually reaches the same canonical remediation but
  pays the simulator's wrong-action penalty first.
- The simulator's family labels and resolution oracle are never included in
  `IncidentObservation` and neither responder imports the oracle.

The corpus contains one adjudicated gold memory for each known family plus
twelve distractors. Each non-novel family has at least three live occurrences;
`F10_NOVEL` deliberately has no memory and has two abstention cases. The F2
slow-query near-miss can retrieve the pool procedure at the ANN stage, but a
structured applicability check must reject it before authorization.

## Run

No third-party packages are required:

```sh
PYTHONPATH=simulator:evaluation python3 -m postmortem_eval \
  --output evaluation/reports/phase2.json
python3 -m unittest discover -s evaluation/tests -v
```

The JSON report contains:

- recall@1, recall@5, recall@10, precision@10 and nDCG@10;
- abstention accuracy;
- near-miss safe-rejection and pool-runbook authorization rate;
- median/p90 MTTR and actions to resolution;
- first-action accuracy and wrong-action count;
- MTTR/accuracy/actions learning curves grouped by occurrence number;
- failed orders and failed-order value;
- escalations;
- deterministic token and cost proxies;
- per-incident records for both arms;
- direct with-memory versus cold-start deltas.

## Interpretation

MTTR and order impact are simulated values and should only be presented as a
relative controlled comparison. The token proxy is not a provider bill: it is a
stable workload estimate using the explicitly reported rate of `$0.000003` per
token. Backend integration can implement the `Responder` protocol and reuse the
same harness without importing these fixture responders.
