import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { verifyAcceptance, MANIFEST_PATH } from '../../src/run/verify.js';
import { EsClient } from '../../src/es/client.js';
import { V1_INDEX } from '../../src/es/reader.js';
import { loadConfig } from '../../src/config/env.js';
import { openTestDatabase } from '../helpers/sql.js';

/**
 * Acceptance verification against the hand-reviewed manifest.
 *
 * Requires the mock stack and a clean fixture load:
 *   docker compose up -d
 *   RESET=1 node scripts/generate-mock-alerts.mjs
 */

const config = loadConfig();
const esAvailable = await new EsClient(config.es)
  .indexExists(V1_INDEX)
  .then((ok) => ok)
  .catch(() => false);
const ctx = await openTestDatabase();

const skip = !esAvailable
  ? 'mock Elasticsearch is not reachable; run: docker compose up -d && RESET=1 node scripts/generate-mock-alerts.mjs'
  : !ctx
    ? 'SQL Server is not reachable'
    : false;

if (ctx) await ctx.pool.close();

const OUT_DIR = path.join('out', 'acceptance-test');

test('persisted rows and CSV exports match the hand-reviewed manifest', { skip }, async () => {
  const result = await verifyAcceptance({
    outDir: OUT_DIR,
    database: config.sql.testDatabase,
  });

  if (!result.ok) {
    const detail = result.failures
      .map(
        (f) =>
          `  ${f.where}\n    expected ${JSON.stringify(f.expected)}\n    actual   ${JSON.stringify(f.actual)}`,
      )
      .join('\n');
    assert.fail(
      `${result.failures.length} of ${result.checks} acceptance checks failed:\n${detail}`,
    );
  }
  assert.ok(result.checks > 300, `expected a substantial number of checks, got ${result.checks}`);
});

test('the manifest is hand-authored and records its derivations', () => {
  const manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf8'));
  assert.ok(Array.isArray(manifest._comment));
  assert.match(manifest._comment.join(' '), /computed BY HAND/);
  for (const [teamId, team] of Object.entries(manifest.teams)) {
    assert.ok(
      Array.isArray(/** @type {any} */ (team)._why),
      `${teamId} must record why its numbers are what they are`,
    );
  }
});

test('the manifest covers every acceptance path the design calls out', () => {
  const manifest = JSON.parse(readFileSync(MANIFEST_PATH, 'utf8'));
  const teams = Object.keys(manifest.teams);
  for (const required of [
    'acceptance-core',
    'acceptance-batching',
    'acceptance-suppression',
    'acceptance-blast-radius',
  ]) {
    assert.ok(teams.includes(required), `missing acceptance team ${required}`);
  }

  // The over-200 group must split into balanced partitions differing by at most one.
  const sizes = manifest.teams['acceptance-batching'].llm_batches.partition_sizes_by_group;
  const big = sizes['https://grafana.internal/d/acc-batch-big'];
  assert.deepEqual(big, [134, 134, 133]);
  assert.equal(
    big.reduce((a, b) => a + b, 0),
    401,
  );
  assert.ok(Math.max(...big) - Math.min(...big) <= 1);
});

test('the acceptance scorecards were rendered for every team', { skip }, () => {
  for (const teamId of [
    'acceptance-core',
    'acceptance-batching',
    'acceptance-suppression',
    'acceptance-blast-radius',
  ]) {
    for (const file of [
      'scorecard.html',
      'daily_metrics.csv',
      'rule_counts.csv',
      'alert_worklist.csv',
    ]) {
      assert.ok(existsSync(path.join(OUT_DIR, teamId, file)), `${teamId}/${file}`);
    }
  }
});
