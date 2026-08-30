import { normalizeRow } from '../../src/domain/normalize.js';

/**
 * Test-only row builders.
 *
 * `time_created` defaults to the row's own `@timestamp` rather than to a fixed instant:
 * otherwise moving `@timestamp` in a test would make R7 fire as a side effect, and a test
 * about some other rule would silently be testing R7 as well. A test that wants an
 * invalid timestamp sets `time_created` explicitly.
 */

/**
 * @param {Record<string, unknown>} overrides
 * @returns {Record<string, unknown>}
 */
function withDefaults(base, overrides) {
  const merged = { ...base, ...overrides };
  if (!('time_created' in overrides)) merged.time_created = merged['@timestamp'];
  return merged;
}

/**
 * Build a normalized v1 record from partial fields.
 * @param {Record<string, unknown>} overrides
 * @returns {import('../../src/domain/normalize.js').AlertRecord}
 */
export function v1Row(overrides = {}) {
  return normalizeRow(
    'v1',
    withDefaults(
      {
        application: 'app-1',
        object: 'component-1',
        message: 'Payment authorization error rate above 3% over 5m',
        severity: 'error',
        operator: 'team-op',
        key_field: 'app-1:component-1:node-1',
        node_name: 'node-1',
        network: null,
        alert_rule_url: 'https://grafana.internal/d/rule-1',
        provider: 'grafana',
        '@timestamp': '2026-08-20T12:00:00.000Z',
      },
      overrides,
    ),
  );
}

/**
 * Build a normalized v2 record from partial fields.
 * @param {Record<string, unknown>} overrides
 * @returns {import('../../src/domain/normalize.js').AlertRecord}
 */
export function v2Row(overrides = {}) {
  return normalizeRow(
    'v2',
    withDefaults(
      {
        application: 'app-2',
        component: 'component-2',
        message: 'p99 latency above 900ms on the authorize endpoint',
        severity: 'high',
        status: 'firing',
        impact: 'Checkout feels slow to customers',
        runbook_url: 'https://runbooks.internal/app-2/latency',
        environment: 'production',
        site: null,
        operator: 'team-op-v2',
        key_field: 'abcdef0123456789',
        node_name: 'node-2',
        network: null,
        alert_rule_url: 'https://grafana.internal/d/rule-2',
        provider: 'grafana',
        '@timestamp': '2026-08-20T12:00:00.000Z',
      },
      overrides,
    ),
  );
}
