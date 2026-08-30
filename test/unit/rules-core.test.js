import test from 'node:test';
import assert from 'node:assert/strict';
import {
  evaluateR1,
  evaluateR2,
  evaluateR3,
  evaluateR4,
  evaluateR7,
  evaluateCoreRules,
} from '../../src/rules/core.js';
import { normalizeMessage, normalizeFieldValue } from '../../src/rules/text.js';
import { v1Row, v2Row } from '../helpers/rows.js';

// ---------------------------------------------------------------- normalization

test('message normalization trims, lowercases, collapses whitespace and strips edge punctuation', () => {
  assert.equal(normalizeMessage('  Error   Occurred!  '), 'error occurred');
  assert.equal(normalizeMessage('"Something went wrong."'), 'something went wrong');
  assert.equal(normalizeMessage('...OK...'), 'ok');
});

test('field normalization keeps punctuation, because n/a is itself a catalogue value', () => {
  // Interior punctuation survives both normalizations, so `n/a` stays matchable either way.
  assert.equal(normalizeFieldValue(' N/A '), 'n/a');
  assert.equal(normalizeMessage('N/A'), 'n/a');
  // The two differ only at the edges, which is why R3 uses the field form.
  assert.equal(normalizeFieldValue('OK.'), 'ok.');
  assert.equal(normalizeMessage('OK.'), 'ok');
});

test('normalization returns null for a non-string', () => {
  assert.equal(normalizeMessage(42), null);
  assert.equal(normalizeFieldValue(null), null);
});

// ------------------------------------------------------------------------- R1

test('R1 matches every catalogue phrase as a whole message', () => {
  for (const phrase of [
    'Error Occurred',
    'Something went wrong',
    'Unable to get data',
    'Alert triggered',
    'Issue detected',
  ]) {
    assert.ok(evaluateR1(v1Row({ message: phrase })), `expected R1 for ${phrase}`);
  }
});

test('R1 does not substring-match: a longer message containing a phrase is not R1', () => {
  assert.equal(evaluateR1(v1Row({ message: 'Error occurred while charging card 4242' })), null);
  assert.equal(evaluateR1(v1Row({ message: 'No issue detected in the last hour' })), null);
});

test('R1 never flags on length alone', () => {
  assert.equal(evaluateR1(v1Row({ message: 'Disk full' })), null);
  assert.equal(evaluateR1(v1Row({ message: 'OOM' })), null);
});

test('R1 evidence names the normalized value that matched', () => {
  const finding = evaluateR1(v1Row({ message: '  ERROR OCCURRED. ' }));
  assert.equal(finding.ruleId, 'R1');
  assert.equal(finding.set, 'core');
  assert.equal(finding.evidence.normalized, 'error occurred');
});

test('R1 applies to both schemas', () => {
  assert.ok(evaluateR1(v2Row({ message: 'Alert triggered' })));
});

// ------------------------------------------------------------------------- R2

test('R2 matches every heartbeat catalogue phrase as a whole message', () => {
  for (const phrase of [
    'i am alive',
    'OK',
    'healthy',
    'started',
    'completed',
    'running',
    'service started',
    'process running',
    'completed successfully',
  ]) {
    assert.ok(evaluateR2(v1Row({ message: phrase })), `expected R2 for ${phrase}`);
  }
});

test('R2 does not substring-match the two documented counter-examples', () => {
  assert.equal(evaluateR2(v1Row({ message: 'backup completed with 10 failures' })), null);
  assert.equal(evaluateR2(v1Row({ message: 'service is not healthy' })), null);
});

// ------------------------------------------------------------------------- R3

test('R3 flags an exact placeholder in a required field', () => {
  for (const value of ['Unknown', 'Test', 'Default', 'N/A']) {
    const finding = evaluateR3(v1Row({ operator: value }));
    assert.ok(finding, `expected R3 for operator=${value}`);
    assert.equal(finding.evidence.violations[0].field, 'operator');
    assert.equal(finding.evidence.violations[0].reason, 'placeholder');
  }
});

test('R3 matching is exact: test-payments-service is not a placeholder', () => {
  assert.equal(evaluateR3(v1Row({ application: 'test-payments-service' })), null);
  assert.equal(evaluateR3(v1Row({ operator: 'unknown-team-alpha' })), null);
});

test('R3 flags an empty required field', () => {
  for (const field of [{ application: '' }, { operator: '   ' }, { object: '' }]) {
    const finding = evaluateR3(v1Row(field));
    assert.ok(finding, `expected R3 for ${JSON.stringify(field)}`);
    assert.equal(finding.evidence.violations[0].reason, 'empty');
  }
});

test('R3 checks component on v1 as object and on v2 as component', () => {
  assert.equal(evaluateR3(v1Row({ object: 'unknown' })).evidence.violations[0].field, 'object');
  assert.equal(
    evaluateR3(v2Row({ component: 'unknown' })).evidence.violations[0].field,
    'component',
  );
});

