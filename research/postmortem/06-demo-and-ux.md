# 06 — Demo Narrative & Incident-Console UX

**Owner:** (U) Web incident console + the <3-min demo narrative.
**Obeys:** `00-charter.md`. **Consumes:** memory schema (`01`), agent tool events (`02`),
scenarios/dataset (`05`). **Grounding:** `../00-context-and-strategy.md`,
`../deep-dive/08-synthesis-where-cockroachdb-wins.md`.

> **The one job of this surface:** make *memory* the most visible thing on screen. A judge watching
> for 30 seconds with the sound off must be able to point at the UI and say "the agent remembered a
> past incident, and that memory changed what it did." Everything below serves that sentence and the
> three wedge proofs (single-store · read-your-writes · RPO=0).

---

## PART A — The Web Incident Console

### A0. Design plan (per `frontend-design`, reviewed against defaults)

**Subject, audience, single job.** The subject is an on-call SRE's incident console at 03:00 during a
SEV-1 on a checkout/payments path. The audience is a skeptical platform engineer (and, at the
hackathon, a skeptical judge). The page's single job: **show the agent recalling proven institutional
memory and acting on it, and prove that memory survives the outage it is fighting.**

**The framing metaphor — a flight recorder / case file.** "Postmortem" is forensic language. So the
console is built as a *case file*: each incident is a **CASE**, the memory panel is the **prior-case
record**, and recall is literally *pulling a matching prior case and the fix that worked*. This frame
(not a generic "chatbot + sidebar") drives every structural decision below and keeps the tone
**calm-under-pressure**: muted until it matters, vivid only at the moments that carry the thesis.

**Color — 6 named tokens.** An ops tool is legitimately dark (terminals, Grafana, PagerDuty), but the
"near-black + one acid-green accent" look is an AI default, so we deliberately do *not* do that. We use
a layered cool-ink base (never pure black) and spend the palette on a **semantic warm/cool split that
encodes the thesis**: memory is the *warm* human thread running through a *cold* crisis.

| Token | Hex | Role |
|---|---|---|
| `ink-900` | `#0E1220` | app base — cool blue-plum ink, not flat black |
| `ink-700` | `#171D2E` | panel surfaces / raised cards |
| `recall-gold` | `#E7B24C` | **the signature accent** — memory, recall, remembered knowledge (warm) |
| `act-cyan` | `#54D2C4` | agent actions on the live system (cool, operational) |
| `steady-green` | `#5FA98A` | consistency / healthy / committed — a *calm* green, deliberately not acid |
| `sev-coral` | `#F0685F` | severity + the region-kill — reserved, used sparingly for maximum weight |

Text: `#ECE7DA` warm off-white on ink (memory-warm even in the type), muted `#8A93A8` for metadata.
The emotional core is one contrast: **recall-gold is the only warm thing in a cool room.** When memory
lights up, the eye cannot miss it.

**Type — 3 deliberate roles (not Inter-default).**
- **Thesis numerals — `Space Grotesk`.** Reserved for the numbers that *are* the argument: `RPO 0`,
  `similarity 0.94`, the RTO timer, `1 txn`. These get large, tabular, confident weight.
- **UI / body — `IBM Plex Sans`.** Chosen for genuine engineering/mainframe heritage — reads as an
  instrument panel, not a marketing site.
- **Telemetry / SQL / logs — `IBM Plex Mono`.** The SRE vernacular. Transaction envelopes, log lines,
  and topology labels live here. `IBM Plex Sans Condensed` for dense eyebrow labels.

This Plex-superfamily + Space-Grotesk-for-numerals pairing is coherent, subject-true, and avoids the
Inter/Geist default.

**Signature element — the Recall Thread.** When the agent recalls a memory, a **forensic evidence card**
slides out of the right-hand timeline and a thin animated **thread** draws itself from the *current*
incident (center) to the *recalled prior case* (right), terminating in a **similarity dial** and the
**runbook** that case produced. You physically watch memory get pulled in and attach to the live
decision. This — not a chat bubble — is what the page is remembered by. Boldness is spent here; every
other surface stays quiet and dense.

