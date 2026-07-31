# Reality Charter — only real, nothing staged

**The rule:** every number, claim, and on-screen value in this project must be **REAL** — produced by
the actual system doing the actual thing, reproducibly. No rigging, no leakage, no hardcoded outcomes,
no staged UI, no unmeasured claims. If it isn't real yet, it is labeled **"not yet measured,"** never
estimated, illustrated, or inflated.

This charter governs all remediation of the audit's evaluation-integrity findings and any future work.

## 1. Definitions

- **Real:** an output that comes from executing the real system (real DB, real cluster, real agent) and
  can be reproduced by re-running a named script. Every published number cites the script/artifact that
  produced it.
- **Legitimate test scaffolding (allowed):** a *controlled test environment* (the incident simulator is
  a fair, disclosed test world), and *unit-test doubles* used ONLY to exercise code paths. These are
  fine — but they may **never** be the source of any external/published number or claim.
- **Forbidden fake (remove on sight):**
  - a **handicapped baseline** — a comparison arm deliberately made worse to flatter our system;
  - **answer-key leakage** — planting the exact answer in the input the system is supposed to figure out;
  - **hardcoded outcomes** — metric values, learning curves, or results typed in rather than emerging
    from execution;
  - **staged UI** — on-screen values hardcoded to look live;
  - **unmeasured claims** — any number presented as measured that wasn't produced by a real run.

## 2. Rules (strict — no exceptions)

- **R1 — Provenance.** No number is published unless a named, reproducible script produced it from the
  real system. State the script and the artifact next to the number.
- **R2 — Fair baselines.** Comparison baselines must be *competent* — the best honest memoryless
  behavior, never handicapped. If our system can't beat a competent baseline, that is the finding.
- **R3 — No leakage.** The thing being measured (the "answer") must not be planted in the input the
  system reads. Retrieval must find *similar* prior cases and the system must *adapt* them, not copy a
  literal template that equals the oracle's required action.
- **R4 — No hardcoded outcomes.** Metrics, learning curves, and results must emerge from actual
  execution. A constant dressed as a measured trend is forbidden.
- **R5 — Real failure.** A resilience/failover proof must exercise the *actual* failure it claims:
  kill the real leaseholder/quorum path, measure real recovery, verify data *during* the outage.
  Measuring a normal write to a surviving node and calling it "recovery time" is forbidden.
- **R6 — Honest UI.** The console shows only real data. When data is absent, show empty / `—` — never a
  fabricated placeholder presented as live.
- **R7 — Model-dependent claims wait for the model.** Any claim about the *agent's decision quality*
  (MTTR delta, wrong-action rate, first-action accuracy) requires the **real model (Bedrock)**. Until
  the real run exists, these are marked **"not yet measured (pending real-agent run)"** — not estimated.
- **R8 — Doubles are for tests only.** Test doubles/fakes are allowed for unit tests, must be clearly
  named as such, and never source any external claim.
- **R9 — Tag every claim.** Every number in README/docs/demo is tagged with how it was produced:
  `[real-run: <script>]` or `[pending real run]`. No ambiguous claims.

## 3. What is real *today* vs. *pending the real agent*

| Claim | Can be real today? | How |
|-------|--------------------|-----|
| **Retrieval quality** (recall@k, precision, nDCG) | ✅ **Yes** | Property of the embedding + C-SPANN index + ranker over a corpus *with hard negatives*. No LLM needed. Real number, expected < 1.0. |
| **Temporal validity** (uses currently-valid fact) | ✅ Yes | Independent check over real bitemporal recall, hard cases. |
| **Consistency** (read-your-writes, atomicity, cross-agent) | ✅ Yes | Real DB behavior. |
| **RPO / RTO under region failure** | ✅ Yes | Real multi-region cluster, **leaseholders pinned to the killed region**, data verified *during* outage. |
| **MTTR delta, wrong-action rate, first-action accuracy** (agent decision quality) | ❌ **No — pending real Bedrock** | Requires the real model reasoning over retrieved memory vs a competent memoryless baseline. Marked "not yet measured" until the real-agent run. |

## 4. How to approach (the process every fix follows)

1. **Inventory** every number/claim/on-screen value.
2. **Trace** each to its source.
3. **Judge** the source against §1: real / legitimate-scaffold / forbidden-fake.
4. **Fix the source** to be real (competent baseline, hard negatives, real failover, real data) — or, if
   it depends on the real model, **remove the number** and mark it "pending real run."
5. **Reproduce** the number from the real run; cite the script.
6. **Publish only** what is real and tagged (R9). Delete everything else.

## 5. Non-negotiable

We would rather show a **smaller true number** (or an honest "pending") than a larger fake one. A number
a reviewer can debunk is worse than no number. The engineering is real; the *claims* must match it
exactly.
