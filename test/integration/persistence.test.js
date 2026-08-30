import test, { after } from 'node:test';
import assert from 'node:assert/strict';
import {
  persistRun,
  findVerdicts,
  findPanelParse,
  getRun,
  getLatestRun,
  getDailyMetrics,
  getRuleCounts,
  getFindings,
  getBatchAttempts,
  verdictKey,
} from '../../src/db/repositories.js';
import { resetTestDatabase, quoteIdentifier } from '../../src/db/migrate.js';
import { loadConfig } from '../../src/config/env.js';
import {
  openTestDatabase,
  sampleRun,
  sampleDaily,
  sampleFinding,
  sampleVerdict,
  emptyPayload,
} from '../helpers/sql.js';

const ctx = await openTestDatabase();
const skip = ctx
  ? false
  : 'SQL Server is not reachable; run: docker compose up -d && npm run db:migrate';

after(async () => {
  if (ctx) await ctx.pool.close();
});

test('persists a run and reads it back', { skip }, async () => {
  const run = sampleRun();
  await persistRun(ctx.pool, {
    ...emptyPayload(run),
    dailyMetrics: [sampleDaily()],
    ruleCounts: [
      {
        run_id: run.run_id,
        team_id: run.team_id,
        alert_schema: 'v1',
        snapshot_date: '2026-08-20',
        rule_id: 'R2',
        ruleset_version: '1.0.0',
        match_count: 2,
        distinct_count: 1,
      },
    ],
    findings: [sampleFinding()],
  });

  const stored = await getRun(ctx.pool, run.run_id);
  assert.equal(stored.team_id, 'checkout-api');
  assert.equal(stored.phase_derived, 'phase_1');
  assert.equal(Number(stored.phase2_readiness_pct), 50);
  assert.equal(stored.llm_assessed, true);
  assert.equal((await getDailyMetrics(ctx.pool, run.run_id)).length, 1);
  assert.equal((await getRuleCounts(ctx.pool, run.run_id)).length, 1);
  assert.equal((await getFindings(ctx.pool, run.run_id)).length, 1);
});

test(
  're-persisting the same run id replaces its rows rather than duplicating',
  { skip },
  async () => {
    const run = sampleRun({ run_id: 'd'.repeat(64) });
    await persistRun(ctx.pool, {
      ...emptyPayload(run),
      dailyMetrics: [sampleDaily({ run_id: run.run_id })],
    });
    await persistRun(ctx.pool, {
      ...emptyPayload(run),
      dailyMetrics: [sampleDaily({ run_id: run.run_id, alerts: 99 })],
    });

    const daily = await getDailyMetrics(ctx.pool, run.run_id);
    assert.equal(daily.length, 1, 'a rerun must not leave two generations of rows');
    assert.equal(daily[0].alerts, 99);
  },
);

test('persistence is atomic: a bad child row leaves no run behind', { skip }, async () => {
  const run = sampleRun({ run_id: 'e'.repeat(64) });
  await assert.rejects(
    persistRun(ctx.pool, {
      ...emptyPayload(run),
      // Violates ck_daily_metrics_distinct: distinct_alerts must not exceed alerts.
      dailyMetrics: [sampleDaily({ run_id: run.run_id, alerts: 1, distinct_alerts: 5 })],
    }),
  );
  assert.equal(await getRun(ctx.pool, run.run_id), null);
});

test('the store rejects a distinct count above the row count', { skip }, async () => {
  const run = sampleRun({ run_id: 'f'.repeat(64) });
  await assert.rejects(
    persistRun(ctx.pool, {
      ...emptyPayload(run),
      dailyMetrics: [sampleDaily({ run_id: run.run_id, alerts: 2, distinct_alerts: 3 })],
    }),
    /ck_daily_metrics_distinct|CHECK constraint/i,
  );
});

test('the store rejects suppressed exceeding flagged_by_rule', { skip }, async () => {
  const run = sampleRun({ run_id: '1'.repeat(64) });
  await assert.rejects(
    persistRun(ctx.pool, {
      ...emptyPayload(run),
      dailyMetrics: [sampleDaily({ run_id: run.run_id, flagged_by_rule: 1, suppressed: 2 })],
    }),
    /ck_daily_metrics_suppressed|CHECK constraint/i,
  );
});