**Self-critique vs. the three AI defaults.** (1) Not cream/serif/terracotta — it's a dark ops
instrument. (2) It risks the "near-black + one acid accent" default, so we explicitly rejected acid
green, used a cool-ink (not black) base, and split the accent into a *semantic* warm/cool pair that
means something. (3) Not broadsheet — it's a live three-rail console with motion, not columns of type.
The numbered structure we *do* use (case IDs, the demo timeline) is legitimate because incidents and
the timeline are genuinely ordered sequences.

---

### A1. Layout — the three-rail console

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│  TOP BAR · SYSTEM STATE                                                                     │
│  postmortem ▮  CASE-2041 checkout-api  SEV-1        REGIONS ● us-east ● us-west ● eu-west   │
│                                                     RPO  0 rows   RTO —   cluster: HEALTHY  │
├───────────────┬──────────────────────────────────────────────┬─────────────────────────────┤
│ LEFT RAIL     │  CENTER · INVESTIGATION (ChatOps)             │  RIGHT · MEMORY TIMELINE     │
│ incident feed │                                               │  (recall inspector)          │
│               │  ┌─ alert ─────────────────────────────────┐  │                              │
│ ▸ CASE-2041 ●│  │ 03:01 p99 latency 4.2s on checkout-api   │  │  ◆ RECALLED  0.94 ▓▓▓▓▓░    │
│   checkout   │  └──────────────────────────────────────────┘  │  CASE-1878 · 14 Mar          │
│   SEV-1 4m   │                                               │  "checkout p99 spike after   │
│               │  agent ▸ recalling memory…                    │   canary deploy #4471"       │
│ ▸ CASE-2038  │  ╭ agent ─────────────────────────────────╮   │  fix: rollback canary →      │
│   billing    │  │ I've seen this. CASE-1878 (0.94): same   │←──┼──── RECALL THREAD ──────┐   │
│   resolved   │  │ p99 shape after a canary. That was fixed │   │  runbook RB-207 ▸       │   │
│               │  │ by rolling back the canary, not scaling. │   │  ┌───────────────────┐ │   │
│ ▸ CASE-2035  │  │ Proposing: rollback deploy #5120.        │   │  │ similarity dial   │◀┘   │
│   search     │  ╰──────────────────────────────────────────╯   │  │      0.94         │     │
│   resolved   │                                               │  └───────────────────┘     │
│               │  ┌ ACTION · proposed ───────────────[Run]┐   │                              │
│ ── filters ──│  │ tool: rollback_deploy(svc=checkout,     │   │  written 40ms ago            │
│ sev · svc ·  │  │       to=#5119)   memory: episodic write │   │  ✓ recalled by agent@eu-west │
│ region       │  └──────────────────────────────────────────┘  │    read-your-writes · 0ms    │
│               │                                               │  ─────────────────────────   │
│               │  [ system-state / topology  ⌄ expand ]        │  ◇ semantic · procedural …   │
└───────────────┴──────────────────────────────────────────────┴─────────────────────────────┘
```

- **Top bar (System State)** is always visible: current case + severity, the **3-region topology dots**,
  and the three thesis counters — `RPO`, `RTO`, `cluster`. This bar is where the failover money shot
  happens; it stays on screen the whole demo so the proof is never off-frame.
- **Left rail — Incident Feed.** The case list (active + resolved). Active SEV pulses `sev-coral`;
  resolved are muted. Filters by severity/service/region. This is where "the same outage recurs" is
  legible — you can see CASE-1878 sitting resolved in history before it gets recalled.
- **Center — Investigation (ChatOps).** The SRE↔agent transcript, but structured: **alert cards**,
  **agent reasoning bubbles**, and inline **Action cards** (propose → Run → result). The Action card is
  where single-store atomicity is shown (A4.1). A collapsible **system-state / topology** drawer lives
  at the bottom of center.
- **Right — Memory Timeline / Recall Inspector.** The star. Streams memory events as forensic cards:
  **RECALLED** (with similarity dial, source case, runbook), **WROTE** (episodic), **CONSOLIDATED**
  (procedural runbook born overnight). Clicking a card opens the **Recall Inspector**: the query vector
  summary, top-k matches with scores, the bitemporal validity of the fact, and the scope
  (service/tenant) it applies to.

### A2. Key views (screen inventory)

1. **Incident Feed** (left rail) — ordered case list, severity/service/region, recurrence visible.
2. **Investigation / ChatOps** (center) — alert → recall → reason → **Action card** → result transcript.
3. **Memory Timeline** (right) — live stream of recall/write/consolidate events as evidence cards.
4. **Recall Inspector** (modal/expand off a timeline card) — *why* this memory: top-k + scores, source
   incident, runbook, bitemporal validity, scope. This is the "show your work" view for Memory Design.
5. **System-State + Service Topology** (center drawer) — service graph (checkout → payments → ledger),
   live SLO/latency sparklines, deploy markers; the operational data the agent mutates.
6. **Region / Failover Status** (top bar, expandable) — 3-region Raft view, leaseholder placement, the
   `RPO`/`RTO`/`cluster` counters; expands to a full **Failover Theater** overlay for the money shot.
7. **Transaction Envelope** (inline in Action card + Inspector) — the single `BEGIN…COMMIT` proof.

### A3. Motion & interaction (deliberate, reduced-motion-safe)

- **The Recall Thread** draws in ~600ms from current case to the recalled card; the similarity dial
  fills to its score; the recalled card's runbook chip glows once. This is the one orchestrated moment.
- **Commit pulse:** when an Action card commits, the transaction envelope flashes `steady-green` once
  and the just-written memory card **materializes at the top of the timeline** — visually linking write
  and instant recall.
- **Failover:** on region kill, the dead region dot goes `sev-coral` and dims; a `REGION DOWN` chip
  appears; the RTO timer counts up then snaps to resolved; **RPO stays pinned at `0`** the entire time.
  The center transcript **does not stall** — the agent's next line streams in mid-failover.
- All motion respects `prefers-reduced-motion` (thread appears without animating; counters still update).

### A4. How each wedge proof is made VISIBLE

#### A4.1 Single store — "memory and the action are one transaction"
The **Action card**, on Run, expands into a **Transaction Envelope** rendered as one bracket around two
differently-colored rows — a `recall-gold` memory write and an `act-cyan` operational write — under a
single commit:

```
┌ TRANSACTION envelope ────────────── 1 txn · 1 commit ─┐
│ BEGIN;                                                 │
│  ● memory   INSERT INTO episodic_memory (…embedding…)  │  ← recall-gold
│  ● action   UPDATE deploys SET active=#5119 WHERE …    │  ← act-cyan
│ COMMIT;   txn=8f2a… ts=2026-07-30T03:02:11.470Z ✓      │  ← steady-green pulse
└────────────────── same store · they can never disagree ┘
```
One bracket, one commit hash, two kinds of data. The caption states the claim in the user's words:
*"same store · they can never disagree."* No two-database hand-waving is possible on screen.

#### A4.2 Read-your-own-writes — "written 40ms ago, already recalled elsewhere"
The instant the envelope commits, the new episodic card appears at the top of the Memory Timeline with a
freshness chip: **`written 40ms ago`** and, beneath it, **`✓ recalled by agent@eu-west · 0ms staleness`**.
A second (consolidator/eu-west) agent identity querying the same memory proves cross-node, cross-region
read-your-writes with no lag — the "I saved a memory and can't find it" bug, visibly killed.

#### A4.3 RPO=0 region survival — the money shot
The top-bar counters are the proof surface. During the live kill: `us-east` dot dies red, `REGION DOWN`
banner, RTO timer runs (target < 10s) then resolves, and **`RPO 0 rows`** never moves. Immediately
after, the agent **re-recalls the memory it wrote seconds before the kill** — read from a surviving
replica — and continues acting. The Failover Theater overlay shows Raft replicas re-electing a
leaseholder with the payload still intact. Caption: *"the on-call agent's memory survived the outage it
was fighting."*

---

## PART B — The <3-Minute Demo Video

**Total budget: 178s.** Shot on a rehearsed, deterministic scenario from `05`. Screen-recorded (not
live-on-stage) so timing and the region kill are controlled. Voiceover is tight; the UI does the
persuading. Order strictly follows charter §9.

### B1. Shot-by-shot storyboard

**Seg 0 — Cold open (0:00–0:14).** On screen: dark console, an alert card lands — `p99 4.2s on
checkout-api`, SEV-1 pulsing. VO: *"3am. Checkout is failing. Most on-call AI agents start every
incident from zero. This one doesn't — because its memory lives in CockroachDB."* Title card:
**Postmortem — an on-call SRE agent with persistent memory.**

**Seg 1 — Memory is load-bearing (0:14–0:58).** *[charter §9.1]* The agent posts "recalling memory…";
the **Recall Thread** draws to **CASE-1878 · similarity 0.94**; the recalled card shows the *fix that
actually worked last time* (roll back the canary — **not** scale up). Agent's proposed action *changes*
because of the memory: it proposes `rollback_deploy(#5120)` instead of the naive autoscale. VO: *"It
recognizes this. A near-identical incident four months ago — 0.94 similar. Last time, scaling up made
it worse; rolling back the canary fixed it. So the agent doesn't guess. It remembers, and it changes
what it does."* **This is the load-bearing beat — hold on the thread + the dial.**

**Seg 2 — One transaction (0:58–1:28).** *[§9.2]* SRE clicks **Run**. The Action card expands into the
**Transaction Envelope**: one `BEGIN…COMMIT` wrapping the `recall-gold` memory write and the `act-cyan`
rollback, one commit hash, `steady-green` pulse. The new memory card materializes in the timeline:
`written 40ms ago · recalled by agent@eu-west · 0ms`. VO: *"The fix and the memory of the fix commit in
one transaction, in one database. Two systems can never disagree — and any other agent, in any region,
can read it instantly."*

**Seg 3 — Kill a region live (1:28–2:22) — MONEY SHOT.** *[§9.3]* Split view: left, a `ccloud` terminal
issues the region kill; right, the console. `us-east` dot dies `sev-coral`, `REGION DOWN` banner, **RTO
timer counts up… resolves at 8.3s**, and **`RPO 0 rows` never moves.** The center transcript keeps
streaming — the agent, mid-remediation, **re-recalls the memory it wrote 6 seconds before the kill** and
confirms the rollback held. VO: *"Now watch. We kill the region the agent is running in — live. No
runbook, no human failover. Recovery in under nine seconds. Zero rows lost. And the memory it wrote
moments ago? Still there — read from a surviving replica. The agent's memory survived the very outage it
was fighting."* Hold on `RPO 0`.

**Seg 4 — Overnight consolidation (2:22–2:48).** *[§9.4, stretch]* Fast time-lapse: a Lambda/changefeed
tick; three raw CASE cards collapse into one **CONSOLIDATED** procedural card — **RB-207: "checkout p99
after canary → roll back first."** Cut to the agent citing RB-207 in Seg 1's recall (callback). VO:
*"And while everyone slept, a background job distilled last night's raw incidents into a reusable
runbook — the exact one it used tonight. The memory doesn't just persist. It improves."*

**Seg 5 — Close (2:48–2:58).** Architecture one-liner + counters frozen: `RPO 0 · RTO 8.3s · 1 store`.
VO: *"One database for memory and the operations it acts on. Always on. Never forgets. That's
Postmortem."*

### B2. Segment → judging-criteria map

| Segment | Primary criterion earned | Secondary |
|---|---|---|
| Seg 1 · load-bearing recall | **Real-World Impact** (MTTR: don't re-solve outages) | Memory Design (semantic+procedural recall) |
| Seg 2 · one transaction | **Technical Implementation** (single-store ACID, C-SPANN) | Memory Design |
| Seg 3 · region kill | **Production Readiness** (RPO=0 / RTO<10s live) | Technical Impl |
| Seg 4 · consolidation | **Creativity & Originality** (sleep-time + bitemporal runbook) | Memory Design (procedural frontier) |
| Whole arc | **Agentic Memory Design** — memory is the thing that makes the agent useful | — |

### B3. The single money shot (design for maximum impact)
**`RPO 0` holding still while a region dies, and the agent's sentence never breaks.** Everything is
composed to make this land: the counter is in the persistent top bar (never cut away), it is the only
`Space Grotesk` numeral held large, and the transcript deliberately streams the agent's next line
*during* the kill so the viewer sees continuity of thought through catastrophe. The emotional line —
*"the agent's memory survived the outage it was fighting"* — is the thesis of the whole product in one
frame. Nothing else in the video competes with it for visual weight.

### B4. Backup plan if live failover is risky on camera
1. **Default to a pre-recorded, real failover clip.** Rehearse the `ccloud` region kill on the actual
   cluster off-camera, capture 3–5 clean takes with genuine timestamps and a real `RPO 0`, and cut the
   best into the video. The kill is *real*; only the recording is pre-made (fully legitimate for a video
   deliverable). This is the primary path — never gamble a live kill inside a 3-min cut.
2. **Deterministic scenario harness.** The demo runs from a fixed scenario in `05` so counters,
   similarity scores, and timings are reproducible take-to-take.
3. **Hot-standby cluster.** Keep a second pre-provisioned cluster so a botched kill doesn't strand the
   shoot; re-provision via `ccloud` between takes.
4. **Telemetry replay fallback.** If the live cluster misbehaves during capture, the console can render
   from a recorded event stream (same event schema from `02`) so the *UI story* is never blocked — but
   the headline numbers shown must come from a real prior kill, not fabricated.
5. **Local capture, not conference Wi-Fi.** Record locally to remove network variance from the take.

---

## Required closing sections

### (A) Storyboard table

| Time | On screen | Voiceover beat | Criterion |
|---|---|---|---|
| 0:00–0:14 | SEV-1 alert lands, console cold-open, title card | "3am, checkout failing; this agent doesn't start from zero" | (hook) Real-World Impact |
| 0:14–0:58 | Recall Thread → CASE-1878 @0.94; action changes to rollback | "It remembers the fix that worked, and changes what it does" | **Memory Design / Impact** |
| 0:58–1:28 | Transaction Envelope: memory+action, 1 commit; `written 40ms · recalled @eu-west 0ms` | "Fix and memory commit in one transaction; any region reads it instantly" | **Technical Impl** |
| 1:28–2:22 | Region kill; `us-east` dies; RTO→8.3s; **RPO 0 holds**; agent re-recalls & continues | "Kill the region live — <9s, zero loss, memory survived the outage" | **Production Readiness** |
| 2:22–2:48 | Time-lapse: 3 raw cases → CONSOLIDATED RB-207; callback to Seg 1 | "Overnight it distilled incidents into the runbook it used tonight" | **Creativity** |
| 2:48–2:58 | Counters frozen `RPO 0 · RTO 8.3s · 1 store`; arch one-liner | "One store for memory and operations. Always on. Never forgets." | (close) all |

### (B) Console screen inventory
1. Incident Feed (left rail) · 2. Investigation/ChatOps (center) · 3. Memory Timeline (right) ·
4. Recall Inspector (modal/expand — *why* this memory) · 5. System-State + Service Topology (center
drawer) · 6. Region/Failover Status + Failover Theater (top bar/overlay) · 7. Transaction Envelope
(inline in Action card + Inspector).

### (C) Decisions & recommendations — frontend stack

| Choice | Recommendation | Why (fast for a small team, non-templated) |
|---|---|---|
| Framework | **Next.js (App Router) + TypeScript** | One app, streaming, easy deploy; team is TS-comfortable (charter §7). |
| Components | **shadcn/ui (Radix primitives) + Tailwind**, tokens overridden | Accessible primitives fast; custom tokens (A0) avoid the shadcn-default look. |
| Fonts | **`next/font`**: Space Grotesk (numerals), IBM Plex Sans + Mono (UI/telemetry) | Self-hosted, no CDN/CSP issues; the deliberate non-Inter pairing. |
| Data viz | **Custom lightweight SVG** for topology + Recall Thread; small sparkline lib (visx/uPlot) | The thread/dial are bespoke signature elements; keep deps minimal. |
| Motion | **Motion (Framer Motion)** with `prefers-reduced-motion` | Orchestrate the one signature moment; degrade safely. |
| Realtime | **SSE (or WebSocket) event stream** from the agent backend | Streams the `02` tool events (recall/act/record/commit/failover) into all three rails. |
| Data layer | **TanStack Query** + a small in-memory event store keyed by case | Simple, replay-friendly (supports the demo harness + backup plan B4.4). |
| Deploy | **Vercel** (or a container alongside the agent) | Fastest path to the required public demo URL. |

**Recommendation:** build the console as a **thin, event-driven visualizer** over the agent's tool-event
stream — it renders `02`'s events and `01`'s schema; it holds no business logic of its own. This keeps
it buildable in the ~4-week window and makes it faithful to what the agent actually did.

### (D) Interfaces I depend on
- **Memory schema (`01`)** — to render recall/write/consolidate cards and the Inspector, I need: table
  identities for **episodic / semantic / procedural**; the **embedding** field + **similarity score**
  returned by C-SPANN search; **bitemporal validity** (valid-from/updated-as-of, so the Inspector can
  show "facts evolve, not overwrite"); **scope** (service/tenant/region); and stable **IDs** for
  incidents ↔ memories ↔ runbooks so the Recall Thread can link current→prior→runbook.
- **Agent tool events (`02`)** — I need a **typed event stream** with these event kinds: `recall`
  (query summary, top-k results with scores, source incident id, runbook id), `reason`, `act` (tool
  name, args, target table, before/after), `record` (episodic write), **`transaction`** (txn_id, commit
  ts, the ordered statements — required for the Transaction Envelope), and `failover` (region, RPO, RTO,
  leaseholder change). Agent identity + region per event (for the read-your-writes chip).
- **Scenarios / dataset (`05`)** — the **deterministic demo script**: which prior incident (CASE-1878),
  which recurrence (CASE-2041), the exact similarity score, the runbook (RB-207), and the timed failover
  cue — so the storyboard, counters, and Recall Thread show real, reproducible content across takes.

**Interfaces I expose:** a **console event contract** (the union above) that `02` emits and `03`
transports — plus a **replayable event log** format the backup plan (B4.4) and demo harness both consume.

### (E) Risks & mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Live region-kill fails on camera | Loses the money shot | Pre-record real failover off-camera (B4.1); hot-standby cluster (B4.3); local capture (B4.5). |
| Video overruns 3:00 | Disqualifying / weakens cut | Hard 178s budget; **cut Seg 4 (consolidation) first** — it's the stretch beat. |
| Memory recall not legible on screen | Thesis doesn't land | The Recall Thread + similarity dial + warm/cool color split make recall the loudest thing; test with sound off. |
| Too dense / cognitive overload | Judges lose the plot | Progressive disclosure: only the active rail is fully lit; envelopes/inspector open on demand. |
| RPO=0 claim looks staged/unbelievable | Credibility hit | Show a *real* txn_id + commit ts + surviving-replica read; drive counters from actual cluster telemetry, never fabricated numbers. |
| Recall-gold/coral fail contrast or colorblind reads | Accessibility + legibility | Pair color with shape/label (dial %, `REGION DOWN` text), verify WCAG-AA contrast on ink, keyboard focus + reduced-motion. |
| Backend event schema (`02`) slips | Console can't render | Build against a mocked event log early (same contract); the console is a thin visualizer, decoupled from agent internals. |
| CSP/font/asset issues on the demo URL | Broken deploy | Self-host fonts via `next/font`; inline critical assets; test the public URL before submission. |

---

### 6-bullet summary
- **Console = a forensic "case file," not a chatbot:** three rails (Incident Feed · ChatOps
  Investigation · Memory Timeline) over a persistent System-State top bar, so the memory proof is never
  off-screen.
- **The signature is the Recall Thread:** a thread animates from the current incident to the recalled
  prior case with a similarity dial + the runbook it produced — making "memory is load-bearing" visible
  in one glance.
- **Deliberate, non-default aesthetic:** cool-ink base (not black), a semantic warm/cool accent split —
  `recall-gold` for memory, `act-cyan` for action — with Space Grotesk numerals + IBM Plex Sans/Mono;
  boldness spent only on the thread and the RPO counter.
- **Each wedge proof gets a dedicated UI surface:** single-store = the one `BEGIN…COMMIT` Transaction
  Envelope; read-your-writes = `written 40ms · recalled @eu-west 0ms` chip; RPO=0 = the top-bar counter
  that holds `0` through a live region kill.
- **<3-min video (178s)** follows charter §9 exactly: load-bearing recall → one transaction → **live
  region kill money shot** → overnight consolidation, each mapped to a judging criterion; money shot =
  `RPO 0` holding while the agent's sentence never breaks.
- **Buildable + safe:** Next.js + shadcn (retokenized) + SSE event stream as a *thin visualizer* over
  `02`'s tool events and `01`'s schema; failover is **pre-recorded from a real kill**, with a hot-standby
  cluster and deterministic replay harness as backups.
