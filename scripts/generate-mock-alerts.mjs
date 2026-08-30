// Generates a mock multi-team alert dataset across the v1 (Appchi) and v2 (Appchi V2)
// schemas described in alerts_bi_design.md section 1.3, and bulk-loads it into the
// local ES mock ("ECK") started via docker-compose.yml.
//
// This is throwaway test-data tooling for the alerts BI MVP's "mock environment first"
// decision (design doc section 6) — not part of the BI pipeline itself.

import { createHash, randomUUID } from 'node:crypto';
import { acceptanceTeams } from './acceptance-teams.mjs';

const ES_URL = process.env.ES_URL || 'http://localhost:9200';
const NOW = new Date('2026-08-25T18:00:00Z').getTime();
const DAY = 24 * 60 * 60 * 1000;
const HOUR = 60 * 60 * 1000;

// ---- seeded RNG so the dataset is reproducible ----
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rng = mulberry32(20260825);
const pick = (arr) => arr[Math.floor(rng() * arr.length)];
const int = (min, max) => min + Math.floor(rng() * (max - min + 1));

function v1KeyField(application, object, node_name) {
  return `${application}:${object}:${node_name || 'n-a'}`;
}
// v2 key_field is a hash of every field EXCEPT status, message and the time fields
// (design doc section 1.3, confirmed 2026-08-26). Note that impact, runbook_url and
// severity ARE in the key, which is why enriching an alert mints a new one (section 3.7).
const V2_KEY_FIELDS = ['application', 'component', 'severity', 'impact', 'runbook_url',
  'environment', 'site', 'operator', 'node_name', 'network', 'alert_rule_url', 'provider'];
function v2KeyField(doc) {
  const h = createHash('sha1')
    .update(V2_KEY_FIELDS.map((f) => `${doc[f] ?? ''}`).join('|'))
    .digest('hex');
  return h.slice(0, 16);
}

// ---- notification-policy repeat intervals (design doc section 1.1) ----
// v1 re-fires a still-active Grafana alert every 5 MINUTES; v2 every 12 HOURS.
// A def's (refireCount x intervalHours) authors how long the alert was firing; the
// actual row count is that duration divided by the schema's real repeat interval.
// Only Grafana-provider alerts are re-fired by the notification policy — API alerts
// are sent by the client at whatever cadence it chooses, so those keep their authored
// cadence (see open question 7.3.3, API re-fire behaviour is not yet known).
const V1_REPEAT_MS = 5 * 60 * 1000;
const V2_REPEAT_MS = 12 * HOUR;

function firingDurationMs(def) {
  return ((def.refireCount ?? 1) - 1) * (def.intervalHours ?? 12) * HOUR;
}
function repeats(def, repeatMs) {
  if ((def.provider || 'grafana') !== 'grafana') {
    return { n: def.refireCount ?? 1, stepMs: (def.intervalHours ?? 12) * HOUR };
  }
  return { n: Math.floor(firingDurationMs(def) / repeatMs) + 1, stepMs: repeatMs };
}
const v1Repeats = (def) => repeats(def, V1_REPEAT_MS);
const v2Repeats = (def) => repeats(def, V2_REPEAT_MS);

// ---- shared bad-alert content pools (what_is_an_incorrect_alert_EN.md) ----
const RULE1_GENERIC = ['Error Occurred', 'Something went wrong', 'Unable to get data', 'Alert triggered', 'Issue detected'];
const RULE2_HEARTBEAT = ['i am alive', 'service started', 'healthy', 'OK', 'completed successfully', 'process running'];
const PLACEHOLDER_VALUES = ['Unknown', 'Test', 'Default', 'N/A'];

// ---- alert-def -> row expansion ----
// def: { application, obj, node_name, message, severity, provider, alert_rule_url,
//        badRule: [..], refireCount, intervalHours, resolves, environment, impact,
//        runbook_url, status_v2, operatorPick }
// Acceptance defs pin their rows exactly instead of deriving them from a repeat
// interval, so an expected-results manifest can be hand-computed from the definition
// rather than reverse-engineered from generated output.
//
//   rowsAt:      explicit ISO timestamps, one row each
//   timeCreated: 'valid' (default) | 'equal' | 'oldest' | 'future' | 'stale' | null
//
// 'equal' and 'oldest' are the two INCLUSIVE R7 boundaries and must not be flagged;
// 'future' and 'stale' sit one millisecond outside them and must be.
const TWENTY_FOUR_HOURS = 24 * HOUR;
function timeCreatedFor(def, tsMs) {
  switch (def.timeCreated) {
    case null:
      return null;
    case 'equal':
      return new Date(tsMs).toISOString();
    case 'oldest':
      return new Date(tsMs - TWENTY_FOUR_HOURS).toISOString();
    case 'future':
      return new Date(tsMs + 1).toISOString();
    case 'stale':
      return new Date(tsMs - TWENTY_FOUR_HOURS - 1).toISOString();
    default:
      return new Date(tsMs).toISOString();
  }
}

function expandV1(team, def) {
  const rows = [];
  const operator = def.operatorPick || pick(team.v1Operators);
  const explicit = def.rowsAt ? def.rowsAt.map((iso) => Date.parse(iso)) : null;
  const { n, stepMs } = v1Repeats(def);
  const endOffset = def.recencyDays != null ? def.recencyDays * DAY : int(0, 3) * DAY;
  const lastTs = NOW - endOffset;
  const count = explicit ? explicit.length : n;
  for (let i = 0; i < count; i++) {
    const ts = explicit ? explicit[i] : lastTs - (n - 1 - i) * stepMs;
    const invalidTime = def.badRule?.includes(7);
    rows.push({
      index: 'appchi-v1',
      doc: {
        id: randomUUID(),
        '@timestamp': new Date(ts).toISOString(),
        application: def.application,
        object: def.obj,
        message: def.message,
        severity: def.severity || 'error',
        operator,
        key_field: def.key_field || v1KeyField(def.application, def.obj, def.node_name),
        time_created:
          def.timeCreated !== undefined
            ? timeCreatedFor(def, ts)
            : invalidTime
              ? i === count - 1
                ? null
                : new Date(ts).toISOString()
              : new Date(ts).toISOString(),
        node_name: def.node_name || null,
        network: def.network || null,
        alert_rule_url: def.alert_rule_url || null,
        provider: def.provider || 'grafana',
      },
    });
  }
  return rows;
}

