import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, rmSync, existsSync } from 'node:fs';
import path from 'node:path';
import { executeRun } from '../../src/run/orchestrator.js';
import { persistRun, getRun, getDailyMetrics, getFindings } from '../../src/db/repositories.js';
import { renderRunReport, OUTPUT_FILES } from '../../src/report/render.js';
import { EsClient } from '../../src/es/client.js';
import { V1_INDEX } from '../../src/es/reader.js';
import { FakeLlmClient } from '../../src/llm/client-fake.js';
import { loadConfig } from '../../src/config/env.js';
import { openTestDatabase } from '../helpers/sql.js';

/**
 * End-to-end pipeline over the real mock Elasticsearch and a disposable SQL Server
 * database. This is the vertical slice the blueprint's milestone 2 asks for, extended
 * with suppression, LLM assessment and reporting.
 */

const config = loadConfig();
const esClient = new EsClient(config.es);
const esAvailable = await esClient
  .indexExists(V1_INDEX)
  .then((ok) => ok)
  .catch(() => false);
const ctx = await openTestDatabase();

const skip = !esAvailable
  ? 'mock Elasticsearch is not reachable'
  : !ctx
    ? 'SQL Server is not reachable'
    : false;

/** The mock dataset is generated against this fixed clock. */
const RUN_AT = new Date('2026-08-25T18:00:00Z');
const OUT_DIR = path.join('out', 'test-pipeline');

after(async () => {
  if (ctx) await ctx.pool.close();
  if (existsSync(OUT_DIR)) rmSync(OUT_DIR, { recursive: true, force: true });
});

/**
 * @param {string} teamId
 * @param {object} [options]
 */
async function run(teamId, options = {}) {
  const result = await executeRun({
    teamId,
    runAt: RUN_AT,
    config,
    esClient,
    // `in` rather than `??`: passing an explicit null must mean "no model", and a
    // nullish fallback would quietly hand the run a fake client instead.
    llmClient: 'llmClient' in options ? options.llmClient : new FakeLlmClient(),
    llmDisabledReason: options.llmDisabledReason ?? null,
    pool: options.pool ?? null,
  });
  await persistRun(ctx.pool, result.payload);
  return result;
}

test('a full run persists and reconciles with its source rows', { skip }, async () => {
  const { payload, summary } = await run('checkout-api');

  const stored = await getRun(ctx.pool, summary.runId);
  assert.equal(stored.team_id, 'checkout-api');
  assert.equal(stored.status, 'completed');
  assert.equal(stored.llm_assessed, true);

  const daily = await getDailyMetrics(ctx.pool, summary.runId);
  // Eight UTC dates times two schemas.
  assert.equal(daily.length, 16);

  const v1Rows = daily.filter((d) => d.alert_schema === 'v1').reduce((sum, d) => sum + d.alerts, 0);
  assert.equal(v1Rows, summary.v1Rows, 'stored daily rows must sum to the rows actually read');

  const coveredHours = daily
    .filter((d) => d.alert_schema === 'v1')
    .reduce((sum, d) => sum + Number(d.covered_hours), 0);
  assert.equal(coveredHours, 168);

  assert.equal(payload.findings.length, summary.v1Identities + summary.v2Identities);
});

test('re-running the same team and clock is idempotent', { skip }, async () => {
  const first = await run('checkout-api');
  const second = await run('checkout-api');
  assert.equal(first.summary.runId, second.summary.runId, 'a frozen run_at gives a stable run id');
  assert.equal((await getDailyMetrics(ctx.pool, second.summary.runId)).length, 16);
});

test(
  'every identity carries exactly one of the five mutually exclusive states',
  { skip },
  async () => {
    const { summary } = await run('data-pipeline-etl');
    const findings = await getFindings(ctx.pool, summary.runId);
    const allowed = new Set([
      'rule_flagged',
      'llm_flagged',
      'needs_review',
      'assessed_good',
      'unassessed',
    ]);
    assert.ok(findings.length > 0);
    for (const f of findings) assert.ok(allowed.has(f.quality_state), f.quality_state);

    // Identity is unique per run and schema.
    const keys = findings.map((f) => `${f.alert_schema}|${f.application}|${f.key_field}`);
    assert.equal(new Set(keys).size, keys.length);
  },
);

test('an identity with a core finding is never sent to the model', { skip }, async () => {
  const { summary } = await run('legacy-batch-jobs');
  const findings = await getFindings(ctx.pool, summary.runId);
  for (const f of findings) {
    if (f.core_rule_ids) {
      assert.equal(f.quality_state, 'rule_flagged', `${f.key_field} has ${f.core_rule_ids}`);
      assert.equal(f.llm_principle_id, null);
    }
  }
});

test('a v2 readiness gap does not prevent assessment', { skip }, async () => {
  const { summary } = await run('search-platform');
  const findings = await getFindings(ctx.pool, summary.runId);
  const gapped = findings.filter((f) => f.readiness_rule_ids && !f.core_rule_ids);
  if (gapped.length > 0) {
    for (const f of gapped) {
      assert.notEqual(f.quality_state, 'rule_flagged');
      assert.notEqual(f.quality_state, 'unassessed');
    }
  }
});

