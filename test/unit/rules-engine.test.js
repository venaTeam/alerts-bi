import test from 'node:test';
import assert from 'node:assert/strict';
import {
  evaluateRows,
  attachRowFindings,
  computeDailyRuleCounts,
  computeDailyFlagged,
  countPhase2GapIdentities,
  compareRuleIds,
} from '../../src/rules/engine.js';
import { buildRunWindow } from '../../src/domain/window.js';
import { identityOf } from '../../src/domain/normalize.js';
import { v1Row, v2Row } from '../helpers/rows.js';

const DATES = buildRunWindow('2026-08-25T18:00:00Z').buckets.map((b) => b.snapshotDate);

test('core rules are evaluated on every raw row, not on the representative alone', () => {
  // Same identity, three rows; only the middle one is a heartbeat.
  const rows = [
    v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'k', message: 'Real failure detail' }),
    v1Row({ '@timestamp': '2026-08-20T11:00:00Z', key_field: 'k', message: 'i am alive' }),
    v1Row({ '@timestamp': '2026-08-20T12:00:00Z', key_field: 'k', message: 'Real failure detail' }),
  ];
  const { rows: evaluated } = evaluateRows(rows);
  assert.deepEqual(
    evaluated.map((e) => e.coreFindings.map((f) => f.ruleId)),
    [[], ['R2'], []],
  );
});

test('a finding is not projected onto other rows sharing the identity', () => {
  const rows = [
    v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'k', message: 'i am alive' }),
    v1Row({ '@timestamp': '2026-08-21T10:00:00Z', key_field: 'k', message: 'Real failure detail' }),
  ];
  const { rows: evaluated } = evaluateRows(rows);
  const counts = computeDailyRuleCounts(evaluated, DATES);
  const r2 = counts.filter((c) => c.ruleId === 'R2');
  // Only the date whose row actually matched carries the finding.
  assert.deepEqual(r2, [{ snapshotDate: '2026-08-20', ruleId: 'R2', count: 1, distinctCount: 1 }]);
});

test('any core finding anywhere in the window withholds the whole identity from the LLM', () => {
  const rows = [
    v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'k', message: 'i am alive' }),
    v1Row({ '@timestamp': '2026-08-21T10:00:00Z', key_field: 'k', message: 'Real failure detail' }),
  ];
  const { identities } = evaluateRows(rows);
  const identity = identities.get(identityOf('app-1', 'k'));
  assert.equal(identity.hasCoreFinding, true);
  assert.equal(identity.llmEligible, false);
  assert.deepEqual(identity.coreRuleIds, ['R2']);
});

test('an identity with no core finding stays LLM-eligible', () => {
  const { identities } = evaluateRows([v1Row({ key_field: 'clean' })]);
  const identity = identities.get(identityOf('app-1', 'clean'));
  assert.equal(identity.hasCoreFinding, false);
  assert.equal(identity.llmEligible, true);
});

test('v2 readiness gaps never withhold an identity from the LLM', () => {
  const { identities } = evaluateRows([
    v2Row({ key_field: 'gap', impact: null, runbook_url: null, severity: 'critical' }),
  ]);
  const identity = identities.get(identityOf('app-2', 'gap'));
  assert.deepEqual(identity.readinessRuleIds, ['R8', 'R9']);
  assert.equal(identity.hasCoreFinding, false);
  assert.equal(identity.llmEligible, true);
});

test('readiness gaps are read off the representative, so enrichment clears them', () => {
  const rows = [
    v2Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'k', impact: null }),
    v2Row({ '@timestamp': '2026-08-21T10:00:00Z', key_field: 'k', impact: 'Checkout is slow' }),
  ];
  const { identities } = evaluateRows(rows);
  assert.deepEqual(identities.get(identityOf('app-2', 'k')).readinessRuleIds, []);
});

test('per-rule count is matching rows and distinct_count is matching identities', () => {
  const rows = [
    // identity A: two matching rows on one date
    v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'a', message: 'i am alive' }),
    v1Row({ '@timestamp': '2026-08-20T11:00:00Z', key_field: 'a', message: 'i am alive' }),
    // identity B: one matching row on the same date
    v1Row({ '@timestamp': '2026-08-20T12:00:00Z', key_field: 'b', message: 'healthy' }),
  ];
  const counts = computeDailyRuleCounts(evaluateRows(rows).rows, DATES);
  const r2 = counts.find((c) => c.ruleId === 'R2' && c.snapshotDate === '2026-08-20');
  assert.equal(r2.count, 3);
  assert.equal(r2.distinctCount, 2);
});

