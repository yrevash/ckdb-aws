# Postmortem console — UI + chart kit (Wave 1 foundation)

This is the shared design foundation. **Wave 2 builds the Incident, Resilience,
and Memory & Retrieval views on top of these tokens and components — do not
introduce new colors, fonts, or one-off chart code.** Overview
(`components/views/overview.tsx`) is the reference implementation; copy its
patterns.

## Non-negotiables (inherited from the brief + Reality Charter)

- **Only real data.** Numbers come from the honest data layer (`lib/*.ts` mappers
  + `hooks/*`). Absent → `—` (use `ABSENT`/`StatState="absent"`). Model-dependent
  numbers (MTTR, decision quality) are **pending**, never fabricated — render a
  `pending` StatTile / `SourceTag kind="pending"`.
- **Tag every number** with a `SourceTag` (`measured` | `replay` | `pending`).
- **Insight first.** Lead with the headline; charts support it. Generous
  whitespace, one job per view.

## Design tokens (`app/globals.css`)

Everything is a CSS variable, themed for light + dark in one place. Reference
tokens by role; never hard-code a hex.

| Group | Tokens |
|---|---|
| Surfaces | `--plane` (page), `--surface-1` (card), `--surface-2` (raised), `--surface-inset` |
| Ink | `--ink-1` (primary), `--ink-2` (secondary), `--ink-3` (muted) |
| Lines | `--line`, `--line-strong`, `--grid` (chart grid), `--axis` |
| Brand accent | `--accent` (iris) + `--accent-weak` / `--accent-line` — the one confident accent |
| Semantic accents | `--memory` (gold), `--action` (cyan), `--kill` (coral) — each `*-weak` too |
| Status | `--status-good` / `-warn` / `-critical` (+ `*-weak`) — reserved for state, never a category |
| Series | `--series-1`…`--series-8` — dataviz **validated** categorical order; assign in order, never cycle |
| Type | `--font-sans` (UI), `--font-mono` (all figures/data); scale `--fs-micro`…`--fs-hero` |
| Space | `--sp-1`…`--sp-8` (4px base) · Radii `--r-sm`…`--r-pill` · Elevation `--shadow-1/2` |
| Motion | `--ease`, `--dur-1/2/3` |

**Theme:** `<html data-theme="light|dark">` (committed pre-paint in `layout.tsx`).
Toggle via `useTheme()` / `<ThemeToggle/>`. Dark values live under both the
`prefers-color-scheme` media query and the `[data-theme="dark"]` scope.

**Signature motifs:** the *tick-rail* (the marked eyebrow on `SectionHeader` /
hero) and *measured = solid / pending = dashed* — the Reality-Charter state is
the visual grammar. Keep new UI faithful to both.

**Numbers are mono.** All figures use `--font-mono` + `tabular-nums` (`.mono`
helper, or the StatTile/chart classes which already do it).

## Component kit (`components/ui`)

| Component | Key props |
|---|---|
| `StatTile` | `label`, `value`, `unit?`, `state: "measured"\|"pending"\|"absent"`, `hint?`, `accent?: brand\|memory\|action\|kill\|good\|warn\|critical`, `source?`, `sourceDetail?`, `animateFrom?` + `format?` (count-up) |
| `Card` | `title?`, `aside?`, `pad?`, `children` |
| `SectionHeader` | `eyebrow?`, `title`, `description?`, `meta?` |
| `Badge` | `tone: neutral\|good\|warn\|critical\|memory\|action\|kill\|accent` |
| `StatusDot` | `tone`, `pulse?`, `label?` |
| `SourceTag` | `kind: measured\|replay\|pending`, `detail?` (R9 provenance) |
| `ThemeToggle` | — |

Import from `@/components/ui`. Icons: `@/components/ui/icons`.

## Chart kit (`components/charts`) — bespoke SVG, no chart lib

All charts wrap `ChartFrame` (accessible `role="img"` + `<title>/<desc>` + an
optional screen-reader `<table>` fallback). Geometry is pure + unit-tested in
`lib/chart.ts`.

| Chart | Use | Key props |
|---|---|---|
| `RankedBars` | magnitude across named rows (recall@k, leaseholders) | `data: {label,value,display?,emphasis?}[]`, `max`, `title`, `desc?`, `caption?` |
| `Gauge` | one 0..1 quality score (nDCG, accuracy) | `value`, `title`, `centerLabel`, `centerCaption?` |
| `LivenessTimeline` | node liveness over a region kill (step/area + outage band) | `phases: {id,label,liveNodes,down,sub?}[]`, `expected`, `title` |
| `StatusGrid` | probe pass/fail (icon + label + color, never color-alone) | `items: {label,status:"pass"\|"fail",meta?}[]` |

### dataviz rules to keep

- Single-series charts get **no legend** (the title names it) + **direct labels**.
- ≥ 2 series → legend always present; assign `--series-*` in fixed order, never
  cycle; color follows the entity, never its rank.
- Never a dual-axis chart. Sequential = one hue; diverging = two hues + gray mid.
- Status = icon + label + color together. Series color never carries text.
- The categorical order is validated (CVD ΔE 9.1 light / 8.4 dark). If you add a
  multi-series chart, re-run `dataviz/scripts/validate_palette.js` before shipping.

## Data hooks

- `useEvaluationReport()` → `{ event, source }` — Phase-2 retrieval + the
  `decisionQualityMeasured` flag (reuses `evaluationEventFromReport`).
- `usePhase3Reports()` → `{ resilience, temporal }` (existing).
- `useConsoleEvents()` → live/replay incident SSE stream (existing, for Incident).

Each returns `source: "live" | "replay"`; map to a `SourceTag` (`live`→measured).