test('the store rejects an unassessed identity with no reason', { skip }, async () => {
  const run = sampleRun({ run_id: '2'.repeat(64) });
  await assert.rejects(
    persistRun(ctx.pool, {
      ...emptyPayload(run),
      findings: [
        sampleFinding({
          run_id: run.run_id,
          quality_state: 'unassessed',
          unassessed_reason: null,
        }),
      ],
    }),
    /ck_alert_findings_unassessed|CHECK constraint/i,
  );
});

test('the store rejects an invalid assessment/principle pairing', { skip }, async () => {
  const run = sampleRun({ run_id: '3'.repeat(64) });
  await assert.rejects(
    persistRun(ctx.pool, {
      ...emptyPayload(run),
      // no_violation must carry principle NONE, never a catalogue id.
      verdicts: [sampleVerdict({ assessment: 'no_violation', principle_id: 'P1' })],
    }),
    /ck_llm_verdicts_pairing|CHECK constraint/i,
  );
});

test('the store rejects a batch attempt above three or above 200 alerts', { skip }, async () => {
  const run = sampleRun({ run_id: '4'.repeat(64) });
  const attempt = {
    run_id: run.run_id,
    batch_id: 'b'.repeat(64),
    attempt_number: 4,
    group_type: 'application',
    group_value: 'app',
    partition_index: 0,
    partition_count: 1,
    alert_count: 1,
    alert_ids: '[]',
    request_hash: 'h'.repeat(64),
    request_payload: '{}',
    status: 'succeeded',
    failure_reason: null,
    duration_ms: 10,
    created_at: new Date(),
  };
  await assert.rejects(
    persistRun(ctx.pool, { ...emptyPayload(run), batchAttempts: [attempt] }),
    /ck_llm_batch_attempts_number|CHECK constraint/i,
  );
  await assert.rejects(
    persistRun(ctx.pool, {
      ...emptyPayload(run),
      batchAttempts: [{ ...attempt, attempt_number: 1, alert_count: 201 }],
    }),
    /ck_llm_batch_attempts_size|CHECK constraint/i,
  );
});

test('a durable verdict survives a rerun and is never overwritten', { skip }, async () => {
  const run = sampleRun({ run_id: '5'.repeat(64) });
  await persistRun(ctx.pool, {
    ...emptyPayload(run),
    verdicts: [sampleVerdict({ first_run_id: run.run_id })],
  });

  // A second run tries to store a different verdict under the same cache key.
  await persistRun(ctx.pool, {
    ...emptyPayload(sampleRun({ run_id: '6'.repeat(64) })),
    verdicts: [
      sampleVerdict({
        first_run_id: '6'.repeat(64),
        assessment: 'catalog_violation',
        principle_id: 'P2',
        justification: 'changed my mind',
      }),
    ],
  });

  const found = await findVerdicts(ctx.pool, '1.0.0', 'fake-model-1', [
    { application: 'checkout-api', keyField: 'checkout-api:cart:node-1' },
  ]);
  const verdict = found.get(verdictKey('checkout-api', 'checkout-api:cart:node-1'));
  assert.equal(verdict.assessment, 'no_violation', 'the first stored verdict must win');
  assert.equal(verdict.principle_id, 'NONE');
});

test('verdict lookup is scoped to the prompt and model version', { skip }, async () => {
  const hit = await findVerdicts(ctx.pool, '1.0.0', 'fake-model-1', [
    { application: 'checkout-api', keyField: 'checkout-api:cart:node-1' },
  ]);
  assert.equal(hit.size, 1);

  const otherPrompt = await findVerdicts(ctx.pool, '2.0.0', 'fake-model-1', [
    { application: 'checkout-api', keyField: 'checkout-api:cart:node-1' },
  ]);
  assert.equal(otherPrompt.size, 0, 'a version bump must not reuse an old verdict');

  const otherModel = await findVerdicts(ctx.pool, '1.0.0', 'other-model', [
    { application: 'checkout-api', keyField: 'checkout-api:cart:node-1' },
  ]);
  assert.equal(otherModel.size, 0);
});

