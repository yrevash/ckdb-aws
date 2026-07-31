# Postmortem — Final Demo Recording Script (<3 min)

**Total budget: 178 seconds.** Screen-recorded (not live-on-stage) so timing and the region kill are
controlled. Derived from [`research/postmortem/06-demo-and-ux.md`](../research/postmortem/06-demo-and-ux.md)
Part B; order strictly follows charter §9. **Ready to record Aug 1** once the live AWS deployment +
demo URL are up.

**The one job of this video:** make *memory* the most visible thing on screen. A judge watching 30
seconds with the sound off must be able to point and say "the agent remembered a past incident, and
that memory changed what it did — and the memory survived the outage it was fighting."

**Recording rules (from 06 §B4):**
- **Failover is pre-recorded from a REAL region kill**, cut into the video. The kill is genuine; only
  the recording is pre-made (fully legitimate for a video deliverable). **Never gamble a live kill
  inside a 3-min cut.** The local proof (Phase 3 Track A: RPO=0 every run, RTO 0.045–0.099s) already
  establishes this works; Aug 1 is about capturing clean takes with real timestamps.
- **Deterministic-replay fallback (camera-safe):** the demo runs from a fixed scenario so similarity
  scores, counters, and timings are reproducible take-to-take. If the live cluster misbehaves during
  capture, the console can render from a recorded event stream (same event schema) so the *UI story* is
  never blocked — **but the headline numbers shown (RPO, RTO, txn id, commit ts) must come from a real
  prior kill, never fabricated.**
- Record locally (not conference Wi-Fi); keep a hot-standby cluster to re-provision between takes.

## Shot-by-shot storyboard

| Time | On screen | Voiceover | Judging criterion |
|---|---|---|---|
| **0:00–0:14** | Dark console cold-open. SEV-1 alert card lands: `p99 4.2s on checkout-api`, pulsing coral. Title card: **Postmortem — an on-call SRE agent with persistent memory.** | "3am. Checkout is failing. Most on-call AI agents start every incident from zero. This one doesn't — because its memory lives in CockroachDB." | Hook / Real-World Impact |
| **0:14–0:58** | Agent posts "recalling memory…". The **Recall Thread** animates from CASE-2041 (center) to **CASE-1878 · similarity 0.94** (right), terminating in the similarity dial + runbook **RB-207**. Recalled card shows the fix that *actually worked*: roll back the canary — **not** scale up. Agent's proposed action changes to `rollback_deploy(#5120)`. Hold on the thread + dial. | "It recognizes this. A near-identical incident four months ago — 0.94 similar. Last time, scaling up made it worse; rolling back the canary fixed it. So the agent doesn't guess. It remembers, and it changes what it does." | **Memory Design / Real-World Impact** — *memory changes the action* |
| **0:58–1:28** | SRE clicks **Run**. The Action card expands into the **Transaction Envelope**: one `BEGIN…COMMIT` wrapping a `recall-gold` memory write + an `act-cyan` rollback write, one commit hash, `steady-green` commit pulse. The new episodic card materializes at the top of the Memory Timeline: `written 40ms ago · ✓ recalled by agent@eu-west · 0ms staleness`. | "The fix and the memory of the fix commit in one transaction, in one database. Two systems can never disagree — and any other agent, in any region, can read it instantly." | **Technical Implementation** — *memory + action are one transaction* + read-your-writes |
| **1:28–2:22** | **MONEY SHOT.** Split view: left, a `ccloud` terminal issues the region kill; right, the console. `us-east` dot dies coral, `REGION DOWN` banner, **RTO timer counts up… resolves (<10s)**, and **`RPO 0 rows` never moves.** Center transcript keeps streaming — the agent, mid-remediation, **re-recalls the memory it wrote seconds before the kill** (read from a surviving replica) and confirms the rollback held. Failover Theater overlay shows Raft re-electing a leaseholder with the payload intact. Hold on `RPO 0`. | "Now watch. We kill the region the database is running in — live. No runbook, no human failover. Recovery in under ten seconds. Zero rows lost. And the memory it wrote moments ago? Still there — read from a surviving replica. The agent's memory survived the very outage it was fighting." | **Production Readiness** — *live region kill, RPO=0* |
| **2:22–2:48** | Fast time-lapse: a Lambda/changefeed tick; three raw CASE cards collapse into one **CONSOLIDATED** procedural card — **RB-207: "checkout p99 after canary → roll back first."** Cut back to Seg 1's recall citing RB-207 (callback). | "And while everyone slept, a background job distilled last night's raw incidents into a reusable runbook — the exact one it used tonight. The memory doesn't just persist. It improves." | **Creativity & Originality** — *sleep-time consolidation* |
| **2:48–2:58** | Counters frozen: `RPO 0 · RTO <10s · 1 store`. Architecture one-liner. | "One database for memory and the operations it acts on. Always on. Never forgets. That's Postmortem." | Close / all criteria |

## The single money shot (design for maximum impact)

**`RPO 0` holding still while a region dies, and the agent's sentence never breaks.** The counter lives
in the persistent top bar (never cut away), is the only large `Space Grotesk` numeral held, and the
transcript deliberately streams the agent's next line *during* the kill so the viewer sees continuity
of thought through catastrophe. The line "the agent's memory survived the outage it was fighting" is the
whole thesis in one frame. Nothing competes with it for visual weight.

## Pre-record checklist (Aug 1)

- [ ] Live AWS deployment up; public demo URL reachable and tested (CSP/fonts/assets OK).
- [ ] Deterministic scenario loaded (CASE-2041 recurrence, CASE-1878 @0.94, RB-207).
- [ ] Real region-kill rehearsed off-camera; 3–5 clean takes captured with genuine timestamps + real
      `RPO 0` and the actual RTO number to quote in Seg 3 VO and the Seg 5 freeze frame.
- [ ] Hot-standby cluster ready to re-provision between takes (`ccloud`).
- [ ] Reduced-motion pass verified (thread/counters still legible).
- [ ] Final cut ≤ 178s. **If over budget, cut Seg 4 (consolidation) first** — it is the stretch beat.
- [ ] Headline numbers on screen all sourced from a real kill, never fabricated.

## Notes on truthfulness

- Seg 1–2 (recall changes the action; one-transaction commit) and the read-your-writes chip are
  **locally proven today** (Phase 1/2 green; Phase 3 Track A freshness/cross-agent probes).
- Seg 3 (RPO=0 / RTO<10s) is **locally proven** on the 9-node simulated multi-region cluster. The
  on-camera capture uses a **real** kill on the deployed cluster (Aug 1); the RTO number quoted must be
  the measured value from that take, not the local micro-benchmark.
- Seg 4 (consolidation) logic is implemented and tested locally; the live changefeed→Lambda path is
  **pending Aug 1**. If that path is not live at record time, render Seg 4 from the deterministic replay
  stream and keep it clearly a distillation of real prior episodes.