function expandV2(team, def) {
  const rows = [];
  const operator = team.v2Operator;
  const { n, stepMs } = v2Repeats(def);
  const endOffset = def.recencyDays != null ? def.recencyDays * DAY : int(0, 3) * DAY;
  const lastTs = NOW - endOffset;
  const baseDoc = {
    application: def.application,
    component: def.obj,
    message: def.message,
    severity: def.severity || 'warning',
    impact: def.impact ?? null,
    runbook_url: def.runbook_url ?? null,
    environment: def.environment || 'production',
    site: def.site || null,
    node_name: def.node_name || null,
    network: def.network || null,
    alert_rule_url: def.alert_rule_url || null,
    provider: def.provider || 'grafana',
  };
  const explicit = def.rowsAt ? def.rowsAt.map((iso) => Date.parse(iso)) : null;
  const count = explicit ? explicit.length : n;
  for (let i = 0; i < count; i++) {
    const ts = explicit ? explicit[i] : lastTs - (n - 1 - i) * stepMs;
    const status = def.resolves && i === count - 1 ? 'resolved' : 'firing';
    const doc = { ...baseDoc, status };
    rows.push({
      index: 'appchi-v2',
      doc: {
        id: randomUUID(),
        '@timestamp': new Date(ts).toISOString(),
        ...doc,
        operator,
        key_field: def.key_field || v2KeyField({ ...doc, operator }),
        time_created: new Date(ts).toISOString(),
      },
    });
  }
  return rows;
}

// ================= TEAM DEFINITIONS =================
const teams = [];

// 1. payments-core — DONE, fully migrated, GOOD
teams.push({
  name: 'payments-core',
  phase: 'done',
  quality: 'good',
  schemas: ['v2'],
  v2Operator: 'payments-core',
  v1PanelQuery: null,
  v2PanelQuery: `SELECT * FROM appchi_v2_hot WHERE operator = 'payments-core'`,
  v2Defs: [
    { application: 'payments-api', obj: 'payment-processor', message: 'Payment authorization error rate above 3% over 5m', severity: 'critical', impact: 'Customers cannot complete checkout; revenue loss accruing', runbook_url: 'https://runbooks.internal/payments-core/auth-error-rate', alert_rule_url: 'https://grafana.internal/d/pay-1', refireCount: 3, resolves: true, recencyDays: 1 },
    { application: 'payments-api', obj: 'payment-processor', message: 'p99 latency above 900ms on authorize endpoint', severity: 'high', impact: 'Checkout feels slow to customers, cart abandonment risk rises', runbook_url: 'https://runbooks.internal/payments-core/latency', alert_rule_url: 'https://grafana.internal/d/pay-2', refireCount: 2, resolves: true, recencyDays: 4 },
    { application: 'payments-worker', obj: 'settlement-queue', message: 'Settlement queue depth above 5000 for 15m', severity: 'high', impact: 'Merchant payouts will be delayed past SLA', runbook_url: 'https://runbooks.internal/payments-core/settlement-backlog', alert_rule_url: 'https://grafana.internal/d/pay-3', refireCount: 2, resolves: true, recencyDays: 9 },
    { application: 'payments-worker', obj: 'settlement-queue', message: 'Settlement job failure rate above 1% over 10m', severity: 'critical', impact: 'A portion of merchant settlements will fail and require manual reconciliation', runbook_url: 'https://runbooks.internal/payments-core/settlement-failures', alert_rule_url: 'https://grafana.internal/d/pay-4', refireCount: 3, resolves: true, recencyDays: 14 },
    { application: 'payments-api', obj: 'fraud-hold-check', message: 'Fraud-hold check latency above 400ms over 5m', severity: 'warning', impact: 'Slightly slower checkout for a subset of flagged transactions', runbook_url: 'https://runbooks.internal/payments-core/fraud-hold-latency', alert_rule_url: 'https://grafana.internal/d/pay-5', refireCount: 1, resolves: true, recencyDays: 20 },
    { application: 'payments-api', obj: 'card-vault', message: 'Card vault write error rate above 0.5% over 5m', severity: 'critical', impact: 'New card saves fail for customers, blocking future one-click checkout', runbook_url: 'https://runbooks.internal/payments-core/vault-errors', alert_rule_url: 'https://grafana.internal/d/pay-6', refireCount: 2, resolves: true, recencyDays: 27 },
    { application: 'payments-worker', obj: 'refund-processor', message: 'Refund processing lag above 30m for queued refunds', severity: 'warning', impact: 'Customers see delayed refunds and may open support tickets', runbook_url: 'https://runbooks.internal/payments-core/refund-lag', alert_rule_url: 'https://grafana.internal/d/pay-7', refireCount: 2, resolves: true, recencyDays: 33 },
    { application: 'payments-api', obj: 'payment-processor', message: 'Circuit breaker open on upstream acquirer connection', severity: 'critical', impact: 'All payments to that acquirer fail until the breaker resets', runbook_url: 'https://runbooks.internal/payments-core/breaker-open', alert_rule_url: 'https://grafana.internal/d/pay-8', refireCount: 1, resolves: true, recencyDays: 41 },
    { application: 'payments-worker', obj: 'settlement-queue', message: 'Dead-letter queue depth above 50 for settlement jobs', severity: 'high', impact: 'Those settlements will not retry automatically and need manual replay', runbook_url: 'https://runbooks.internal/payments-core/dlq-depth', alert_rule_url: 'https://grafana.internal/d/pay-9', refireCount: 2, resolves: true, recencyDays: 48 },
    { application: 'payments-api', obj: 'rate-limiter', message: 'Upstream acquirer rate-limit responses above 2% over 10m', severity: 'warning', impact: 'A small share of payments will be retried with added latency', runbook_url: 'https://runbooks.internal/payments-core/rate-limit', alert_rule_url: 'https://grafana.internal/d/pay-10', refireCount: 1, resolves: true, recencyDays: 55 },
  ],
});

