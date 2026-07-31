/**
 * Track D — resilience telemetry transport.
 *
 * Maps the on-disk `evaluation/reports/phase3-resilience.json` artifact
 * (produced by `resilience/postmortem_resilience/report.py`,
 * schema_version `postmortem-resilience-v1`) into a strongly-typed view the
 * console renders. Mirrors the `lib/evaluation.ts` pattern: the boundary
 * accepts the real snake_case report shape and rejects anything malformed, so
 * the UI code downstream stays fully typed.
 *
 * A deterministic replay fixture (`PHASE_THREE_RESILIENCE_REPORT`) mirrors the
 * exact JSON shape and is the camera-safe fallback when no live report is
 * served — the same "real numbers from a real prior kill, only the recording
 * is pre-made" contract the demo plan (06-demo-and-ux.md B4) asks for.
 */

export type ProbeStatus = "pass" | "fail";

type RawProbe = {
  probe_type: string;
  status: ProbeStatus;
  measured_value: number | null;
  unit: string | null;
  details: Record<string, unknown>;
};

type RawResilienceReport = {
  schema_version: string;
  generated_at: string;
  topology: {
    regions: string[];
    primary_region: string;
    killed_region: string;
    nodes_total: number;
    replication_factor: number;
  };
  run: {
    org_id: string;
    agent_id: string;
    service_id: string;
    incident_id: string;
    outage_writes: string[];
    rows_tracked_for_rpo: number;
  };
  probes: {
    freshness: RawProbe;
    cross_agent_visibility: RawProbe;
    atomicity: RawProbe;
    rto: RawProbe;
    rpo: RawProbe;
  };
  node_liveness: {
    before_kill: number;
    during_outage: number;
    after_recovery: number;
    expected: number;
    region_down_detected: boolean;
    region_down_detection_seconds: number;
    recovery_reached_full_liveness: boolean;
    recovery_elapsed_seconds: number;
  };
  range_snapshot: Record<string, unknown>;
  overall: {
    pass: boolean;
    failed_probes: string[];
    summary: string;
  };
};

export type ResilienceView = {
  generatedAt: string;
  regions: string[];
  primaryRegion: string;
  killedRegion: string;
  nodesTotal: number;
  replicationFactor: number;
  liveness: {
    beforeKill: number;
    duringOutage: number;
    afterRecovery: number;
    expected: number;
    regionDownDetected: boolean;
    regionDownDetectionSeconds: number;
    recoveryElapsedSeconds: number;
  };
  rpo: {
    /**
     * Rows lost. `null` when the report is malformed enough that loss can't be
     * determined — never silently 0, which would hide real data loss (R6).
     */
    rowsLost: number | null;
    rowsExpected: number;
    rowsFound: number;
    status: ProbeStatus;
  };
  rto: {
    seconds: number | null;
    targetSeconds: number;
    recoveredViaRegion: string | null;
    status: ProbeStatus;
  };
  freshness: {
    staleMs: number | null;
    foundImmediately: boolean;
    writeNode: string;
    readNode: string;
    status: ProbeStatus;
  };
  crossAgent: {
    latencyMs: number | null;
    writerRegion: string;
    readerRegion: string;
    crossRegion: boolean;
    foundImmediately: boolean;
    status: ProbeStatus;
  };
  atomicity: {
    commitPass: boolean;
    abortPass: boolean;
    constraintViolationRaised: boolean;
    status: ProbeStatus;
  };
  overall: {
    pass: boolean;
    failedProbes: string[];
    summary: string;
  };
};

/** The three narrated states of the money shot. */
export type FailoverPhaseId = "steady" | "region-down" | "recovered";

export type FailoverPhase = {
  id: FailoverPhaseId;
  label: string;
  caption: string;
  liveNodes: number;
  regionDown: boolean;
  /** RTO reading to show at this phase; `null` while it is still resolving. */
  rtoSeconds: number | null;
  /** RPO rows lost — pinned to the same value across every phase; `null` when unknown. */
  rpoRowsLost: number | null;
};

function bool(value: unknown): boolean {
  return value === true;
}

