// Acceptance fixture teams for the alerts BI MVP.
//
// These are NOT a second fixture system: they are team definitions consumed by
// generate-mock-alerts.mjs alongside the seven realistic teams, and they load into the
// same appchi-v1 / appchi-v2 indices. They are separated into this file only because
// they are authored to a different standard - every row is pinned to an exact timestamp
// and an exact expected outcome, so test/fixtures/expected-results.json can be computed
// by hand from these definitions rather than reverse-engineered from generated output.
//
// The acceptance window is the exact 168 hours ending at the generator's fixed clock:
//   run_at       2026-08-25T18:00:00Z
//   window_start 2026-08-18T18:00:00Z (inclusive)
//   window_end   2026-08-25T18:00:00Z (exclusive)
//
// Rows sit on 2026-08-20 and 2026-08-21 so both single-date and multi-date allocation are
// exercised, well inside the window's partial first and last buckets.

/** Every acceptance row lands on one of these two instants. */
export const T20 = '2026-08-20T12:00:00.000Z';
export const T21 = '2026-08-21T12:00:00.000Z';

const RULE_URL = 'https://grafana.internal/d/acc-core-1';
const GOOD_V1_MESSAGE = 'Checkout error rate above 2% of requests over 5m';
const GOOD_V2_MESSAGE = 'p99 checkout latency above 900ms over 10m';
const GOOD_IMPACT = 'Customers see slow or failing checkouts';
const GOOD_RUNBOOK = 'https://runbooks.internal/acceptance/checkout';

/**
 * acceptance-core — one row per case, so every deterministic rule boundary is countable
 * by eye. Each v1 alert uses a distinct `obj`, which makes each one a distinct identity
 * under the v1 key (application + object + node_name).
 */