// 2. legacy-batch-jobs — PHASE 0, not started, REALLY BAD, v1 only
teams.push({
  name: 'legacy-batch-jobs',
  phase: 'phase0-not-started',
  quality: 'bad',
  schemas: ['v1'],
  v1Operators: ['batch-team', 'BATCH_JOBS', 'batch_svc'],
  v1PanelQuery: `SELECT * FROM appchi_v1_hot WHERE operator IN ('batch-team','BATCH_JOBS','batch_svc') AND node_name != 'legacy-heartbeat-node' AND message NOT LIKE '%test%'`,
  v2PanelQuery: null,
  v1Defs: [
    { application: 'nightly-etl', obj: 'job-runner', node_name: 'etl-node-1', message: pick(RULE1_GENERIC), severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/batch-1', badRule: [1, 6], refireCount: 32, intervalHours: 12, recencyDays: 0 },
    { application: 'report-gen', obj: 'job-runner', node_name: 'report-node-2', message: 'Error Occurred', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/batch-2', badRule: [1, 6], refireCount: 28, intervalHours: 12, recencyDays: 2 },
    { application: 'nightly-etl', obj: 'heartbeat', node_name: 'legacy-heartbeat-node', message: 'i am alive', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 18, intervalHours: 12, recencyDays: 0 },
    { application: 'archiver', obj: 'heartbeat', node_name: 'legacy-heartbeat-node', message: 'service started', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 14, intervalHours: 12, recencyDays: 1 },
    { application: 'nightly-etl', obj: 'Unknown', node_name: 'Default', message: 'Unable to get data', severity: 'error', provider: 'grafana', badRule: [1, 3], refireCount: 6, intervalHours: 24, recencyDays: 5 },
    { application: 'report-gen', obj: 'Test', node_name: 'N/A', message: 'Test', severity: 'error', provider: 'grafana', badRule: [3], refireCount: 4, intervalHours: 24, recencyDays: 12 },
    { application: 'archiver', obj: 'job-runner', node_name: 'archiver-node-1', message: 'Manual escalation triggered by script', severity: 'error', provider: 'api', badRule: [4], refireCount: 5, intervalHours: 8, recencyDays: 3 },
    { application: 'report-gen', obj: 'job-runner', node_name: 'report-node-3', message: 'Nightly report generation failed for finance dataset', severity: 'error', provider: 'grafana', badRule: [7], refireCount: 3, intervalHours: 24, recencyDays: 6 },
    { application: 'nightly-etl', obj: 'job-runner', node_name: 'etl-node-4', message: 'ETL job for warehouse sync failed with exit code 1', severity: 'major', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/batch-9', refireCount: 2, intervalHours: 24, recencyDays: 8, resolves: true },
    { application: 'archiver', obj: 'job-runner', node_name: 'archiver-node-2', message: 'Archive job disk write failures above threshold', severity: 'major', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/batch-10', refireCount: 2, intervalHours: 24, recencyDays: 15, resolves: true },
  ],
});

// 3. fraud-detection — PHASE 0, mid-cleanup, IN-BETWEEN (v1 only, decent hygiene)
teams.push({
  name: 'fraud-detection',
  phase: 'phase0-mid-cleanup',
  quality: 'in-between',
  schemas: ['v1'],
  v1Operators: ['fraud-detection'],
  v1PanelQuery: `SELECT * FROM appchi_v1_hot WHERE operator = 'fraud-detection' AND node_name != 'fraud-canary-test'`,
  v2PanelQuery: null,
  v1Defs: [
    { application: 'fraud-scoring', obj: 'scoring-service', node_name: 'fs-node-1', message: 'Fraud score latency above 500ms over 5m', severity: 'major', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/fraud-1', refireCount: 2, intervalHours: 12, recencyDays: 1, resolves: true },
    { application: 'fraud-scoring', obj: 'scoring-service', node_name: 'fs-node-2', message: 'Fraud model inference error rate above 2% over 10m', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/fraud-2', refireCount: 3, intervalHours: 12, recencyDays: 3, resolves: true },
    { application: 'fraud-rules-engine', obj: 'rules-evaluator', node_name: 'fr-node-1', message: 'Rules engine queue depth above 2000 for 10m', severity: 'major', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/fraud-3', refireCount: 2, intervalHours: 12, recencyDays: 6, resolves: true },
    { application: 'fraud-rules-engine', obj: 'rules-evaluator', node_name: 'fr-node-2', message: 'Rule evaluation timeout rate above 1% over 5m', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/fraud-4', refireCount: 1, intervalHours: 12, recencyDays: 10, resolves: true },
    { application: 'fraud-scoring', obj: 'model-loader', node_name: 'fs-node-3', message: 'Fraud model reload failure on deploy', severity: 'major', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/fraud-5', refireCount: 1, intervalHours: 12, recencyDays: 18, resolves: true },
    { application: 'fraud-scoring', obj: 'scoring-service', node_name: 'fs-node-1', message: 'Fraud score cache hit rate below 60% over 15m', severity: 'warning', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/fraud-6', refireCount: 2, intervalHours: 12, recencyDays: 24, resolves: true },
    { application: 'fraud-rules-engine', obj: 'rules-evaluator', node_name: 'fr-node-3', message: 'Manual chargeback review queue above SLA threshold', severity: 'warning', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/fraud-7', refireCount: 1, intervalHours: 12, recencyDays: 31, resolves: true },
    { application: 'fraud-scoring', obj: 'scoring-service', node_name: 'fs-node-2', message: 'Error Occurred', severity: 'error', provider: 'grafana', badRule: [1], refireCount: 2, intervalHours: 12, recencyDays: 2 },
    { application: 'fraud-rules-engine', obj: 'heartbeat', node_name: 'fraud-canary-test', message: 'healthy', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 10, intervalHours: 12, recencyDays: 0 },
  ],
});

// 4. checkout-api — PHASE 1 dual-run, IN-BETWEEN, bad alerts in both schemas
teams.push({
  name: 'checkout-api',
  phase: 'phase1',
  quality: 'in-between',
  schemas: ['v1', 'v2'],
  v1Operators: ['checkout', 'Checkout-API'],
  v2Operator: 'checkout-api',
  v1PanelQuery: `SELECT * FROM appchi_v1_hot WHERE operator IN ('checkout','Checkout-API') AND node_name NOT LIKE 'test-%'`,
  v2PanelQuery: `SELECT * FROM appchi_v2_hot WHERE operator = 'checkout-api'`,
  v1Defs: [
    { application: 'checkout-svc', obj: 'checkout-flow', node_name: 'chk-node-1', message: 'Checkout completion rate below 90% over 15m', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/chk-1', refireCount: 2, intervalHours: 12, recencyDays: 1, resolves: true },
    { application: 'checkout-svc', obj: 'checkout-flow', node_name: 'chk-node-2', message: 'p95 latency above 700ms on checkout submit', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/chk-2', refireCount: 2, intervalHours: 12, recencyDays: 3, resolves: true },
    { application: 'cart-svc', obj: 'cart-store', node_name: 'cart-node-1', message: 'Cart persistence write error rate above 1% over 5m', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/chk-3', refireCount: 3, intervalHours: 12, recencyDays: 6, resolves: true },
    { application: 'cart-svc', obj: 'cart-store', node_name: 'cart-node-2', message: 'Cart abandonment spike above baseline by 20%', severity: 'warning', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/chk-4', refireCount: 1, intervalHours: 12, recencyDays: 10, resolves: true },
    { application: 'checkout-svc', obj: 'inventory-check', node_name: 'chk-node-3', message: 'Inventory check timeout rate above 2% over 10m', severity: 'major', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/chk-5', refireCount: 2, intervalHours: 12, recencyDays: 14, resolves: true },
    { application: 'checkout-svc', obj: 'checkout-flow', node_name: 'chk-node-1', message: 'Checkout retry rate above 5% over 10m', severity: 'warning', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/chk-6', refireCount: 1, intervalHours: 12, recencyDays: 20, resolves: true },
    { application: 'cart-svc', obj: 'promo-engine', node_name: 'cart-node-3', message: 'Promo code validation error rate above 3% over 5m', severity: 'warning', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/chk-7', refireCount: 1, intervalHours: 12, recencyDays: 27, resolves: true },
    { application: 'checkout-svc', obj: 'checkout-flow', node_name: 'chk-node-4', message: 'Something went wrong', severity: 'error', provider: 'grafana', badRule: [1], refireCount: 3, intervalHours: 12, recencyDays: 4 },
    { application: 'cart-svc', obj: 'heartbeat', node_name: 'test-node-1', message: 'OK', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 8, intervalHours: 12, recencyDays: 0 },
    { application: 'checkout-svc', obj: 'gateway', node_name: 'chk-node-5', message: 'Manual escalation triggered by script', severity: 'error', provider: 'api', badRule: [4], refireCount: 2, intervalHours: 24, recencyDays: 9 },
  ],
  v2Defs: [
    { application: 'checkout-svc', obj: 'checkout-flow', message: 'Checkout completion rate below 90% over 15m', severity: 'high', impact: 'Customers abandon carts at a higher rate, direct revenue impact', runbook_url: 'https://runbooks.internal/checkout-api/completion-rate', alert_rule_url: 'https://grafana.internal/d/chk-v2-1', refireCount: 2, resolves: true, recencyDays: 1 },
    { application: 'checkout-svc', obj: 'checkout-flow', message: 'p95 latency above 700ms on checkout submit', severity: 'high', impact: 'Checkout feels slow, increasing abandonment', runbook_url: 'https://runbooks.internal/checkout-api/latency', alert_rule_url: 'https://grafana.internal/d/chk-v2-2', refireCount: 2, resolves: true, recencyDays: 3 },
    { application: 'cart-svc', obj: 'cart-store', message: 'Cart persistence write error rate above 1% over 5m', severity: 'high', impact: 'Customers lose cart contents and must re-add items', runbook_url: 'https://runbooks.internal/checkout-api/cart-write-errors', alert_rule_url: 'https://grafana.internal/d/chk-v2-3', refireCount: 2, resolves: true, recencyDays: 5 },
    { application: 'checkout-svc', obj: 'inventory-check', message: 'Inventory check timeout rate above 2% over 10m', severity: 'warning', impact: 'Some checkouts stall waiting on stock confirmation', runbook_url: 'https://runbooks.internal/checkout-api/inventory-timeout', alert_rule_url: 'https://grafana.internal/d/chk-v2-4', refireCount: 1, resolves: true, recencyDays: 8 },
    { application: 'cart-svc', obj: 'promo-engine', message: 'Promo code validation error rate above 3% over 5m', severity: 'warning', impact: 'Customers see promo codes rejected incorrectly', runbook_url: 'https://runbooks.internal/checkout-api/promo-errors', alert_rule_url: 'https://grafana.internal/d/chk-v2-5', refireCount: 1, resolves: true, recencyDays: 12 },
    { application: 'checkout-svc', obj: 'checkout-flow', message: 'Checkout retry rate above 5% over 10m', severity: 'warning', impact: 'More load on payment gateway from retried submissions', runbook_url: 'https://runbooks.internal/checkout-api/retry-rate', alert_rule_url: 'https://grafana.internal/d/chk-v2-6', refireCount: 1, resolves: true, recencyDays: 16 },
    { application: 'cart-svc', obj: 'cart-store', message: 'Cart abandonment spike above baseline by 20%', severity: 'warning', impact: 'Indicates a possible checkout regression affecting conversion', runbook_url: 'https://runbooks.internal/checkout-api/abandonment-spike', alert_rule_url: 'https://grafana.internal/d/chk-v2-7', refireCount: 1, resolves: true, recencyDays: 22 },
    { application: 'checkout-svc', obj: 'gateway', message: 'Payment gateway 5xx rate above 2% over 5m', severity: 'critical', impact: null, runbook_url: 'https://runbooks.internal/checkout-api/gateway-5xx', alert_rule_url: 'https://grafana.internal/d/chk-v2-8', badRule: [8], refireCount: 2, resolves: true, recencyDays: 2 },
    { application: 'checkout-svc', obj: 'checkout-flow', message: 'Checkout service circuit breaker open', severity: 'critical', impact: 'All checkout submissions fail until breaker resets', runbook_url: null, alert_rule_url: 'https://grafana.internal/d/chk-v2-9', badRule: [9], refireCount: 2, resolves: false, recencyDays: 0 },
    { application: 'cart-svc', obj: 'cart-store', message: 'Cart save request received', severity: 'warning', impact: null, runbook_url: null, provider: 'api', badRule: [4], refireCount: 3, resolves: false, recencyDays: 7 },
  ],
});

// 5. notifications-svc — PHASE 1, BAD in both, recreated suppression in v2 (gaming the migration)
teams.push({
  name: 'notifications-svc',
  phase: 'phase1',
  quality: 'bad',
  schemas: ['v1', 'v2'],
  v1Operators: ['notifications', 'notif-svc', 'NOTIF_TEAM'],
  v2Operator: 'notifications-svc',
  v1PanelQuery: `SELECT * FROM appchi_v1_hot WHERE operator IN ('notifications','notif-svc','NOTIF_TEAM') AND node_name != 'notif-canary' AND application != 'sms-gateway-test'`,
  v2PanelQuery: `SELECT * FROM appchi_v2_hot WHERE operator = 'notifications-svc' AND node_name != 'notif-canary'`,
  v1Defs: [
    { application: 'notif-dispatcher', obj: 'dispatch-queue', node_name: 'notif-node-1', message: 'Dispatch queue depth above 10000 for 10m', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/notif-1', refireCount: 2, intervalHours: 12, recencyDays: 2, resolves: true },
    { application: 'email-worker', obj: 'send-worker', node_name: 'email-node-1', message: 'Email send failure rate above 5% over 10m', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/notif-2', refireCount: 2, intervalHours: 12, recencyDays: 5, resolves: true },
    { application: 'notif-dispatcher', obj: 'dispatch-queue', node_name: 'notif-node-2', message: pick(RULE1_GENERIC), severity: 'error', provider: 'grafana', badRule: [1, 6], refireCount: 26, intervalHours: 12, recencyDays: 0 },
    { application: 'sms-gateway', obj: 'sms-send', node_name: 'sms-node-1', message: 'Error Occurred', severity: 'error', provider: 'grafana', badRule: [1, 6], refireCount: 22, intervalHours: 12, recencyDays: 1 },
    { application: 'notif-dispatcher', obj: 'heartbeat', node_name: 'notif-canary', message: 'i am alive', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 16, intervalHours: 12, recencyDays: 0 },
    { application: 'email-worker', obj: 'heartbeat', node_name: 'notif-canary', message: 'service started', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 14, intervalHours: 12, recencyDays: 0 },
    { application: 'sms-gateway-test', obj: 'sms-send', node_name: 'sms-test-node', message: 'completed successfully', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 12, intervalHours: 12, recencyDays: 1 },
    { application: 'notif-dispatcher', obj: 'Default', node_name: 'N/A', message: 'Unable to get data', severity: 'error', provider: 'grafana', badRule: [1, 3], refireCount: 5, intervalHours: 24, recencyDays: 8 },
    { application: 'email-worker', obj: 'send-worker', node_name: 'email-node-2', message: 'Manual escalation triggered by script', severity: 'error', provider: 'api', badRule: [4], refireCount: 4, intervalHours: 24, recencyDays: 11 },
    { application: 'sms-gateway', obj: 'sms-send', node_name: 'sms-node-2', message: 'SMS delivery confirmation lag above 60s', severity: 'major', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/notif-9', badRule: [7], refireCount: 3, intervalHours: 24, recencyDays: 15 },
  ],
  v2Defs: [
    { application: 'notif-dispatcher', obj: 'dispatch-queue', message: 'Dispatch queue depth above 10000 for 10m', severity: 'high', impact: 'Notification delivery delays for all channels', runbook_url: 'https://runbooks.internal/notifications-svc/queue-depth', alert_rule_url: 'https://grafana.internal/d/notif-v2-1', refireCount: 2, resolves: true, recencyDays: 2 },
    { application: 'email-worker', obj: 'send-worker', message: 'Email send failure rate above 5% over 10m', severity: 'high', impact: 'Customers miss transactional emails such as receipts', runbook_url: 'https://runbooks.internal/notifications-svc/email-failures', alert_rule_url: 'https://grafana.internal/d/notif-v2-2', refireCount: 2, resolves: true, recencyDays: 4 },
    { application: 'notif-dispatcher', obj: 'dispatch-queue', message: 'Something went wrong', severity: 'high', impact: null, runbook_url: null, alert_rule_url: 'https://grafana.internal/d/notif-v2-3', badRule: [1, 8, 9], refireCount: 3, resolves: false, recencyDays: 1 },
    { application: 'sms-gateway', obj: 'sms-send', message: 'SMS delivery failure rate above 3% over 10m', severity: 'critical', impact: null, runbook_url: null, alert_rule_url: 'https://grafana.internal/d/notif-v2-4', badRule: [8, 9], refireCount: 2, resolves: false, recencyDays: 0 },
    { application: 'notif-dispatcher', obj: 'heartbeat', node_name: 'notif-canary', message: 'i am alive', severity: 'warning', impact: null, provider: 'api', badRule: [2, 5, 8], refireCount: 12, resolves: false, recencyDays: 0 },
    { application: 'email-worker', obj: 'heartbeat', node_name: 'notif-canary', message: 'healthy', severity: 'warning', impact: null, provider: 'api', badRule: [2, 5, 8], refireCount: 10, resolves: false, recencyDays: 0 },
    { application: 'email-worker', obj: 'send-worker', message: 'Email bounce rate above 8% over 15m', severity: 'warning', impact: 'high bounce rate', runbook_url: 'https://runbooks.internal/notifications-svc/bounce-rate', alert_rule_url: 'https://grafana.internal/d/notif-v2-6', badRule: [10], refireCount: 1, resolves: true, recencyDays: 9 },
    { application: 'sms-gateway', obj: 'sms-send', message: 'SMS provider latency above 2s over 10m', severity: 'warning', impact: 'Delayed SMS delivery for OTP and alerts, users may retry', runbook_url: 'https://runbooks.internal/notifications-svc/sms-latency', alert_rule_url: 'https://grafana.internal/d/notif-v2-7', refireCount: 1, resolves: true, recencyDays: 13 },
    { application: 'notif-dispatcher', obj: 'dispatch-queue', message: 'Dead-letter queue depth above 500 for notification jobs', severity: 'high', impact: 'Those notifications will not retry automatically and are lost', runbook_url: 'https://runbooks.internal/notifications-svc/dlq', alert_rule_url: 'https://grafana.internal/d/notif-v2-8', refireCount: 1, resolves: true, recencyDays: 18 },
    { application: 'email-worker', obj: 'template-render', message: 'Template render error rate above 1% over 5m', severity: 'warning', impact: 'Customers may receive malformed emails for affected templates', runbook_url: 'https://runbooks.internal/notifications-svc/template-errors', alert_rule_url: 'https://grafana.internal/d/notif-v2-9', refireCount: 1, resolves: true, recencyDays: 24 },
  ],
});

// 6. search-platform — PHASE 2 enrich, mostly v2 + small v1 tail, GOOD-ish
teams.push({
  name: 'search-platform',
  phase: 'phase2',
  quality: 'good',
  schemas: ['v1', 'v2'],
  v1Operators: ['search-platform'],
  v2Operator: 'search-platform',
  v1PanelQuery: `SELECT * FROM appchi_v1_hot WHERE operator = 'search-platform'`,
  v2PanelQuery: `SELECT * FROM appchi_v2_hot WHERE operator = 'search-platform'`,
  v1Defs: [
    { application: 'search-api', obj: 'query-handler', node_name: 'search-node-legacy-1', message: 'Legacy query timeout rate above 2% over 10m (pending v2 cutover)', severity: 'warning', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/search-legacy-1', refireCount: 2, intervalHours: 12, recencyDays: 30, resolves: true },
    { application: 'indexer', obj: 'index-builder', node_name: 'search-node-legacy-2', message: 'Legacy index build lag above 15m (pending v2 cutover)', severity: 'warning', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/search-legacy-2', refireCount: 2, intervalHours: 12, recencyDays: 40, resolves: true },
    { application: 'query-router', obj: 'router', node_name: 'search-node-legacy-3', message: 'Legacy router 5xx rate above 1% (pending v2 cutover)', severity: 'error', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/search-legacy-3', refireCount: 1, intervalHours: 12, recencyDays: 55, resolves: true },
  ],
  v2Defs: [
    { application: 'search-api', obj: 'query-handler', message: 'Query error rate above 2% over 10m', severity: 'high', impact: 'Users see failed or empty search results', runbook_url: 'https://runbooks.internal/search-platform/query-errors', alert_rule_url: 'https://grafana.internal/d/search-v2-1', refireCount: 2, resolves: true, recencyDays: 1 },
    { application: 'search-api', obj: 'query-handler', message: 'p99 query latency above 1200ms over 10m', severity: 'high', impact: 'Search feels slow, users may abandon the search flow', runbook_url: 'https://runbooks.internal/search-platform/query-latency', alert_rule_url: 'https://grafana.internal/d/search-v2-2', refireCount: 2, resolves: true, recencyDays: 3 },
    { application: 'indexer', obj: 'index-builder', message: 'Index build lag above 20m over baseline', severity: 'warning', impact: 'Search results reflect stale catalog data for affected items', runbook_url: 'https://runbooks.internal/search-platform/index-lag', alert_rule_url: 'https://grafana.internal/d/search-v2-3', refireCount: 1, resolves: true, recencyDays: 6 },
    { application: 'indexer', obj: 'index-builder', message: 'Index build failure rate above 1% over 15m', severity: 'high', impact: 'New or updated items stop appearing in search results', runbook_url: 'https://runbooks.internal/search-platform/index-failures', alert_rule_url: 'https://grafana.internal/d/search-v2-4', refireCount: 1, resolves: true, recencyDays: 9 },
    { application: 'query-router', obj: 'router', message: 'Router 5xx rate above 1% over 5m', severity: 'critical', impact: 'Search is unavailable for affected users', runbook_url: 'https://runbooks.internal/search-platform/router-5xx', alert_rule_url: 'https://grafana.internal/d/search-v2-5', refireCount: 2, resolves: true, recencyDays: 12 },
    { application: 'query-router', obj: 'router', message: 'Routing table reload failure on deploy', severity: 'high', impact: 'New search backends will not receive traffic until reload succeeds', runbook_url: 'https://runbooks.internal/search-platform/reload-failure', alert_rule_url: 'https://grafana.internal/d/search-v2-6', refireCount: 1, resolves: true, recencyDays: 16 },
    { application: 'search-api', obj: 'ranking-service', message: 'Ranking model inference latency above 300ms over 10m', severity: 'warning', impact: 'Search relevance falls back to a simpler ranking method', runbook_url: 'https://runbooks.internal/search-platform/ranking-latency', alert_rule_url: 'https://grafana.internal/d/search-v2-7', refireCount: 1, resolves: true, recencyDays: 20 },
    { application: 'indexer', obj: 'index-builder', message: 'Index shard allocation imbalance above 30%', severity: 'warning', impact: 'Some queries run slower than others due to hot shards', runbook_url: 'https://runbooks.internal/search-platform/shard-imbalance', alert_rule_url: 'https://grafana.internal/d/search-v2-8', refireCount: 1, resolves: true, recencyDays: 25 },
    { application: 'search-api', obj: 'query-handler', message: 'Cache hit rate below 50% over 15m', severity: 'warning', impact: 'Higher backend load and slower queries during the drop', runbook_url: 'https://runbooks.internal/search-platform/cache-hit-rate', alert_rule_url: 'https://grafana.internal/d/search-v2-9', refireCount: 1, resolves: true, recencyDays: 29 },
    { application: 'query-router', obj: 'router', message: 'Upstream backend timeout rate above 2% over 10m', severity: 'high', impact: 'A subset of queries return errors instead of results', runbook_url: null, alert_rule_url: 'https://grafana.internal/d/search-v2-10', badRule: [9], severity_override: 'critical', refireCount: 2, resolves: true, recencyDays: 34 },
    { application: 'search-api', obj: 'query-handler', message: 'Query volume spike above 3x baseline', severity: 'warning', impact: null, runbook_url: 'https://runbooks.internal/search-platform/volume-spike', alert_rule_url: 'https://grafana.internal/d/search-v2-11', badRule: [8], refireCount: 1, resolves: true, recencyDays: 38 },
    { application: 'indexer', obj: 'index-builder', message: 'Index build queue depth above 200', severity: 'warning', impact: 'New items are delayed from appearing in search', runbook_url: 'https://runbooks.internal/search-platform/build-queue', alert_rule_url: 'https://grafana.internal/d/search-v2-12', refireCount: 1, resolves: true, recencyDays: 44 },
  ],
});

// 7. data-pipeline-etl — PHASE 1, REALLY BAD in both (worst offender)
teams.push({
  name: 'data-pipeline-etl',
  phase: 'phase1',
  quality: 'bad',
  schemas: ['v1', 'v2'],
  v1Operators: ['data-pipeline', 'dp-team', 'ETL_TEAM', 'pipeline'],
  v2Operator: 'data-pipeline-etl',
  v1PanelQuery: `SELECT * FROM appchi_v1_hot WHERE operator IN ('data-pipeline','dp-team','ETL_TEAM','pipeline') AND node_name NOT IN ('dp-heartbeat','dp-test-node') AND message NOT LIKE '%OK%'`,
  v2PanelQuery: `SELECT * FROM appchi_v2_hot WHERE operator = 'data-pipeline-etl' AND node_name NOT IN ('dp-heartbeat','dp-test-node')`,
  v1Defs: [
    { application: 'ingest-worker', obj: 'ingest-queue', node_name: 'dp-node-1', message: pick(RULE1_GENERIC), severity: 'error', provider: 'grafana', badRule: [1, 6], refireCount: 34, intervalHours: 12, recencyDays: 0 },
    { application: 'transform-job', obj: 'job-runner', node_name: 'dp-node-2', message: 'Error Occurred', severity: 'error', provider: 'grafana', badRule: [1, 6], refireCount: 30, intervalHours: 12, recencyDays: 0 },
    { application: 'load-job', obj: 'job-runner', node_name: 'dp-node-3', message: 'Something went wrong', severity: 'error', provider: 'grafana', badRule: [1, 6], refireCount: 24, intervalHours: 12, recencyDays: 1 },
    { application: 'ingest-worker', obj: 'heartbeat', node_name: 'dp-heartbeat', message: 'i am alive', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 20, intervalHours: 12, recencyDays: 0 },
    { application: 'transform-job', obj: 'heartbeat', node_name: 'dp-heartbeat', message: 'OK', severity: 'warning', provider: 'api', badRule: [2, 5], refireCount: 18, intervalHours: 12, recencyDays: 0 },
    { application: 'load-job', obj: 'Unknown', node_name: 'Default', message: 'Unable to get data', severity: 'error', provider: 'grafana', badRule: [1, 3], refireCount: 7, intervalHours: 24, recencyDays: 4 },
    { application: 'ingest-worker', obj: 'Test', node_name: 'dp-test-node', message: 'Test', severity: 'error', provider: 'grafana', badRule: [3, 5], refireCount: 6, intervalHours: 24, recencyDays: 7 },
    { application: 'transform-job', obj: 'job-runner', node_name: 'dp-node-4', message: 'Manual escalation triggered by script', severity: 'error', provider: 'api', badRule: [4], refireCount: 5, intervalHours: 24, recencyDays: 10 },
    { application: 'load-job', obj: 'job-runner', node_name: 'dp-node-5', message: 'Warehouse load job failed with constraint violation', severity: 'major', provider: 'grafana', badRule: [7], refireCount: 4, intervalHours: 24, recencyDays: 13 },
    { application: 'ingest-worker', obj: 'ingest-queue', node_name: 'dp-node-6', message: 'Source connector authentication failure', severity: 'major', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/dp-10', refireCount: 1, intervalHours: 24, recencyDays: 20, resolves: true },
  ],
  v2Defs: [
    { application: 'ingest-worker', obj: 'ingest-queue', message: 'Ingest queue depth above 20000 for 15m', severity: 'high', impact: null, runbook_url: null, badRule: [8, 9], refireCount: 22, resolves: false, recencyDays: 0 },
    { application: 'transform-job', obj: 'job-runner', message: 'Transform job failure rate above 10% over 15m', severity: 'critical', impact: null, runbook_url: null, badRule: [8, 9], refireCount: 18, resolves: false, recencyDays: 0 },
    { application: 'load-job', obj: 'job-runner', message: 'Warehouse load failure rate above 5% over 15m', severity: 'critical', impact: null, runbook_url: null, badRule: [8, 9], refireCount: 3, resolves: false, recencyDays: 1 },
    { application: 'ingest-worker', obj: 'heartbeat', node_name: 'dp-heartbeat', message: 'i am alive', severity: 'warning', impact: null, provider: 'api', badRule: [2, 5, 8], refireCount: 15, resolves: false, recencyDays: 0 },
    { application: 'transform-job', obj: 'heartbeat', node_name: 'dp-heartbeat', message: 'healthy', severity: 'warning', impact: null, provider: 'api', badRule: [2, 5, 8], refireCount: 13, resolves: false, recencyDays: 0 },
    { application: 'load-job', obj: 'job-runner', message: 'high cpu usage on load worker pods', severity: 'warning', impact: 'high cpu usage', runbook_url: 'https://runbooks.internal/data-pipeline-etl/cpu', badRule: [10], refireCount: 2, resolves: true, recencyDays: 5 },
    { application: 'ingest-worker', obj: 'ingest-queue', message: 'Manual escalation triggered by script', severity: 'high', impact: null, runbook_url: null, provider: 'api', badRule: [4, 8, 9], refireCount: 3, resolves: false, recencyDays: 8 },
    { application: 'transform-job', obj: 'job-runner', message: 'Transform schema validation error rate above 3% over 10m', severity: 'high', impact: 'Downstream tables receive incomplete rows for affected sources', runbook_url: 'https://runbooks.internal/data-pipeline-etl/schema-validation', refireCount: 1, resolves: true, recencyDays: 11 },
    { application: 'load-job', obj: 'job-runner', message: 'Warehouse connection pool exhaustion', severity: 'high', impact: 'New load jobs queue and downstream tables fall behind', runbook_url: 'https://runbooks.internal/data-pipeline-etl/pool-exhaustion', refireCount: 1, resolves: true, recencyDays: 17 },
    { application: 'ingest-worker', obj: 'ingest-queue', message: 'Dead-letter queue depth above 1000 for ingest jobs', severity: 'critical', impact: null, runbook_url: null, badRule: [8, 9], refireCount: 2, resolves: false, recencyDays: 3 },
  ],
});

// Acceptance fixture teams (design section 7.5, acceptance-data contract). Appended LAST
// on purpose: expansion consumes the seeded RNG in team order, so adding these at the end
// leaves every realistic team's generated data byte-stable. Acceptance defs pin their own
// operator and timestamps, so they consume no RNG draws themselves.
teams.push(...acceptanceTeams);

// Unattributed orphans — match no team's registry (design doc section 6: must be reported explicitly)
const unattributed = {
  name: 'Unattributed',
  v1: [
    { application: 'mystery-svc', obj: 'unknown-flow', node_name: 'ghost-node-1', message: 'Unhandled exception in request pipeline', severity: 'error', operator: 'ghost-team-alpha', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/ghost-1', refireCount: 2, intervalHours: 24, recencyDays: 5 },
    { application: 'mystery-svc', obj: 'unknown-flow', node_name: 'ghost-node-2', message: 'Retry budget exhausted for downstream call', severity: 'major', operator: 'unregistered-legacy-cron', provider: 'grafana', alert_rule_url: 'https://grafana.internal/d/ghost-2', refireCount: 1, intervalHours: 24, recencyDays: 15 },
  ],
  v2: [
    { application: 'shadow-api', obj: 'edge-handler', message: 'Edge handler 5xx rate above 2% over 10m', severity: 'high', impact: 'Requests through this edge fail for an unknown subset of traffic', runbook_url: null, operator: 'unmapped-svc-9', refireCount: 2, resolves: true, recencyDays: 2 },
    { application: 'shadow-api', obj: 'edge-handler', message: 'Edge handler restart loop detected', severity: 'critical', impact: null, runbook_url: null, operator: 'api-key-not-in-registry', refireCount: 1, resolves: false, recencyDays: 0 },
  ],
};

// ================= EXPAND ALL DEFS INTO ROWS =================
const bulkLines = [];
const summary = [];
const ruleBreakdown = []; // { team, schema, rule, rows, distinct }

function tallyRules(teamName, schema, defs, rowCountFn) {
  const perRule = {}; // rule -> { rows, distinct }
  for (const def of defs) {
    const rows = rowCountFn(def);
    for (const rule of def.badRule || []) {
      perRule[rule] ??= { rows: 0, distinct: 0 };
      perRule[rule].rows += rows;
      perRule[rule].distinct += 1;
    }
  }
  for (const [rule, v] of Object.entries(perRule)) {
    ruleBreakdown.push({ team: teamName, schema, rule: Number(rule), rows: v.rows, distinct: v.distinct });
  }
}

for (const team of teams) {
  let v1Rows = 0, v2Rows = 0, v1Distinct = 0, v2Distinct = 0;
  if (team.v1Defs) {
    for (const def of team.v1Defs) {
      const rows = expandV1(team, def);
      v1Rows += rows.length;
      v1Distinct += 1;
      for (const r of rows) bulkLines.push(r);
    }
    tallyRules(team.name, 'v1', team.v1Defs, (d) => v1Repeats(d).n);
  }
  if (team.v2Defs) {
    for (const def of team.v2Defs) {
      const rows = expandV2(team, def);
      v2Rows += rows.length;
      v2Distinct += 1;
      for (const r of rows) bulkLines.push(r);
    }
    tallyRules(team.name, 'v2', team.v2Defs, (d) => v2Repeats(d).n);
  }
  summary.push({ team: team.name, phase: team.phase, quality: team.quality, v1Rows, v2Rows, v1Distinct, v2Distinct });
}

// Unattributed
for (const def of unattributed.v1) {
  const { n, stepMs } = v1Repeats(def);
  const lastTs = NOW - (def.recencyDays ?? 0) * DAY;
  for (let i = 0; i < n; i++) {
    const ts = lastTs - (n - 1 - i) * stepMs;
    bulkLines.push({
      index: 'appchi-v1',
      doc: {
        id: randomUUID(), '@timestamp': new Date(ts).toISOString(),
        application: def.application, object: def.obj, message: def.message,
        severity: def.severity, operator: def.operator,
        key_field: v1KeyField(def.application, def.obj, def.node_name),
        time_created: new Date(ts).toISOString(), node_name: def.node_name,
        network: null, alert_rule_url: def.alert_rule_url || null, provider: def.provider || 'grafana',
      },
    });
  }
}
for (const def of unattributed.v2) {
  const { n, stepMs } = v2Repeats(def);
  const lastTs = NOW - (def.recencyDays ?? 0) * DAY;
  for (let i = 0; i < n; i++) {
    const ts = lastTs - (n - 1 - i) * stepMs;
    const status = def.resolves && i === n - 1 ? 'resolved' : 'firing';
    const doc = { application: def.application, component: def.obj, message: def.message, severity: def.severity, status, impact: def.impact ?? null, runbook_url: def.runbook_url ?? null, environment: 'production', site: null, node_name: null, network: null, alert_rule_url: null, provider: 'grafana' };
    bulkLines.push({
      index: 'appchi-v2',
      doc: { id: randomUUID(), '@timestamp': new Date(ts).toISOString(), ...doc, operator: def.operator, key_field: v2KeyField({ ...doc, operator: def.operator }), time_created: new Date(ts).toISOString() },
    });
  }
}

// ================= BULK LOAD =================
import { writeFileSync } from 'node:fs';

// Explicit mappings so a clean reload is reproducible. Without them the first document
// decides the mapping dynamically, which makes a reloaded index depend on insertion
// order. `operator` and `key_field` must be keyword for exact, case-sensitive term
// matching - the whole ownership model rests on that.
const INDEX_MAPPINGS = {
  'appchi-v1': {
    '@timestamp': { type: 'date' },
    id: { type: 'keyword' },
    application: { type: 'keyword' },
    object: { type: 'keyword' },
    message: { type: 'text', fields: { raw: { type: 'keyword', ignore_above: 1024 } } },
    severity: { type: 'keyword' },
    operator: { type: 'keyword' },
    key_field: { type: 'keyword' },
    time_created: { type: 'date' },
    node_name: { type: 'keyword' },
    network: { type: 'keyword' },
    alert_rule_url: { type: 'keyword' },
    provider: { type: 'keyword' },
  },
  'appchi-v2': {
    '@timestamp': { type: 'date' },
    id: { type: 'keyword' },
    application: { type: 'keyword' },
    component: { type: 'keyword' },
    message: { type: 'text', fields: { raw: { type: 'keyword', ignore_above: 1024 } } },
    severity: { type: 'keyword' },
    status: { type: 'keyword' },
    impact: { type: 'text', fields: { raw: { type: 'keyword', ignore_above: 1024 } } },
    runbook_url: { type: 'keyword' },
    environment: { type: 'keyword' },
    site: { type: 'keyword' },
    operator: { type: 'keyword' },
    key_field: { type: 'keyword' },
    time_created: { type: 'date' },
    node_name: { type: 'keyword' },
    network: { type: 'keyword' },
    alert_rule_url: { type: 'keyword' },
    provider: { type: 'keyword' },
  },
};

// RESET=1 deletes and recreates both mock indices before loading. The acceptance contract
// requires a clean reload, because a normal rerun APPENDS another copy of every row.
//
// Guarded: this refuses to run against anything but an explicitly local endpoint, so a
// mistyped ES_URL cannot delete a real index.
const LOCAL_ES = /^https?:\/\/(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/i;

async function resetIndices() {
  if (!LOCAL_ES.test(ES_URL)) {
    throw new Error(
      `refusing to reset indices at ${ES_URL}: RESET=1 is only allowed against an explicit local mock endpoint`,
    );
  }
  for (const [index, properties] of Object.entries(INDEX_MAPPINGS)) {
    await fetch(`${ES_URL}/${index}`, { method: 'DELETE' });
    const res = await fetch(`${ES_URL}/${index}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        settings: { number_of_shards: 1, number_of_replicas: 0 },
        mappings: { properties },
      }),
    });
    if (!res.ok) throw new Error(`could not create ${index}: ${await res.text()}`);
    console.log(`recreated ${index} with explicit mappings`);
  }
}

async function bulkLoad() {
  if (!process.env.STATS_ONLY && process.env.RESET) await resetIndices();
  if (!process.env.STATS_ONLY) {
    const CHUNK = 300;
    for (let i = 0; i < bulkLines.length; i += CHUNK) {
      const chunk = bulkLines.slice(i, i + CHUNK);
      const body = chunk.map((r) => `${JSON.stringify({ index: { _index: r.index, _id: r.doc.id } })}\n${JSON.stringify(r.doc)}`).join('\n') + '\n';
      const res = await fetch(`${ES_URL}/_bulk`, { method: 'POST', headers: { 'Content-Type': 'application/x-ndjson' }, body });
      const json = await res.json();
      if (json.errors) {
        const firstErr = json.items.find((it) => it.index?.error);
        console.error('Bulk errors present, first:', JSON.stringify(firstErr));
        process.exitCode = 1;
      }
    }
    await fetch(`${ES_URL}/appchi-v1/_refresh`, { method: 'POST' });
    await fetch(`${ES_URL}/appchi-v2/_refresh`, { method: 'POST' });
    console.log(`Indexed ${bulkLines.length} total rows.`);
  }

  console.table(summary);
  const unattributedV2Rows = unattributed.v2.reduce((a, d) => a + v2Repeats(d).n, 0);
  console.log('Unattributed:', { v1: unattributed.v1.reduce((a, d) => a + v1Repeats(d).n, 0), v2: unattributedV2Rows });

  const statsPath = process.env.STATS_PATH || 'mock-data-stats.json';
  writeFileSync(statsPath, JSON.stringify({
    generatedAt: new Date(NOW).toISOString(),
    totalRows: bulkLines.length,
    summary,
    ruleBreakdown,
    panelQueries: teams.map((t) => ({ team: t.name, v1: t.v1PanelQuery || null, v2: t.v2PanelQuery || null, v1Operators: t.v1Operators || null, v2Operator: t.v2Operator || null })),
    unattributed: {
      v1Rows: unattributed.v1.reduce((a, d) => a + v1Repeats(d).n, 0),
      v2Rows: unattributedV2Rows,
      v1Operators: unattributed.v1.map((d) => d.operator),
      v2Operators: unattributed.v2.map((d) => d.operator),
    },
  }, null, 2));
  console.log(`Stats written to ${statsPath}`);
}

bulkLoad().catch((err) => { console.error(err); process.exitCode = 1; });