test('an identity is counted once in each bucket where it matched', () => {
  const rows = [
    v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'k', message: 'i am alive' }),
    v1Row({ '@timestamp': '2026-08-21T10:00:00Z', key_field: 'k', message: 'i am alive' }),
  ];
  const counts = computeDailyRuleCounts(evaluateRows(rows).rows, DATES);
  const r2 = counts.filter((c) => c.ruleId === 'R2');
  assert.equal(r2.length, 2);
  assert.deepEqual(
    r2.map((c) => c.distinctCount),
    [1, 1],
  );
});

test('flagged_by_rule is the union of rows with any core finding, counted once per row', () => {
  const rows = [
    // one row matching three rules must count once, not three times
    v1Row({
      '@timestamp': '2026-08-20T10:00:00Z',
      key_field: 'a',
      message: 'i am alive',
      operator: 'unknown',
      alert_rule_url: null,
    }),
    v1Row({ '@timestamp': '2026-08-20T11:00:00Z', key_field: 'b' }),
  ];
  const evaluated = evaluateRows(rows).rows;
  assert.equal(evaluated[0].coreFindings.length, 3);
  const flagged = computeDailyFlagged(evaluated, DATES).get('2026-08-20');
  assert.equal(flagged.flaggedByRule, 1);
  assert.equal(flagged.flaggedByRuleDistinct, 1);
});

test('readiness gaps never enter flagged_by_rule', () => {
  const rows = [v2Row({ '@timestamp': '2026-08-20T10:00:00Z', impact: null, runbook_url: null })];
  const flagged = computeDailyFlagged(evaluateRows(rows).rows, DATES).get('2026-08-20');
  assert.equal(flagged.flaggedByRule, 0);
  assert.equal(flagged.flaggedByRuleDistinct, 0);
});

test('readiness rules still appear in the per-rule breakdown', () => {
  const rows = [v2Row({ '@timestamp': '2026-08-20T10:00:00Z', impact: null })];
  const counts = computeDailyRuleCounts(evaluateRows(rows).rows, DATES);
  assert.ok(counts.some((c) => c.ruleId === 'R8' && c.count === 1));
});

test('an externally attached R5 becomes a core finding and withholds the identity', () => {
  const row = v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'k' });
  const evaluation = evaluateRows([row]);
  assert.equal(evaluation.identities.get(identityOf('app-1', 'k')).llmEligible, true);

  attachRowFindings(
    evaluation,
    new Map([[row, { ruleId: 'R5', set: 'core', evidence: { panel_id: 'p1' } }]]),
  );

  const identity = evaluation.identities.get(identityOf('app-1', 'k'));
  assert.deepEqual(identity.coreRuleIds, ['R5']);
  assert.equal(identity.llmEligible, false);
  const flagged = computeDailyFlagged(evaluation.rows, DATES).get('2026-08-20');
  assert.equal(flagged.flaggedByRule, 1);
});

test('attaching R5 to one row of an identity leaves the other rows unmatched', () => {
  const suppressed = v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'k' });
  const visible = v1Row({ '@timestamp': '2026-08-21T10:00:00Z', key_field: 'k' });
  const evaluation = evaluateRows([suppressed, visible]);
  attachRowFindings(
    evaluation,
    new Map([[suppressed, { ruleId: 'R5', set: 'core', evidence: {} }]]),
  );
  const counts = computeDailyRuleCounts(evaluation.rows, DATES).filter((c) => c.ruleId === 'R5');
  assert.deepEqual(counts, [
    { snapshotDate: '2026-08-20', ruleId: 'R5', count: 1, distinctCount: 1 },
  ]);
});

test('identity records the dates it appears on, for per-date LLM state allocation', () => {
  const rows = [
    v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'k' }),
    v1Row({ '@timestamp': '2026-08-22T10:00:00Z', key_field: 'k' }),
  ];
  const identity = evaluateRows(rows).identities.get(identityOf('app-1', 'k'));
  assert.deepEqual([...identity.presentDates].sort(), ['2026-08-20', '2026-08-22']);
});

test('phase-2 gap identities are counted from v2 representatives only', () => {
  const { identities } = evaluateRows([
    v2Row({ key_field: 'a', impact: null }),
    v2Row({ key_field: 'b' }),
  ]);
  assert.equal(countPhase2GapIdentities(identities.values()), 1);
});

test('rule ids sort numerically so R10 does not land between R1 and R2', () => {
  assert.deepEqual(['R10', 'R2', 'R1'].sort(compareRuleIds), ['R1', 'R2', 'R10']);
});

test('the per-rule breakdown is ordered by date then rule number', () => {
  const rows = [
    v2Row({ '@timestamp': '2026-08-21T10:00:00Z', key_field: 'x', impact: 'high cpu' }),
    v2Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'y', message: 'i am alive' }),
  ];
  const counts = computeDailyRuleCounts(evaluateRows(rows).rows, DATES);
  assert.deepEqual(
    counts.map((c) => `${c.snapshotDate}:${c.ruleId}`),
    ['2026-08-20:R2', '2026-08-21:R10'],
  );
});
