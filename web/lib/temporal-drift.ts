/**
 * Track D — bitemporal temporal-drift transport.
 *
 * Joins two real artifacts into the "facts evolve, not overwrite" view:
 *  - the temporal-validity block of `evaluation/reports/phase3.json`
 *    (`runner.py::_run_temporal_drift`) — which fact the memory arm actually
 *    applied at each incident's decision time, and which one was currently
 *    valid; and
 *  - the drift memory corpus (`evaluation/fixtures/drift_memory_corpus.json`)
 *    — each gold fact's business-time window (`valid_from`/`valid_to`) and text.
 *
 * Together they render a fact's valid-time transition (old fix → superseded →
 * currently-valid fix) and prove the agent chose the currently-valid fix, not
 * the stale one. Both superseded and current facts persist — nothing is
 * overwritten. A deterministic replay fixture mirrors the exact JSON shapes.
 */

type RawDriftAction = {
  action_type: string;
  target_service: string;
  params: Record<string, unknown>;
};

type RawDriftMemory = {
  memory_id: string;
  family_id: string;
  gold: boolean;
  text: string;
  valid_from: string;
  valid_to: string | null;
  actions: RawDriftAction[];
};

type RawDriftCorpus = {
  memories: RawDriftMemory[];
};

type RawIncidentRecord = {
  scenario_id: string;
  family_id: string;
  variant_id: string;
  observed_at: string | null;
  authorized_memory_id: string | null;
  expected_memory_id: string | null;
  applied_currently_valid_fix: boolean;
  applied_stale_fact: boolean;
  resolved: boolean;
  mttr_seconds: number | null;
};

type RawTemporalReport = {
  families: string[];
  incidents_evaluated: number;
  temporal_validity_accuracy: number;
  stale_fact_applications: number;
  target_temporal_validity_accuracy: number;
  target_stale_fact_applications: number;
  meets_target: boolean;
  incidents: RawIncidentRecord[];
};

export type DriftFact = {
  memoryId: string;
  text: string;
  actionSummary: string;
  validFrom: string;
  validTo: string | null;
  /** "current" is the fact valid now (valid_to === null); others are superseded. */
  status: "superseded" | "current";
};

export type DriftIncident = {
  scenarioId: string;
  variantId: string;
  observedAt: string | null;
  authorizedMemoryId: string | null;
  expectedMemoryId: string | null;
  appliedCurrentlyValidFix: boolean;
  appliedStaleFact: boolean;
  resolved: boolean;
  mttrSeconds: number | null;
};

export type DriftFamilyView = {
  familyId: string;
  title: string;
  facts: DriftFact[];
  /** Business-time instant the environment change superseded the old fact. */
  supersededAt: string | null;
  /** The post-migration incident where the agent had to pick the new fix. */
  incident: DriftIncident | null;
};

export type TemporalDriftView = {
  families: DriftFamilyView[];
  temporalValidityAccuracy: number;
  staleFactApplications: number;
  incidentsEvaluated: number;
  meetsTarget: boolean;
  targetAccuracy: number;
};

const FAMILY_TITLES: Record<string, string> = {
  F11_POOL_DRIVER_MIGRATION: "Connection pool driver migration",
  F12_CACHE_TOPOLOGY_MIGRATION: "Cache topology migration",
};

function titleize(familyId: string): string {
  return (
    FAMILY_TITLES[familyId] ??
    familyId
      .replace(/^F\d+_/, "")
      .toLowerCase()
      .split("_")
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ")
  );
}

/** A concise, on-call-legible summary of what a fact's fix actually does. */
export function summarizeActions(actions: RawDriftAction[]): string {
  const parts = actions.map((action) => {
    const params = action.params ?? {};
    switch (action.action_type) {
      case "set_config": {
        const key = String(params.key ?? "");
        const value = params.value;
        return typeof value === "boolean"
          ? value
            ? `enable ${key}`
            : `disable ${key}`
          : `set ${key} → ${String(value)}`;
      }
      case "failover_dependency":
        return `fail over ${String(params.dependency_key ?? "")} → ${String(params.to_service ?? "")}`;
      case "restart_service":
        return "restart service";
      default:
        return action.action_type.replace(/_/g, " ");
    }
  });
  return parts.join(" · ");
}