export const acceptanceCore = {
  name: 'acceptance-core',
  phase: 'acceptance',
  quality: 'mixed',
  schemas: ['v1', 'v2'],
  v1Operators: ['acc-core'],
  v2Operator: 'acc-core-v2',
  v1PanelQuery: null,
  v2PanelQuery: null,
  v1Defs: [
    // --- clean: no core finding, so these go to the model ---
    { application: 'acc-app', obj: 'c01-clean', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, provider: 'grafana', rowsAt: [T20] },
    // R4 does NOT apply to API alerts: no rule URL is not evidence against them.
    { application: 'acc-app', obj: 'c02-api-no-url', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: null, provider: 'api', rowsAt: [T20] },
    // R7 inclusive boundaries: both valid, neither flagged.
    { application: 'acc-app', obj: 'c03-r7-equal', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, timeCreated: 'equal', rowsAt: [T20] },
    { application: 'acc-app', obj: 'c04-r7-oldest', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, timeCreated: 'oldest', rowsAt: [T20] },

    // --- one core finding each ---
    { application: 'acc-app', obj: 'c05-r1', node_name: 'node-a', message: 'Error Occurred',
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, rowsAt: [T20] },
    { application: 'acc-app', obj: 'c06-r2', node_name: 'node-a', message: 'i am alive',
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, rowsAt: [T20] },
    { application: 'acc-app', obj: 'Unknown', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, rowsAt: [T20] },
    { application: 'acc-app', obj: 'c08-r4', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: null, provider: 'grafana', rowsAt: [T20] },
    { application: 'acc-app', obj: 'c09-r7-future', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, timeCreated: 'future', rowsAt: [T20] },
    { application: 'acc-app', obj: 'c10-r7-stale', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, timeCreated: 'stale', rowsAt: [T20] },

    // --- multi-row identity spanning two dates: three rows, one identity ---
    // The R1 match is on the 08-21 row only, which proves findings are not projected onto
    // the 08-20 rows that did not match, and that one core finding anywhere in the window
    // still withholds the whole identity from the model.
    { application: 'acc-app', obj: 'c11-multiday', node_name: 'node-a', message: 'Alert triggered',
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, rowsAt: [T21] },
    { application: 'acc-app', obj: 'c11-multiday', node_name: 'node-a', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-core', alert_rule_url: RULE_URL, rowsAt: [T20, T20] },
  ],
  v2Defs: [
    // completion-ready
    { application: 'acc-app-v2', obj: 'v01-ready', message: GOOD_V2_MESSAGE, severity: 'high',
      impact: GOOD_IMPACT, runbook_url: GOOD_RUNBOOK, alert_rule_url: RULE_URL, rowsAt: [T20] },
    // R8: missing impact
    { application: 'acc-app-v2', obj: 'v02-r8', message: GOOD_V2_MESSAGE, severity: 'warning',
      impact: null, runbook_url: GOOD_RUNBOOK, alert_rule_url: RULE_URL, rowsAt: [T20] },
    // R9 on critical: blocks phase-2 completion
    { application: 'acc-app-v2', obj: 'v03-r9-critical', message: GOOD_V2_MESSAGE, severity: 'critical',
      impact: GOOD_IMPACT, runbook_url: null, alert_rule_url: RULE_URL, rowsAt: [T20] },
    // R9 on high: visible, but does NOT reduce readiness
    { application: 'acc-app-v2', obj: 'v04-r9-high', message: GOOD_V2_MESSAGE, severity: 'high',
      impact: GOOD_IMPACT, runbook_url: null, alert_rule_url: RULE_URL, rowsAt: [T20] },
    // R10: impact restates the technical cause
    { application: 'acc-app-v2', obj: 'v05-r10', message: GOOD_V2_MESSAGE, severity: 'warning',
      impact: 'high cpu', runbook_url: GOOD_RUNBOOK, alert_rule_url: RULE_URL, rowsAt: [T20] },
    // R2 core finding on v2: core rules apply to both schemas
    { application: 'acc-app-v2', obj: 'v06-r2', message: 'completed successfully', severity: 'warning',
      impact: GOOD_IMPACT, runbook_url: GOOD_RUNBOOK, alert_rule_url: RULE_URL, rowsAt: [T20] },
  ],
};

/**
 * acceptance-batching — exercises the grouping and partition paths that design section
 * 7.5 records the current fixtures cannot reach.
 *
 * 401 v2 identities share ONE alert_rule_url, so the group must split into balanced
 * partitions of 134/134/133. A separate set of API alerts carries no rule URL and must
 * fall back to application grouping without ever merging with the rule-URL group.
 */
export const acceptanceBatching = (() => {
  const BIG_RULE_URL = 'https://grafana.internal/d/acc-batch-big';
  /** @type {any[]} */
  const v2Defs = [];

  // 401 distinct identities under one rule URL.
  for (let i = 0; i < 401; i++) {
    const n = String(i).padStart(4, '0');
    v2Defs.push({
      application: 'acc-batch-app',
      obj: `big-${n}`,
      message: `Queue depth above threshold on shard ${n}`,
      severity: 'warning',
      impact: 'Processing for this shard falls behind',
      runbook_url: 'https://runbooks.internal/acceptance/batch',
      alert_rule_url: BIG_RULE_URL,
      key_field: `acc-batch-big-${n}`,
      rowsAt: [T20],
    });
  }

  // 3 identities with no rule URL: application fallback.
  for (let i = 0; i < 3; i++) {
    v2Defs.push({
      application: 'acc-batch-api-app',
      obj: `api-${i}`,
      message: `API-sent alert ${i}: ingest lag above 5m`,
      severity: 'warning',
      impact: 'Ingest results arrive late for this stream',
      runbook_url: 'https://runbooks.internal/acceptance/ingest',
      alert_rule_url: null,
      provider: 'api',
      key_field: `acc-batch-api-${i}`,
      rowsAt: [T20],
    });
  }

  return {
    name: 'acceptance-batching',
    phase: 'acceptance',
    quality: 'good',
    schemas: ['v2'],
    v2Operator: 'acc-batching',
    v1PanelQuery: null,
    v2PanelQuery: null,
    v2Defs,
  };
})();

/**
 * acceptance-suppression — every suppression safety path in one team.
 *
 * The registry gives this team two v1 panels so multi-panel unanimity is exercised: a row
 * hidden by both is suppressed, a row hidden by only one is not.
 */
export const acceptanceSuppression = {
  name: 'acceptance-suppression',
  phase: 'acceptance',
  quality: 'mixed',
  schemas: ['v1'],
  v1Operators: ['acc-suppression'],
  v1PanelQuery: null,
  v2PanelQuery: null,
  v1Defs: [
    // Hidden by BOTH panels -> suppressed (R5).
    { application: 'acc-sup-app', obj: 's01', node_name: 'junk-node', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-suppression', alert_rule_url: RULE_URL, rowsAt: [T20] },
    // Hidden by panel A only (its message matches A's NOT LIKE) -> NOT suppressed.
    { application: 'acc-sup-app', obj: 's02', node_name: 'real-node-1', message: 'canary probe reported a fault',
      operatorPick: 'acc-suppression', alert_rule_url: RULE_URL, rowsAt: [T20] },
    // Named only inside panel B's OR-nested leaf -> unmeasured, never suppressed.
    { application: 'acc-sup-app', obj: 's03', node_name: 'or-nested-node', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-suppression', alert_rule_url: RULE_URL, rowsAt: [T20] },
    // Named only by an unresolved query variable -> unmeasured, never suppressed.
    { application: 'acc-sup-app', obj: 's04', node_name: 'query-var-node', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-suppression', alert_rule_url: RULE_URL, rowsAt: [T20] },
    // Visible in both panels.
    { application: 'acc-sup-app', obj: 's05', node_name: 'real-node-2', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-suppression', alert_rule_url: RULE_URL, rowsAt: [T20] },
    { application: 'acc-sup-app', obj: 's06', node_name: 'real-node-3', message: GOOD_V1_MESSAGE,
      operatorPick: 'acc-suppression', alert_rule_url: RULE_URL, rowsAt: [T20] },
  ],
};

/**
 * acceptance-blast-radius — a single panel whose exclusion list reaches more than half
 * the team's owned rows, which the guard must refuse to apply.
 *
 * 3 of 5 rows (60%) are named by the exclusion, so the leaf becomes unmeasured and NO row
 * is suppressed.
 */
export const acceptanceBlastRadius = {
  name: 'acceptance-blast-radius',
  phase: 'acceptance',
  quality: 'mixed',
  schemas: ['v1'],
  v1Operators: ['acc-blast'],
  v1PanelQuery: null,
  v2PanelQuery: null,
  v1Defs: ['b01', 'b02', 'b03', 'b04', 'b05'].map((obj, i) => ({
    application: 'acc-blast-app',
    obj,
    node_name: `blast-node-${i + 1}`,
    message: GOOD_V1_MESSAGE,
    operatorPick: 'acc-blast',
    alert_rule_url: RULE_URL,
    rowsAt: [T20],
  })),
};

/** Every acceptance team, appended after the realistic ones. */
export const acceptanceTeams = [
  acceptanceCore,
  acceptanceBatching,
  acceptanceSuppression,
  acceptanceBlastRadius,
];
