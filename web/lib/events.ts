/**
 * A cluster region label, spelled exactly the way the real cluster spells it:
 * the `--locality=region=...` flags in docker-compose.multiregion.yml, the
 * region names in db/bootstrap/010_multiregion.sql, and the strings in the
 * generated evaluation/reports/phase3-resilience.json all say us-east-1 /
 * us-east-2 / us-west-2. The old "us-east" | "us-west" | "eu-west" union was a
 * console-only fiction no live component ever produced (audit C2).
 *
 * Deliberately open (`string`): the failover/leaseholder path renders whatever
 * regions the live cluster reported, and the console must never rewrite a
 * measured label into the closed set it happens to know about (charter R6).
 * parseConsoleEvent already validates only `typeof region === "string"`, so
 * this makes the type match what the boundary actually enforces.
 */
export type Region = string;

/** The demo cluster's real topology — for fixtures and ordering only, never a
 *  validation gate on live data. */
export const CLUSTER_REGIONS = ["us-east-1", "us-east-2", "us-west-2"] as const;

export type AgentIdentity = {
  id: string;
  region: Region;
};

type EventBase = {
  id: string;
  sequence: number;
  occurredAt: string;
  caseId: string;
  agent: AgentIdentity;
};

/**
 * Structured telemetry that accompanies an incident alert. Every field is
 * optional: the console renders each one only when the producing system actually
 * measured it, and shows `—` otherwise (Reality Charter R6). Nothing here is
 * ever defaulted to a plausible-looking constant.
 */
export type IncidentTelemetry = {
  /** p99 latency in milliseconds. */
  p99LatencyMs?: number;
  /** error rate as a percentage (e.g. 18.4 for 18.4%). */
  errorRatePct?: number;
  /** the deploy identifier implicated (e.g. "#5120"). */
  deploy?: string;
  /** error-budget burn multiplier (e.g. 16.2 for 16.2×). */
  errorBudgetBurnRate?: number;
};

export type IncidentEvent = EventBase & {
  type: "incident";
  payload: {
    service: string;
    severity: "SEV-1" | "SEV-2" | "SEV-3";
    status: "open" | "resolved";
    summary: string;
    telemetry?: IncidentTelemetry;
  };
};

export type RecallResult = {
  memoryId: string;
  memoryKind?: "episodic" | "semantic" | "procedural";
  sourceCaseId: string;
  summary: string;
  similarity: number;
  accepted?: boolean;
  confidence?: number;
  successRate?: number;
  score?: {
    vector: number;
    scope: number;
    freshness: number;
    outcome: number;
    composite: number;
  };
  provenance?: string[];
  rejectionReasons?: string[];
  runbookId?: string;
  successfulAction?: string;
  scope: {
    service: string;
    tenant: string;
  };
  validFrom: string;
  learnedAt: string;
};

export type RecallEvent = EventBase & {
  type: "recall";
  payload: {
    querySummary: string;
    provider: "c-spann+mcp" | "c-spann+sql" | "cold-start";
    mode?: "memory" | "cold";
    durationMs: number;
    rejectedCount?: number;
    results: RecallResult[];
  };
};

export type ReasonEvent = EventBase & {
  type: "reason";
  payload: {
    message: string;
    citedMemoryIds: string[];
    citedRunbookIds: string[];
  };
};

export type ActEvent = EventBase & {
  type: "act";
  payload: {
    actionId: string;
    status: "proposed" | "approved" | "running" | "committed" | "failed";
    tool: string;
    arguments: Record<string, string | number | boolean>;
    target: string;
    requiresApproval: boolean;
    citedMemoryId: string;
  };
};

export type RecordEvent = EventBase & {
  type: "record";
  payload: {
    memoryId: string;
    memoryKind: "episodic" | "semantic" | "procedural";
    summary: string;
    freshnessMs: number;
    recalledBy?: AgentIdentity;
    staleReadsObserved: number;
  };
};

export type TransactionStatement = {
  operation: "INSERT" | "UPDATE";
  target: string;
  role: "memory" | "action" | "audit";
  summary: string;
};

export type TransactionEvent = EventBase & {
  type: "transaction";
  payload: {
    transactionId: string;
    state: "begun" | "committed" | "rolled_back";
    committedAt?: string;
    statements: TransactionStatement[];
  };
};

export type FailoverEvent = EventBase & {
  type: "failover";
  payload: {
    affectedRegion: Region;
    regionState: "healthy" | "down" | "recovering";
    clusterState: "healthy" | "degraded" | "recovering";
    rpoRows: number;
    rtoMs: number | null;
    leaseholderRegion?: Region;
  };
};

