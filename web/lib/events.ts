export type Region = "us-east" | "us-west" | "eu-west";

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

export type IncidentEvent = EventBase & {
  type: "incident";
  payload: {
    service: string;
    severity: "SEV-1" | "SEV-2" | "SEV-3";
    status: "open" | "resolved";
    summary: string;
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

export type EvaluationArm = {
  medianMttrSeconds: number;
  p90MttrSeconds: number;
  wrongActions: number;
  escalations: number;
  failedOrders: number;
  tokenProxy: number;
};

export type EvaluationEvent = EventBase & {
  type: "evaluation";
  payload: {
    seed: number;
    familyCount: number;
    recallAt10: number;
    cold: EvaluationArm;
    memory: EvaluationArm;
    learningCurve: {
      occurrence: number;
      coldMttrSeconds: number;
      memoryMttrSeconds: number;
    }[];
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

/**
 * A deliberately small transport guard. Domain-level fields remain strongly typed
 * at producers/consumers while malformed SSE frames are rejected at the boundary.
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
    !("payload" in candidate)
  ) {
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
