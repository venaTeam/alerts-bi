import { loadConfig } from '../../src/config/env.js';
import { openPool } from '../../src/db/pool.js';
import { resetTestDatabase } from '../../src/db/migrate.js';

/**
 * Integration-test access to the disposable `alerts_bi_test` database.
 *
 * Never the development or a production database: resetTestDatabase refuses any target
 * that is not the configured test database.
 */

/**
 * @returns {Promise<{pool: any, config: import('../../src/config/env.js').AppConfig}|null>}
 *   null when SQL Server is not reachable, so the suite skips instead of failing.
 */
export async function openTestDatabase() {
  const config = loadConfig();
  try {
    await resetTestDatabase(config.sql, config.sql.testDatabase);
    const pool = await openPool(config.sql, config.sql.testDatabase);
    return { pool, config };
  } catch {
    return null;
  }
}

/** Minimal valid run record for persistence tests. */
export function sampleRun(overrides = {}) {
  return {
    run_id: 'a'.repeat(64),
    run_at: new Date('2026-08-25T18:00:00Z'),
    team_id: 'checkout-api',
    team_display_name: 'Checkout API',
    window_start: new Date('2026-08-18T18:00:00Z'),
    window_end: new Date('2026-08-25T18:00:00Z'),
    registry_version: '2026-08-30.1',
    registry_sha256: 'b'.repeat(64),
    registry_entry_snapshot: '{"team_id":"checkout-api"}',
    ruleset_version: '1.0.0',
    prompt_version: '1.0.0',
    model_version: 'fake-model-1',
    llm_assessed: true,
    phase_derived: 'phase_1',
    phase2_readiness_pct: 50,
    app_version: '0.1.0',
    status: 'completed',
    started_at: new Date('2026-08-25T18:00:00Z'),
    completed_at: new Date('2026-08-25T18:00:05Z'),
    error_summary: null,
    ...overrides,
  };
}

/** Minimal valid daily metric row. */
export function sampleDaily(overrides = {}) {
  return {
    run_id: 'a'.repeat(64),
    team_id: 'checkout-api',
    alert_schema: 'v1',
    snapshot_date: '2026-08-20',
    bucket_start: new Date('2026-08-20T00:00:00Z'),
    bucket_end: new Date('2026-08-21T00:00:00Z'),
    covered_hours: 24,
    alerts: 10,
    distinct_alerts: 3,
    alerts_per_hour: 10 / 24,
    node_name_numerator: 3,
    node_name_denominator: 1,
    node_name_ratio: 3,
    key_inflation_numerator: 3,
    key_inflation_denominator: 1,
    key_inflation_ratio: 3,
    flagged_by_rule: 2,
    flagged_by_rule_distinct: 1,
    flagged_by_llm: 0,
    flagged_by_llm_distinct: 0,
    needs_review: 0,
    assessed_good: 2,
    unassessed: 0,
    phase2_gaps: 0,
    suppressed: 1,
    suppression_unmeasured: 0,
    ...overrides,
  };
}

/** Minimal valid finding row. */
export function sampleFinding(overrides = {}) {
  return {
    run_id: 'a'.repeat(64),
    alert_schema: 'v1',
    application: 'checkout-api',
    key_field: 'checkout-api:cart:node-1',
    representative_at: new Date('2026-08-20T12:00:00Z'),
    representative_hash: 'c'.repeat(64),
    representative_doc: '{"application":"checkout-api"}',
    message: 'Cart service error rate above 2%',
    severity: 'error',
    component: 'cart',
    node_name: 'node-1',
    environment: null,
    provider: 'grafana',
    alert_rule_url: 'https://grafana.internal/d/checkout-1',
    row_count: 4,
    first_seen: new Date('2026-08-20T09:00:00Z'),
    last_seen: new Date('2026-08-20T12:00:00Z'),
    core_rule_ids: '',
    readiness_rule_ids: '',
    findings_evidence: '[]',
    quality_state: 'assessed_good',
    llm_principle_id: 'NONE',
    llm_confidence: 'high',
    llm_justification: 'Metric-based, names the component and the symptom.',
    unassessed_reason: null,
    ...overrides,
  };
}

/** Minimal valid verdict row. */
export function sampleVerdict(overrides = {}) {
  return {
    application: 'checkout-api',
    key_field: 'checkout-api:cart:node-1',
    prompt_version: '1.0.0',
    model_version: 'fake-model-1',
    alert_schema: 'v1',
    assessment: 'no_violation',
    principle_id: 'NONE',
    confidence: 'high',
    justification: 'Metric-based and actionable.',
    representative_doc: '{"application":"checkout-api"}',
    doc_hash: 'c'.repeat(64),
    classified_at: new Date('2026-08-25T18:00:01Z'),
    ruleset_version: '1.0.0',
    first_run_id: 'a'.repeat(64),
    ...overrides,
  };
}

/** An empty persistence payload with only the run record populated. */
export function emptyPayload(run) {
  return {
    run,
    dailyMetrics: [],
    ruleCounts: [],
    findings: [],
    batchAttempts: [],
    verdicts: [],
    panelParses: [],
    runPanels: [],
  };
}
