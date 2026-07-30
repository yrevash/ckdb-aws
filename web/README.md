# Postmortem incident console

Phase 1 of the original Postmortem plan: a three-rail, event-driven incident console that makes
agent memory visible.

## What is implemented

- Persistent system-state bar with case, three CockroachDB regions, RPO/RTO, and stream state.
- Incident Feed, Investigation, and Memory Timeline rails.
- Phase-1 Recall Thread, similarity dial, approval/action card, and Transaction Envelope.
- Typed transport contract for `incident`, `recall`, `reason`, `act`, `record`, `transaction`, and
  `failover` events.
- SSE client with event-ID deduplication and deterministic CASE-2041 replay when no endpoint is set.
- Responsive single-column/tablet layouts, keyboard focus styles, semantic live regions, and
  reduced-motion handling.

The console intentionally contains no incident-response business logic. It visualizes the responder's
tool events; the mock log follows the same contract the backend will emit.

## Run locally

```bash
pnpm install
pnpm dev
```

Open `http://localhost:3000`.

## Connect an SSE responder

Set a public browser-reachable endpoint:

```bash
NEXT_PUBLIC_POSTMORTEM_EVENTS_URL=http://localhost:8080/v1/incidents/INCIDENT_UUID/events pnpm dev
```

To load the generated Phase 2 A/B scorecard alongside the live incident stream, also set
`NEXT_PUBLIC_POSTMORTEM_EVALUATION_URL` to the browser-reachable
`evaluation/reports/phase2.json` artifact (for example, its S3/CloudFront URL).

The endpoint should return standard SSE frames whose `data` value is one JSON `ConsoleEvent`.
If the connection fails, the console closes it and falls back to the deterministic Phase-1 replay.
The canonical TypeScript contract is in `lib/events.ts`.

## Verify

```bash
pnpm test
pnpm typecheck
pnpm lint
pnpm build
```

## Phase boundary

Phase 1 establishes the working console shell and the vertical-slice proof surfaces. The detailed
Recall Inspector modal, animated cross-rail geometry, Failover Theater, real topology telemetry,
consolidation time-lapse, and final self-hosted font assets remain later-phase work from the original
master plan.