test('R3 treats an absent or empty node_name as valid', () => {
  assert.equal(evaluateR3(v1Row({ node_name: null })), null);
  assert.equal(evaluateR3(v1Row({ node_name: '' })), null);
  assert.equal(evaluateR3(v1Row({ node_name: '   ' })), null);
});

test('R3 flags a supplied placeholder node_name', () => {
  const finding = evaluateR3(v1Row({ node_name: 'Default' }));
  assert.ok(finding);
  assert.equal(finding.evidence.violations[0].field, 'node_name');
});

test('R3 ignores site, network and alert_rule_url', () => {
  assert.equal(evaluateR3(v2Row({ site: 'unknown', network: 'test' })), null);
  assert.equal(evaluateR3(v2Row({ alert_rule_url: 'default' })), null);
});

test('R3 reports every violating field, not just the first', () => {
  const finding = evaluateR3(v1Row({ application: 'unknown', operator: '' }));
  assert.equal(finding.evidence.violations.length, 2);
});

// ------------------------------------------------------------------------- R4

test('R4 flags a Grafana alert with no rule URL', () => {
  for (const value of [null, '', '   ']) {
    assert.ok(evaluateR4(v1Row({ provider: 'grafana', alert_rule_url: value })));
  }
});

test('R4 does not apply to API alerts, even with no rule URL', () => {
  assert.equal(evaluateR4(v1Row({ provider: 'api', alert_rule_url: null })), null);
  assert.equal(evaluateR4(v2Row({ provider: 'api', alert_rule_url: null })), null);
});

test('R4 does not flag a Grafana alert that has a rule URL', () => {
  assert.equal(evaluateR4(v1Row({ provider: 'grafana', alert_rule_url: 'https://g/d/1' })), null);
});

test('R4 applies to both schemas', () => {
  assert.ok(evaluateR4(v2Row({ provider: 'grafana', alert_rule_url: null })));
});

// ------------------------------------------------------------------------- R7

const RECEIPT = '2026-08-20T12:00:00.000Z';

test('R7 accepts a time_created equal to @timestamp (inclusive upper bound)', () => {
  assert.equal(evaluateR7(v1Row({ '@timestamp': RECEIPT, time_created: RECEIPT })), null);
});

test('R7 accepts a time_created exactly 24 hours old (inclusive lower bound)', () => {
  assert.equal(
    evaluateR7(v1Row({ '@timestamp': RECEIPT, time_created: '2026-08-19T12:00:00.000Z' })),
    null,
  );
});

test('R7 flags a time_created one millisecond in the future', () => {
  const finding = evaluateR7(
    v1Row({ '@timestamp': RECEIPT, time_created: '2026-08-20T12:00:00.001Z' }),
  );
  assert.equal(finding.evidence.reason, 'future');
});

test('R7 flags a time_created one millisecond older than 24 hours', () => {
  const finding = evaluateR7(
    v1Row({ '@timestamp': RECEIPT, time_created: '2026-08-19T11:59:59.999Z' }),
  );
  assert.equal(finding.evidence.reason, 'older_than_24h');
});

test('R7 flags a missing or unparseable time_created', () => {
  assert.equal(evaluateR7(v1Row({ time_created: null })).evidence.reason, 'missing');
  assert.equal(evaluateR7(v1Row({ time_created: '   ' })).evidence.reason, 'missing');
  assert.equal(evaluateR7(v1Row({ time_created: 'yesterday' })).evidence.reason, 'unparseable');
});

test('R7 never applies to v2, which stamps the field itself', () => {
  assert.equal(evaluateR7(v2Row({ time_created: '2030-01-01T00:00:00Z' })), null);
  assert.equal(evaluateR7(v2Row({ time_created: null })), null);
});

// ------------------------------------------------------------- combined core

test('a row can carry several core findings at once', () => {
  const findings = evaluateCoreRules(
    v1Row({
      message: 'i am alive',
      operator: 'unknown',
      provider: 'grafana',
      alert_rule_url: null,
      time_created: null,
    }),
  );
  assert.deepEqual(findings.map((f) => f.ruleId).sort(), ['R2', 'R3', 'R4', 'R7']);
});

test('a clean row produces no core findings', () => {
  assert.deepEqual(evaluateCoreRules(v1Row()), []);
  assert.deepEqual(evaluateCoreRules(v2Row()), []);
});

test('R5 is never produced by the row-level core rules', () => {
  const findings = evaluateCoreRules(v1Row({ message: 'i am alive', operator: 'unknown' }));
  assert.equal(
    findings.some((f) => f.ruleId === 'R5'),
    false,
  );
});

test('R6 is post-MVP and is never produced', () => {
  const rows = Array.from({ length: 50 }, () => v1Row());
  for (const row of rows) {
    assert.equal(
      evaluateCoreRules(row).some((f) => f.ruleId === 'R6'),
      false,
    );
  }
});
