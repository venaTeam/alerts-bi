import test from 'node:test';
import assert from 'node:assert/strict';
import { EsClient } from '../../src/es/client.js';
import { readSchema, readTeamAlerts, buildQuery, V1_INDEX } from '../../src/es/reader.js';
import { buildRunWindow } from '../../src/domain/window.js';
import { loadRegistry, selectTeam } from '../../src/registry/registry.js';
import { loadConfig } from '../../src/config/env.js';

/**
 * Integration tests against the local mock Elasticsearch from docker-compose.yml.
 *
 * Read-only: these never write to or delete from the indices.
 */

const config = loadConfig();
const client = new EsClient(config.es);

/** The mock dataset is generated against this fixed clock. */
const MOCK_NOW = '2026-08-25T18:00:00Z';
const WINDOW = buildRunWindow(MOCK_NOW);

const available = await client
  .indexExists(V1_INDEX)
  .then((ok) => ok)
  .catch(() => false);

const skip = available
  ? false
  : 'mock Elasticsearch is not reachable; run: docker compose up -d && node scripts/generate-mock-alerts.mjs';

test('the query is scoped to the exact operators and the half-open window', { skip }, () => {
  const query = /** @type {any} */ (buildQuery(['a', 'b'], WINDOW));
  const filters = query.bool.filter;
  assert.deepEqual(filters[0], { terms: { operator: ['a', 'b'] } });
  assert.equal(filters[1].range['@timestamp'].gte, '2026-08-18T18:00:00.000Z');
  assert.equal(filters[1].range['@timestamp'].lt, '2026-08-25T18:00:00.000Z');
  assert.equal('lte' in filters[1].range['@timestamp'], false);
});

test('reads only the selected team operators', { skip }, async () => {
  const team = selectTeam(loadRegistry(), 'fraud-detection');
  const { rows } = await readSchema(client, 'v1', team.v1_operators, WINDOW);
  assert.ok(rows.length > 0, 'expected the fraud-detection fixture to have rows in the window');
  const operators = new Set(rows.map((r) => r.operator));
  assert.deepEqual([...operators], ['fraud-detection']);
});

test('operator matching is exact and case-sensitive', { skip }, async () => {
  const exact = await readSchema(client, 'v1', ['batch-team'], WINDOW);
  const wrongCase = await readSchema(client, 'v1', ['BATCH-TEAM'], WINDOW);
  assert.ok(exact.rows.length > 0);
  assert.equal(wrongCase.rows.length, 0);
});

test('a team listing several case variants gets all of them', { skip }, async () => {
  const team = selectTeam(loadRegistry(), 'legacy-batch-jobs');
  const { rows } = await readSchema(client, 'v1', team.v1_operators, WINDOW);
  const operators = new Set(rows.map((r) => r.operator));
  for (const op of operators)
    assert.ok(team.v1_operators.includes(op), `unexpected operator ${op}`);
  assert.ok(operators.size > 1, 'expected several operator variants for this team');
});

test('every returned row falls inside the half-open window', { skip }, async () => {
  const team = selectTeam(loadRegistry(), 'checkout-api');
  const { rows } = await readSchema(client, 'v1', team.v1_operators, WINDOW);
  assert.ok(rows.length > 0);
  for (const row of rows) {
    const t = row.timestamp.getTime();
    assert.ok(
      t >= WINDOW.windowStart.getTime(),
      `row before window: ${row.timestamp.toISOString()}`,
    );
    assert.ok(
      t < WINDOW.windowEnd.getTime(),
      `row at or after window end: ${row.timestamp.toISOString()}`,
    );
  }
});

test(
  'pagination is exact: a tiny page size returns the same rows as a large one',
  { skip },
  async () => {
    const team = selectTeam(loadRegistry(), 'legacy-batch-jobs');
    const big = await readSchema(client, 'v1', team.v1_operators, WINDOW, { pageSize: 5000 });
    const small = await readSchema(client, 'v1', team.v1_operators, WINDOW, { pageSize: 37 });

    assert.equal(small.rows.length, big.rows.length);
    assert.ok(small.pages > big.pages, 'the small page size should have needed more pages');

    // Same multiset of documents, order-independent: paging must not skip or duplicate.
    const hashes = (r) => r.rows.map((x) => x.docHash).sort();
    assert.deepEqual(hashes(small), hashes(big));
  },
);

test('the retrieved row count matches the count the cluster reports', { skip }, async () => {
  const team = selectTeam(loadRegistry(), 'data-pipeline-etl');
  const result = await readSchema(client, 'v1', team.v1_operators, WINDOW, { pageSize: 500 });
  assert.equal(result.rows.length, result.reportedTotal);
});

test('a team with no v2 operator skips the v2 query entirely', { skip }, async () => {
  const team = selectTeam(loadRegistry(), 'legacy-batch-jobs');
  assert.equal(team.v2_operator, null);
  const { v2 } = await readTeamAlerts(client, team, WINDOW);
  assert.deepEqual(v2, { rows: [], pages: 0, reportedTotal: 0 });
});

test('a fully migrated team returns v2 rows and no v1 rows', { skip }, async () => {
  const team = selectTeam(loadRegistry(), 'payments-core');
  const { v1, v2 } = await readTeamAlerts(client, team, WINDOW);
  assert.equal(v1.rows.length, 0);
  assert.ok(v2.rows.length > 0);
  assert.deepEqual([...new Set(v2.rows.map((r) => r.operator))], ['payments-core']);
});

test('unattributed alerts belong to no team and are never returned', { skip }, async () => {
  const registry = loadRegistry();
  const claimed = new Set();
  for (const team of registry.registry.teams) {
    for (const op of team.v1_operators) claimed.add(op);
    if (team.v2_operator) claimed.add(team.v2_operator);
  }
  for (const team of registry.registry.teams) {
    const { v1, v2 } = await readTeamAlerts(client, team, WINDOW);
    for (const row of [...v1.rows, ...v2.rows]) {
      assert.ok(claimed.has(row.operator), `row carried unclaimed operator ${row.operator}`);
    }
  }
});

test('rows keep their complete source document', { skip }, async () => {
  const team = selectTeam(loadRegistry(), 'payments-core');
  const { v2 } = await readTeamAlerts(client, team, WINDOW);
  const row = v2.rows[0];
  assert.equal(typeof row.source, 'object');
  assert.ok('key_field' in row.source);
  assert.ok('@timestamp' in row.source);
  assert.equal(row.schema, 'v2');
});