export type EvaluationEvent = EventBase & {
  type: "evaluation";
  payload: {
    seed: number;
    // Real, MEASURED retrieval quality over a corpus with hard negatives.
    // recall@1 is < 1.0 by design (a close-but-wrong prior case can outrank gold).
    retrieval: {
      recallAt1: number;
      recallAt5: number;
      recallAt10: number;
      ndcgAt10: number;
      hardNegativeCount: number;
    };
    // Decision quality (MTTR / wrong-action rate) is NOT measured yet — it needs
    // the real reasoning agent. false => render "pending real-agent run", never a
    // fabricated percentage. (Reality Charter R7.)
    decisionQualityMeasured: boolean;
  };
};

export type ConsoleEvent =
  | IncidentEvent
  | RecallEvent
  | ReasonEvent
  | ActEvent
  | RecordEvent
  | TransactionEvent
  | FailoverEvent
  | EvaluationEvent;

export const CONSOLE_EVENT_TYPES = [
  "incident",
  "recall",
  "reason",
  "act",
  "record",
  "transaction",
  "failover",
  "evaluation",
] as const;

const consoleEventTypes = new Set<string>(CONSOLE_EVENT_TYPES);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/**
 * Per-type payload validation. The console reads these payload fields
 * unconditionally (mapping over arrays, formatting numbers), so a frame whose
 * payload is shaped wrong must be rejected at the boundary rather than thrown
 * from deep inside render — one malformed SSE frame can never blank the console.
 */
function isValidPayload(type: ConsoleEvent["type"], payload: Record<string, unknown>): boolean {
  switch (type) {
    case "incident":
      return (
        typeof payload.service === "string" &&
        typeof payload.severity === "string" &&
        typeof payload.status === "string" &&
        typeof payload.summary === "string" &&
        (payload.telemetry === undefined || isRecord(payload.telemetry))
      );
    case "recall":
      return (
        typeof payload.provider === "string" &&
        typeof payload.durationMs === "number" &&
        Array.isArray(payload.results)
      );
    case "reason":
      return (
        typeof payload.message === "string" &&
        Array.isArray(payload.citedMemoryIds) &&
        Array.isArray(payload.citedRunbookIds)
      );
    case "act":
      return (
        typeof payload.tool === "string" &&
        typeof payload.status === "string" &&
        typeof payload.citedMemoryId === "string" &&
        isRecord(payload.arguments)
      );
    case "record":
      return (
        typeof payload.memoryId === "string" &&
        typeof payload.memoryKind === "string" &&
        typeof payload.summary === "string" &&
        typeof payload.freshnessMs === "number" &&
        typeof payload.staleReadsObserved === "number"
      );
    case "transaction":
      return (
        typeof payload.transactionId === "string" &&
        typeof payload.state === "string" &&
        Array.isArray(payload.statements)
      );
    case "failover":
      return (
        typeof payload.affectedRegion === "string" &&
        typeof payload.regionState === "string" &&
        typeof payload.clusterState === "string" &&
        typeof payload.rpoRows === "number"
      );
    case "evaluation": {
      const retrieval = payload.retrieval;
      return (
        isRecord(retrieval) &&
        typeof retrieval.recallAt1 === "number" &&
        typeof retrieval.recallAt10 === "number" &&
        typeof retrieval.ndcgAt10 === "number" &&
        typeof payload.decisionQualityMeasured === "boolean"
      );
    }
    default:
      return false;
  }
}

/**
 * A transport guard that rejects malformed SSE frames at the boundary. Base
 * envelope fields and the per-type payload shape are both validated, so
 * downstream render code stays fully typed and never throws on a bad frame.
 */
export function parseConsoleEvent(value: unknown): ConsoleEvent | null {
  if (!value || typeof value !== "object") return null;

  const candidate = value as Partial<ConsoleEvent>;
  if (
    typeof candidate.id !== "string" ||
    typeof candidate.sequence !== "number" ||
    typeof candidate.occurredAt !== "string" ||
    typeof candidate.caseId !== "string" ||
    typeof candidate.type !== "string" ||
    !consoleEventTypes.has(candidate.type) ||
    !candidate.agent ||
    typeof candidate.agent.id !== "string" ||
    typeof candidate.agent.region !== "string" ||
    !isRecord((candidate as { payload?: unknown }).payload)
  ) {
    return null;
  }

  const payload = (candidate as { payload: Record<string, unknown> }).payload;
  if (!isValidPayload(candidate.type as ConsoleEvent["type"], payload)) {
    return null;
  }

  return candidate as ConsoleEvent;
}

export function parseSsePayload(payload: string): ConsoleEvent | null {
  try {
    return parseConsoleEvent(JSON.parse(payload) as unknown);
  } catch {
    return null;
  }
}