function toEpoch(value: string | null): number {
  if (!value) return Number.POSITIVE_INFINITY;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

function isTemporalReport(value: unknown): value is RawTemporalReport {
  if (!value || typeof value !== "object") return false;
  const report = value as Partial<RawTemporalReport>;
  return (
    typeof report.temporal_validity_accuracy === "number" &&
    typeof report.stale_fact_applications === "number" &&
    Array.isArray(report.incidents)
  );
}

function isCorpus(value: unknown): value is RawDriftCorpus {
  return (
    !!value &&
    typeof value === "object" &&
    Array.isArray((value as Partial<RawDriftCorpus>).memories)
  );
}

export function temporalDriftViewFromReport(
  reportValue: unknown,
  corpusValue: unknown,
): TemporalDriftView | null {
  if (!isTemporalReport(reportValue) || !isCorpus(corpusValue)) return null;

  const goldByFamily = new Map<string, RawDriftMemory[]>();
  for (const memory of corpusValue.memories) {
    if (!memory.gold) continue;
    const list = goldByFamily.get(memory.family_id) ?? [];
    list.push(memory);
    goldByFamily.set(memory.family_id, list);
  }

  const families: DriftFamilyView[] = [];
  for (const [familyId, memories] of goldByFamily) {
    const ordered = [...memories].sort(
      (a, b) => toEpoch(a.valid_from) - toEpoch(b.valid_from),
    );
    const facts: DriftFact[] = ordered.map((memory) => ({
      memoryId: memory.memory_id,
      text: memory.text,
      actionSummary: summarizeActions(memory.actions),
      validFrom: memory.valid_from,
      validTo: memory.valid_to,
      status: memory.valid_to === null ? "current" : "superseded",
    }));

    const currentFact = facts.find((fact) => fact.status === "current");
    const supersededAt = currentFact?.validFrom ?? null;

    // The load-bearing beat: the post-migration incident whose currently-valid
    // fix is the new fact — where a stale answer would have been wrong.
    const familyIncidents = reportValue.incidents.filter(
      (record) => record.family_id === familyId,
    );
    const migratedRecord =
      familyIncidents.find(
        (record) => record.expected_memory_id === currentFact?.memoryId,
      ) ?? familyIncidents[familyIncidents.length - 1] ?? null;

    families.push({
      familyId,
      title: titleize(familyId),
      facts,
      supersededAt,
      incident: migratedRecord
        ? {
            scenarioId: migratedRecord.scenario_id,
            variantId: migratedRecord.variant_id,
            observedAt: migratedRecord.observed_at,
            authorizedMemoryId: migratedRecord.authorized_memory_id,
            expectedMemoryId: migratedRecord.expected_memory_id,
            appliedCurrentlyValidFix: migratedRecord.applied_currently_valid_fix,
            appliedStaleFact: migratedRecord.applied_stale_fact,
            resolved: migratedRecord.resolved,
            mttrSeconds: migratedRecord.mttr_seconds,
          }
        : null,
    });
  }

  families.sort((a, b) => a.familyId.localeCompare(b.familyId));

  return {
    families,
    temporalValidityAccuracy: reportValue.temporal_validity_accuracy,
    staleFactApplications: reportValue.stale_fact_applications,
    incidentsEvaluated: reportValue.incidents_evaluated,
    meetsTarget: reportValue.meets_target,
    targetAccuracy: reportValue.target_temporal_validity_accuracy,
  };
}

/**
 * Deterministic replay fixtures. Mirror the exact shapes of
 * `evaluation/fixtures/drift_memory_corpus.json` and the `temporal_drift`
 * block of `evaluation/reports/phase3.json`. Values match the live run
 * recorded in docs/SESSION_2026-07-31.md §3 Track B
 * (temporal_validity_accuracy = 1.0, stale_fact_applications = 0).
 */
export const DRIFT_MEMORY_CORPUS: RawDriftCorpus = {
  memories: [
    {
      memory_id: "mem-f11-legacy-pool",
      family_id: "F11_POOL_DRIVER_MIGRATION",
      gold: true,
      text: "Database too many connections and high p99 latency indicate connection pool exhaustion under the legacy thread pool driver. Raise db_pool_size to 80, then restart the affected worker.",
      valid_from: "2026-06-01T00:00:00Z",
      valid_to: "2026-07-01T03:00:00Z",
      actions: [
        { action_type: "set_config", target_service: "$incident.service", params: { key: "db_pool_size", value: 80 } },
        { action_type: "restart_service", target_service: "$incident.service", params: {} },
      ],
    },
    {
      memory_id: "mem-f11-multiplexed-pool",
      family_id: "F11_POOL_DRIVER_MIGRATION",
      gold: true,
      text: "Since the platform migrated the service to the multiplexed connection pool driver, raising db_pool_size no longer has any effect -- enable pool_multiplexing_enabled instead.",
      valid_from: "2026-07-01T03:00:00Z",
      valid_to: null,
      actions: [
        { action_type: "set_config", target_service: "$incident.service", params: { key: "pool_multiplexing_enabled", value: true } },
      ],
    },
    {
      memory_id: "mem-f12-onprem-cache",
      family_id: "F12_CACHE_TOPOLOGY_MIGRATION",
      gold: true,
      text: "Cache timeout cascade reduced storefront availability on the on-prem cache topology. Fail over storefront-cache to the on-prem redis replica.",
      valid_from: "2026-06-01T00:00:00Z",
      valid_to: "2026-07-01T15:00:00Z",
      actions: [
        { action_type: "failover_dependency", target_service: "$incident.service", params: { dependency_key: "storefront-cache", to_service: "cache-onprem-replica" } },
      ],
    },
    {
      memory_id: "mem-f12-managed-cache",
      family_id: "F12_CACHE_TOPOLOGY_MIGRATION",
      gold: true,
      text: "Since the platform migrated to the managed cache topology and decommissioned the on-prem replica, fail over storefront-cache to the managed cache primary instead.",
      valid_from: "2026-07-01T15:00:00Z",
      valid_to: null,
      actions: [
        { action_type: "failover_dependency", target_service: "$incident.service", params: { dependency_key: "storefront-cache", to_service: "cache-managed-primary" } },
      ],
    },
  ],
};

export const PHASE_THREE_TEMPORAL_REPORT: RawTemporalReport = {
  families: ["F11_POOL_DRIVER_MIGRATION", "F12_CACHE_TOPOLOGY_MIGRATION"],
  incidents_evaluated: 4,
  temporal_validity_accuracy: 1.0,
  stale_fact_applications: 0,
  target_temporal_validity_accuracy: 0.9,
  target_stale_fact_applications: 0,
  meets_target: true,
  incidents: [
    {
      scenario_id: "f11-pool-legacy",
      family_id: "F11_POOL_DRIVER_MIGRATION",
      variant_id: "legacy-driver",
      observed_at: "2026-07-01T00:00:00Z",
      authorized_memory_id: "mem-f11-legacy-pool",
      expected_memory_id: "mem-f11-legacy-pool",
      applied_currently_valid_fix: true,
      applied_stale_fact: false,
      resolved: true,
      mttr_seconds: 120,
    },
    {
      scenario_id: "f11-pool-migrated",
      family_id: "F11_POOL_DRIVER_MIGRATION",
      variant_id: "multiplexed-driver",
      observed_at: "2026-07-01T06:00:00Z",
      authorized_memory_id: "mem-f11-multiplexed-pool",
      expected_memory_id: "mem-f11-multiplexed-pool",
      applied_currently_valid_fix: true,
      applied_stale_fact: false,
      resolved: true,
      mttr_seconds: 90,
    },
    {
      scenario_id: "f12-cache-onprem",
      family_id: "F12_CACHE_TOPOLOGY_MIGRATION",
      variant_id: "onprem-topology",
      observed_at: "2026-07-01T12:00:00Z",
      authorized_memory_id: "mem-f12-onprem-cache",
      expected_memory_id: "mem-f12-onprem-cache",
      applied_currently_valid_fix: true,
      applied_stale_fact: false,
      resolved: true,
      mttr_seconds: 120,
    },
    {
      scenario_id: "f12-cache-managed",
      family_id: "F12_CACHE_TOPOLOGY_MIGRATION",
      variant_id: "managed-topology",
      observed_at: "2026-07-01T18:00:00Z",
      authorized_memory_id: "mem-f12-managed-cache",
      expected_memory_id: "mem-f12-managed-cache",
      applied_currently_valid_fix: true,
      applied_stale_fact: false,
      resolved: true,
      mttr_seconds: 90,
    },
  ],
};

export const PHASE_THREE_TEMPORAL: TemporalDriftView =
  temporalDriftViewFromReport(PHASE_THREE_TEMPORAL_REPORT, DRIFT_MEMORY_CORPUS)!;