test('verdicts outlive the run that produced them', { skip }, async () => {
  const found = await findVerdicts(ctx.pool, '1.0.0', 'fake-model-1', [
    { application: 'checkout-api', keyField: 'checkout-api:cart:node-1' },
  ]);
  assert.equal(found.size, 1);
  assert.equal(
    found.get(verdictKey('checkout-api', 'checkout-api:cart:node-1')).first_run_id,
    '5'.repeat(64),
  );
});

test('a panel parse is frozen by sql-text hash and parser version', { skip }, async () => {
  const run = sampleRun({ run_id: '7'.repeat(64) });
  const parse = {
    sql_text_hash: '9'.repeat(64),
    parser_version: '1.0.0',
    parsed_result: '{"leaves":1}',
    safety_state: 'parsed',
    unmeasured_reason: null,
    created_at: new Date(),
  };
  await persistRun(ctx.pool, { ...emptyPayload(run), panelParses: [parse] });
  await persistRun(ctx.pool, {
    ...emptyPayload(sampleRun({ run_id: '8'.repeat(64) })),
    panelParses: [{ ...parse, parsed_result: '{"leaves":999}' }],
  });

  const stored = await findPanelParse(ctx.pool, '9'.repeat(64), '1.0.0');
  assert.equal(stored.parsed_result, '{"leaves":1}', 'identical SQL must keep its interpretation');
  assert.equal(await findPanelParse(ctx.pool, '9'.repeat(64), '2.0.0'), null);
});

test('batch attempts read back in batch and attempt order', { skip }, async () => {
  const run = sampleRun({ run_id: 'c'.repeat(64) });
  const base = {
    run_id: run.run_id,
    group_type: 'alert_rule_url',
    group_value: 'https://grafana.internal/d/x',
    partition_index: 0,
    partition_count: 1,
    alert_count: 2,
    alert_ids: '["a","b"]',
    request_hash: 'h'.repeat(64),
    request_payload: '{}',
    created_at: new Date(),
  };
  await persistRun(ctx.pool, {
    ...emptyPayload(run),
    batchAttempts: [
      {
        ...base,
        batch_id: 'b2'.padEnd(64, '0'),
        attempt_number: 1,
        status: 'succeeded',
        failure_reason: null,
        duration_ms: 5,
      },
      {
        ...base,
        batch_id: 'b1'.padEnd(64, '0'),
        attempt_number: 2,
        status: 'succeeded',
        failure_reason: null,
        duration_ms: 7,
      },
      {
        ...base,
        batch_id: 'b1'.padEnd(64, '0'),
        attempt_number: 1,
        status: 'invalid_response',
        failure_reason: 'alert id set mismatch',
        duration_ms: 6,
      },
    ],
  });
  const attempts = await getBatchAttempts(ctx.pool, run.run_id);
  assert.deepEqual(
    attempts.map((a) => `${a.batch_id.slice(0, 2)}#${a.attempt_number}`),
    ['b1#1', 'b1#2', 'b2#1'],
  );
});

test('getLatestRun returns the most recent completed run for a team', { skip }, async () => {
  const latest = await getLatestRun(ctx.pool, 'checkout-api');
  assert.ok(latest);
  assert.equal(latest.team_id, 'checkout-api');
});

test(
  'resetTestDatabase refuses any database that is not the configured test one',
  { skip },
  async () => {
    const config = loadConfig();
    await assert.rejects(
      resetTestDatabase(config.sql, 'alerts_bi_dev'),
      /only the configured test database/,
    );
    await assert.rejects(
      resetTestDatabase(config.sql, 'master'),
      /only the configured test database/,
    );
  },
);

test('SQL identifiers are validated before ever reaching a DDL statement', { skip }, () => {
  assert.equal(quoteIdentifier('alerts_bi_test'), '[alerts_bi_test]');
  assert.throws(
    () => quoteIdentifier('alerts_bi_test]; DROP DATABASE x --'),
    /unsafe SQL identifier/,
  );
  assert.throws(() => quoteIdentifier('has space'), /unsafe SQL identifier/);
});