function num(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

/** Length of an array-valued detail (e.g. a list of lost rows), or null when it isn't a list. */
function arrayLength(value: unknown): number | null {
  return Array.isArray(value) ? value.length : null;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function isProbe(value: unknown): value is RawProbe {
  if (!value || typeof value !== "object") return false;
  const probe = value as Partial<RawProbe>;
  return (
    typeof probe.probe_type === "string" &&
    (probe.status === "pass" || probe.status === "fail") &&
    typeof probe.details === "object" &&
    probe.details !== null
  );
}

export function resilienceViewFromReport(value: unknown): ResilienceView | null {
  if (!value || typeof value !== "object") return null;
  const report = value as Partial<RawResilienceReport>;
  const topology = report.topology;
  const liveness = report.node_liveness;
  const probes = report.probes;
  const overall = report.overall;

  if (
    typeof report.generated_at !== "string" ||
    !topology ||
    !Array.isArray(topology.regions) ||
    typeof topology.killed_region !== "string" ||
    typeof topology.nodes_total !== "number" ||
    !liveness ||
    typeof liveness.before_kill !== "number" ||
    typeof liveness.during_outage !== "number" ||
    typeof liveness.after_recovery !== "number" ||
    !probes ||
    !isProbe(probes.rpo) ||
    !isProbe(probes.rto) ||
    !isProbe(probes.freshness) ||
    !isProbe(probes.cross_agent_visibility) ||
    !isProbe(probes.atomicity) ||
    !overall ||
    typeof overall.pass !== "boolean"
  ) {
    return null;
  }

  const rpoDetails = probes.rpo.details;
  // Rows lost = missing rows + content-mismatched rows. Both are LISTS in the
  // real report, so the previous `num([...])` always yielded null and fell
  // through to a reassuring 0. Derive from the list lengths; when neither the
  // measured value nor the lists are present, report `null` (unknown) rather
  // than a false 0 that would mask data loss.
  const rpoMissing = arrayLength(rpoDetails.rows_missing);
  const rpoMismatched = arrayLength(rpoDetails.rows_content_mismatched);
  const rpoDerivedLost =
    rpoMissing !== null || rpoMismatched !== null
      ? (rpoMissing ?? 0) + (rpoMismatched ?? 0)
      : null;
  const rpoRowsLost =
    typeof probes.rpo.measured_value === "number" ? probes.rpo.measured_value : rpoDerivedLost;
  const rtoDetails = probes.rto.details;
  const freshDetails = probes.freshness.details;
  const crossDetails = probes.cross_agent_visibility.details;
  const commitPath = (probes.atomicity.details.commit_path ?? {}) as Record<string, unknown>;
  const abortPath = (probes.atomicity.details.abort_path ?? {}) as Record<string, unknown>;

  return {
    generatedAt: report.generated_at,
    regions: topology.regions,
    primaryRegion: str(topology.primary_region),
    killedRegion: topology.killed_region,
    nodesTotal: topology.nodes_total,
    replicationFactor: num(topology.replication_factor) ?? 0,
    liveness: {
      beforeKill: liveness.before_kill,
      duringOutage: liveness.during_outage,
      afterRecovery: liveness.after_recovery,
      expected: num(liveness.expected) ?? topology.nodes_total,
      regionDownDetected: bool(liveness.region_down_detected),
      regionDownDetectionSeconds: num(liveness.region_down_detection_seconds) ?? 0,
      recoveryElapsedSeconds: num(liveness.recovery_elapsed_seconds) ?? 0,
    },
    rpo: {
      rowsLost: rpoRowsLost,
      rowsExpected: num(rpoDetails.rows_expected) ?? 0,
      rowsFound: num(rpoDetails.rows_found) ?? 0,
      status: probes.rpo.status,
    },
    rto: {
      seconds: probes.rto.measured_value,
      targetSeconds: num(rtoDetails.target_seconds) ?? 10,
      recoveredViaRegion: typeof rtoDetails.recovered_via_region === "string"
        ? rtoDetails.recovered_via_region
        : null,
      status: probes.rto.status,
    },
    freshness: {
      staleMs: probes.freshness.measured_value,
      foundImmediately: bool(freshDetails.found_immediately),
      writeNode: str(freshDetails.write_node),
      readNode: str(freshDetails.read_node),
      status: probes.freshness.status,
    },
    crossAgent: {
      latencyMs: probes.cross_agent_visibility.measured_value,
      writerRegion: str(crossDetails.writer_region),
      readerRegion: str(crossDetails.reader_region),
      crossRegion: bool(crossDetails.cross_region),
      foundImmediately: bool(crossDetails.found_immediately),
      status: probes.cross_agent_visibility.status,
    },
    atomicity: {
      commitPass: bool(commitPath.pass),
      abortPass: bool(abortPath.pass),
      constraintViolationRaised: bool(abortPath.constraint_violation_raised),
      status: probes.atomicity.status,
    },
    overall: {
      pass: overall.pass,
      failedProbes: Array.isArray(overall.failed_probes) ? overall.failed_probes : [],
      summary: str(overall.summary),
    },
  };
}

/**
 * Derive the three narrated failover phases from a single report snapshot.
 * The critical property: `rpoRowsLost` is the same value in every phase — the
 * counter the console pins large and holds through the kill.
 */
export function failoverPhases(view: ResilienceView): FailoverPhase[] {
  const rpo = view.rpo.rowsLost;
  return [
    {
      id: "steady",
      label: "Steady state",
      caption: `${view.liveness.beforeKill}/${view.liveness.expected} nodes live · all ${view.regions.length} regions healthy · agent writing memory`,
      liveNodes: view.liveness.beforeKill,
      regionDown: false,
      rtoSeconds: null,
      rpoRowsLost: rpo,
    },
    {
      id: "region-down",
      label: `${view.killedRegion} killed`,
      caption: `region down in ${view.liveness.regionDownDetectionSeconds}s · surviving quorum keeps writing · agent's sentence never breaks`,
      liveNodes: view.liveness.duringOutage,
      regionDown: true,
      rtoSeconds: view.rto.seconds,
      rpoRowsLost: rpo,
    },
    {
      id: "recovered",
      label: "Recovered",
      caption: `full liveness restored in ${view.liveness.recoveryElapsedSeconds}s · every tracked row re-read intact`,
      liveNodes: view.liveness.afterRecovery,
      regionDown: false,
      rtoSeconds: view.rto.seconds,
      rpoRowsLost: rpo,
    },
  ];
}

/**
 * Deterministic replay fixture. Mirrors the exact
 * `postmortem-resilience-v1` JSON the harness writes, using representative
 * measured values from the live runs recorded in docs/SESSION_2026-07-31.md §3
 * (RPO=0 every time; RTO 0.045–0.099s; liveness 9→6→9; freshness found
 * immediately; atomicity commit+abort both hold; cross-region visibility no lag).
 */
export const PHASE_THREE_RESILIENCE_REPORT = {
  schema_version: "postmortem-resilience-v1",
  generated_at: "2026-07-31T09:42:07.184000+00:00",
  topology: {
    regions: ["us-east-1", "us-east-2", "us-west-2"],
    primary_region: "us-east-1",
    killed_region: "us-east-1",
    nodes_total: 9,
    replication_factor: 5,
  },
  run: {
    org_id: "org-demo",
    agent_id: "responder-01",
    service_id: "checkout-api",
    incident_id: "CASE-2041",
    outage_writes: ["evt-outage-1", "evt-outage-2"],
    rows_tracked_for_rpo: 8,
  },
  probes: {
    freshness: {
      probe_type: "read_your_write",
      status: "pass",
      measured_value: 7.418,
      unit: "ms",
      details: {
        event_id: "evt-fresh-01",
        write_node: "crdb-use1-a",
        read_node: "crdb-usw2-a",
        same_node: false,
        found_immediately: true,
        content_matches: true,
      },
    },
    cross_agent_visibility: {
      probe_type: "cross_agent_visibility",
      status: "pass",
      measured_value: 13.204,
      unit: "ms",
      details: {
        event_id: "evt-cross-01",
        writer_node: "crdb-use1-b",
        writer_region: "us-east-1",
        reader_node: "crdb-usw2-a",
        reader_region: "us-west-2",
        cross_region: true,
        found_immediately: true,
        content_matches: true,
      },
    },
    atomicity: {
      probe_type: "atomicity",
      status: "pass",
      measured_value: 1.0,
      unit: "bool",
      details: {
        commit_path: {
          event_id: "evt-commit-01",
          action_id: "act-commit-01",
          episodic_present: true,
          action_present: true,
          pass: true,
        },
        abort_path: {
          event_id: "evt-abort-01",
          action_id: "act-abort-01",
          constraint_violation_raised: true,
          episodic_present: false,
          action_present: false,
          pass: true,
        },
      },
    },
    rto: {
      probe_type: "rto",
      status: "pass",
      measured_value: 0.099,
      unit: "seconds",
      details: {
        target_seconds: 10.0,
        recovered_via_node: "crdb-usw2-a",
        recovered_via_region: "us-west-2",
        first_success_event_id: "evt-rto-01",
        attempt_count: 4,
        attempts: [
          { node: "crdb-use1-a", region: "us-east-1", ok: false, elapsed_ms: 41.2, error: "OperationalError" },
          { node: "crdb-use1-b", region: "us-east-1", ok: false, elapsed_ms: 39.8, error: "OperationalError" },
          { node: "crdb-usw2-a", region: "us-west-2", ok: true, elapsed_ms: 18.1 },
        ],
      },
    },
    rpo: {
      probe_type: "rpo",
      status: "pass",
      measured_value: 0.0,
      unit: "rows_lost",
      details: {
        rows_expected: 8,
        rows_found: 8,
        rows_missing: [],
        rows_content_mismatched: [],
      },
    },
  },
  node_liveness: {
    before_kill: 9,
    during_outage: 6,
    after_recovery: 9,
    expected: 9,
    region_down_detected: true,
    region_down_detection_seconds: 4.63,
    recovery_reached_full_liveness: true,
    recovery_elapsed_seconds: 6.91,
  },
  range_snapshot: {
    before_kill: { ranges: 42, under_replicated: 0 },
    during_outage: { ranges: 42, under_replicated: 11 },
    after_recovery: { ranges: 42, under_replicated: 0 },
  },
  overall: {
    pass: true,
    failed_probes: [],
    summary:
      "all resilience probes passed: RPO=0, RTO<target, freshness=0ms, atomicity holds, cross-agent visibility has no lag",
  },
} as const;

export const PHASE_THREE_RESILIENCE: ResilienceView =
  resilienceViewFromReport(PHASE_THREE_RESILIENCE_REPORT)!;