test(
  'running without a model marks eligible identities unassessed with a reason',
  { skip },
  async () => {
    const { summary } = await run('fraud-detection', {
      llmClient: null,
      llmDisabledReason: 'LLM assessment was disabled for this run',
    });
    const stored = await getRun(ctx.pool, summary.runId);
    assert.equal(stored.llm_assessed, false);

    const findings = await getFindings(ctx.pool, summary.runId);
    const unassessed = findings.filter((f) => f.quality_state === 'unassessed');
    assert.ok(unassessed.length > 0);
    for (const f of unassessed) {
      assert.match(f.unassessed_reason, /disabled/);
    }
  },
);

test('durable verdicts are reused on a second run, issuing no new requests', { skip }, async () => {
  const client = new FakeLlmClient();
  await run('payments-core', { llmClient: client, pool: ctx.pool });
  const firstCalls = client.calls.length;
  assert.ok(firstCalls > 0, 'the first run should call the model');

  const second = new FakeLlmClient();
  await run('payments-core', { llmClient: second, pool: ctx.pool });
  assert.equal(second.calls.length, 0, 'the second run should reuse every stored verdict');
});

test(
  'suppression findings appear as core rule R5 and are counted in suppressed',
  { skip },
  async () => {
    const { summary, payload } = await run('notifications-svc');
    const findings = await getFindings(ctx.pool, summary.runId);
    const suppressed = findings.filter((f) => f.core_rule_ids.split(',').includes('R5'));

    const daily = await getDailyMetrics(ctx.pool, summary.runId);
    const suppressedRows = daily.reduce((sum, d) => sum + d.suppressed, 0);
    const flaggedRows = daily.reduce((sum, d) => sum + d.flagged_by_rule, 0);

    if (suppressed.length > 0) {
      assert.ok(suppressedRows > 0);
      // suppressed is a SUBSET of flagged_by_rule, never an addition to it.
      assert.ok(suppressedRows <= flaggedRows);
    }
    assert.ok(payload.runPanels.length > 0, 'supplied panels are published with the numbers');
  },
);

test(
  'reports render from SQL only and write exactly the four approved files',
  { skip },
  async () => {
    const { summary } = await run('checkout-api');
    const { files } = await renderRunReport(ctx.pool, summary.runId, OUT_DIR);

    assert.deepEqual(
      files.map((f) => path.basename(f)),
      [...OUTPUT_FILES],
    );
    for (const file of files) assert.ok(existsSync(file), file);

    const html = readFileSync(path.join(OUT_DIR, 'scorecard.html'), 'utf8');
    assert.match(html, /Alerts BI scorecard/);
    assert.match(html, /Checkout API/);

    const csv = readFileSync(path.join(OUT_DIR, 'daily_metrics.csv'), 'utf8');
    const dataLines = csv.trim().split('\r\n').slice(1);
    assert.equal(dataLines.length, 16);
  },
);

test(
  'rendering an unknown run id fails rather than producing an empty report',
  { skip },
  async () => {
    await assert.rejects(renderRunReport(ctx.pool, 'f'.repeat(64), OUT_DIR), /not in the store/);
  },
);

test('the CSV daily rows reconcile with the stored rows exactly', { skip }, async () => {
  const { summary } = await run('data-pipeline-etl');
  await renderRunReport(ctx.pool, summary.runId, OUT_DIR);
  const daily = await getDailyMetrics(ctx.pool, summary.runId);

  const csv = readFileSync(path.join(OUT_DIR, 'daily_metrics.csv'), 'utf8');
  const lines = csv.trim().split('\r\n');
  const headers = lines[0].split(',');
  const alertsIndex = headers.indexOf('alerts');

  const csvTotal = lines
    .slice(1)
    .reduce((sum, line) => sum + Number(line.split(',')[alertsIndex]), 0);
  const sqlTotal = daily.reduce((sum, d) => sum + d.alerts, 0);
  assert.equal(csvTotal, sqlTotal);
});

test('a run for an unknown team fails before any Elasticsearch query', { skip }, async () => {
  await assert.rejects(
    executeRun({
      teamId: 'not-a-team',
      runAt: RUN_AT,
      config,
      esClient,
      llmClient: null,
      llmDisabledReason: 'disabled',
    }),
    /is not in the registry/,
  );
});

test('every registered team runs end to end', { skip }, async () => {
  const teams = [
    'payments-core',
    'legacy-batch-jobs',
    'fraud-detection',
    'checkout-api',
    'notifications-svc',
    'search-platform',
    'data-pipeline-etl',
  ];
  for (const teamId of teams) {
    const { summary } = await run(teamId);
    const stored = await getRun(ctx.pool, summary.runId);
    assert.equal(stored.status, 'completed', teamId);
    assert.ok(
      ['no_data', 'phase_0', 'phase_1', 'phase_2', 'done'].includes(stored.phase_derived),
      `${teamId}: ${stored.phase_derived}`,
    );
  }
});
