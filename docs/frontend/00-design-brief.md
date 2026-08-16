# Frontend Rebuild — Design Brief

The current console is **cluttered and hard to understand.** We are rebuilding the **presentation from
scratch** — beautiful, calm, insight-first — while **keeping the honest data layer** (`web/lib/*.ts`
mappers + hooks) that the Reality work produced. Every pixel shows **real data**; nothing is fabricated.

## Non-negotiables

1. **Clarity over density.** The #1 failure of the old UI was clutter. Lead with the *insight*, not the
   raw dump. Generous whitespace, strong typographic hierarchy, one job per view.
2. **Only real data (Reality Charter `docs/reality/00-reality-charter.md`).** Charts/metrics come from
   the real sources below. Absent data → `—`. Model-dependent numbers (MTTR/decision-quality) are shown
   as **"pending real-agent run,"** never a fabricated value.
3. **Keep the honest data layer.** REUSE `web/lib/{events.ts,resilience.ts,temporal-drift.ts,evaluation.ts,
   mock-events.ts}` + `web/hooks/*` (these are the correct, de-rigged data contracts). Rebuild the
   *components, layout, and design* on top — do not rewrite the data mappers or reintroduce hardcoded
   values.
4. **Beautiful + distinctive.** Follow the `frontend-design` skill (intentional aesthetic, not a
   template) and the `dataviz` skill (chart system, accessible palette, light+dark).

## The real data → what insight/chart it drives

**A. Live incident stream** (`lib/events.ts` — SSE or replay): incident, recall (results w/ similarity
scores + component scores), reason (citations), act (tool + args), record, transaction (statements),
failover.
→ *Insight:* "memory changed the action." Charts: the perceive→recall→reason→act→record **timeline**;
recall candidates as **ranked similarity bars**; the one-transaction **envelope**.

**B. Resilience report** (`lib/resilience.ts` / `phase3-resilience.json`): `probes.{freshness,
cross_agent_visibility,atomicity,rto,rpo}` (status/value/unit); `node_liveness` (before/during/after +
detection/recovery seconds); `range_snapshot` + `leaseholder_region_counts` per region; `topology`
(regions, killed_region, primary_region, nodes_total).
→ *Insight:* "survives a real region kill." Charts: **node-liveness over the kill** (9→6→9 area/step
chart with the outage band), **RTO/RPO stat tiles**, a **region topology** view highlighting the killed
region, **leaseholder distribution** before/during/after (stacked), a **probe status grid**.

**C. Evaluation report v2** (`lib/evaluation.ts` / `phase2.json`): `retrieval` {recall@1=0.85, recall@5,
recall@10, ndcg@10=0.94, precision@10, hard_negative_count=9, abstention_accuracy}; `decision_quality`
{measured:false → **pending**}; `temporal_validity` (independent oracle check).
→ *Insight:* "retrieval is genuinely good (with hard negatives)." Charts: **recall@k** bars,
**nDCG gauge**, a **hard-negatives** callout, and an honest **"decision-quality: pending real agent"**
panel (no fake %).

**D. Temporal drift** (`lib/temporal-drift.ts`): fact `valid_from`/`valid_to` transitions.
→ *Insight:* "facts evolve, not overwrite." Chart: a **validity timeline** (the superseded → current
transition) with the agent choosing the currently-valid fix.

## Information architecture (keep it to a few focused views)

A clean left/top nav with 4 calm views (not one crowded page):
1. **Overview** — the honest headline as elegant stat tiles: retrieval recall@1 0.85 · nDCG 0.94 ·
   RPO 0 · RTO 3.1–4.9s · read-your-writes 0-lag · **MTTR: pending real agent**. A calm "what's proven
   vs pending" board. This replaces the cluttered dump.
2. **Incident** — the live investigation story (source A).
3. **Resilience** — the failover proof (source B).
4. **Memory & Retrieval** — the eval insights + temporal drift (sources C, D).

## Aesthetic direction (guidance — the design agent refines via frontend-design)

- Calm, technical, "mission control but legible." A restrained dark theme **and** a light theme, both
  first-class. One confident accent; a semantic accent for memory (gold) vs action (cyan) vs the region
  kill (coral) — reused consistently, not decoratively.
- Real charts: crisp axes, direct labels, no chartjunk. Motion is purposeful (a value animating in), not
  ambient noise. Everything readable with the sound off and at a glance.

## Tech

- Next.js (existing app). A charting approach that looks **bespoke, not templated** — prefer lightweight
  SVG primitives or `visx`/`d3` over an out-of-the-box Recharts look; install via pnpm (the app bundles
  normally — no artifact CSP limits). Keep `pnpm test/typecheck/lint/build` green; keep the security
  headers.
- Light+dark via CSS variables/`prefers-color-scheme` + a toggle. Responsive, but desktop-first is fine
  for now (a dedicated mobile view is a separate future task).

## Definition of done

Uncluttered, genuinely beautiful, insight-first; every number real or honestly "pending"; all four pnpm
checks green; the four views above implemented with real charts driven by the data layer.
