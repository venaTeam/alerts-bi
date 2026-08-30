import test from 'node:test';
import assert from 'node:assert/strict';
import {
  evaluateR8,
  evaluateR9,
  evaluateR10,
  isCompletionReady,
  phase2ReadinessPct,
} from '../../src/rules/readiness.js';
import { derivePhase } from '../../src/rules/phase.js';
import { v1Row, v2Row } from '../helpers/rows.js';

// ------------------------------------------------------------------------- R8

test('R8 flags a missing, null, non-string or empty impact', () => {
  assert.equal(evaluateR8(v2Row({ impact: null })).evidence.reason, 'missing');
  assert.equal(evaluateR8(v2Row({ impact: undefined })).evidence.reason, 'missing');
  assert.equal(evaluateR8(v2Row({ impact: 42 })).evidence.reason, 'not_a_string');
  assert.equal(evaluateR8(v2Row({ impact: '   ' })).evidence.reason, 'empty');
});

test('R8 flags an exact placeholder impact', () => {
  for (const value of ['Unknown', 'Test', 'Default', 'N/A']) {
    assert.equal(evaluateR8(v2Row({ impact: value })).evidence.reason, 'placeholder');
  }
});

test('R8 does not flag a present but poor impact: that is R10 or the LLM', () => {
  assert.equal(evaluateR8(v2Row({ impact: 'high cpu' })), null);
});

test('R8 never applies to v1', () => {
  assert.equal(evaluateR8(v1Row()), null);
});

// ------------------------------------------------------------------------- R9

test('R9 flags a missing, non-string, empty or placeholder runbook_url', () => {
  assert.equal(evaluateR9(v2Row({ runbook_url: null })).evidence.reason, 'missing');
  assert.equal(evaluateR9(v2Row({ runbook_url: 7 })).evidence.reason, 'not_a_string');
  assert.equal(evaluateR9(v2Row({ runbook_url: '  ' })).evidence.reason, 'empty');
  assert.equal(evaluateR9(v2Row({ runbook_url: 'N/A' })).evidence.reason, 'placeholder');
});

test('R9 requires an absolute http(s) URL naming a host', () => {
  for (const bad of [
    '/runbooks/local-path',
    'runbooks.internal/x',
    'ftp://runbooks.internal/x',
    'mailto:oncall@example.com',
    'https://',
    '//runbooks.internal/x',
  ]) {
    assert.equal(evaluateR9(v2Row({ runbook_url: bad })).evidence.reason, 'not_absolute_http', bad);
  }
});

test('R9 accepts a valid absolute http or https URL', () => {
  assert.equal(evaluateR9(v2Row({ runbook_url: 'https://runbooks.internal/a/b?x=1#y' })), null);
  assert.equal(evaluateR9(v2Row({ runbook_url: 'http://runbooks.internal/a' })), null);
});

test('R9 is reported for every severity, and marks only critical as completion-blocking', () => {
  assert.equal(
    evaluateR9(v2Row({ severity: 'critical', runbook_url: null })).evidence.blocks_completion,
    true,
  );
  for (const severity of ['high', 'warning']) {
    const finding = evaluateR9(v2Row({ severity, runbook_url: null }));
    assert.ok(finding, `R9 should still be reported for ${severity}`);
    assert.equal(finding.evidence.blocks_completion, false);
  }
});

test('R9 never applies to v1', () => {
  assert.equal(evaluateR9(v1Row()), null);
});

// ------------------------------------------------------------------------ R10

test('R10 matches only the exact technical-cause catalogue', () => {
  for (const value of ['high cpu', 'High CPU usage', 'CPU usage is high', 'cpu is high']) {
    assert.ok(evaluateR10(v2Row({ impact: value })), value);
  }
});

test('R10 does not substring-match: a causal sentence continues to the LLM', () => {
  assert.equal(evaluateR10(v2Row({ impact: 'high cpu causes checkout latency' })), null);
  assert.equal(evaluateR10(v2Row({ impact: 'Customers cannot complete checkout' })), null);
});

test('R10 never applies to v1 and ignores a non-string impact', () => {
  assert.equal(evaluateR10(v1Row()), null);
  assert.equal(evaluateR10(v2Row({ impact: 42 })), null);
});

// ------------------------------------------------- phase-2 completion readiness

test('an identity with impact, runbook and a real symptom is completion-ready', () => {
  assert.equal(isCompletionReady(v2Row()), true);
});

test('an R8 or R10 gap makes an identity not completion-ready at any severity', () => {
  assert.equal(isCompletionReady(v2Row({ severity: 'warning', impact: null })), false);
  assert.equal(isCompletionReady(v2Row({ severity: 'warning', impact: 'high cpu' })), false);
});

test('a missing runbook blocks completion only for critical', () => {
  assert.equal(isCompletionReady(v2Row({ severity: 'critical', runbook_url: null })), false);
  assert.equal(isCompletionReady(v2Row({ severity: 'high', runbook_url: null })), true);
  assert.equal(isCompletionReady(v2Row({ severity: 'warning', runbook_url: null })), true);
});

test('readiness is only defined for v2', () => {
  assert.throws(() => isCompletionReady(v1Row()), /only defined for v2/);
});

test('phase2_readiness_pct is ready identities over all distinct v2 identities', () => {
  const reps = [
    v2Row({ key_field: 'a' }),
    v2Row({ key_field: 'b' }),
    v2Row({ key_field: 'c', impact: null }),
    v2Row({ key_field: 'd', severity: 'critical', runbook_url: null }),
  ];
  assert.equal(phase2ReadinessPct(reps), 50);
});

test('phase2_readiness_pct is null with no v2 identities, never zero', () => {
  assert.equal(phase2ReadinessPct([]), null);
});

test('a non-critical missing runbook stays visible but does not reduce readiness', () => {
  assert.equal(phase2ReadinessPct([v2Row({ severity: 'high', runbook_url: null })]), 100);
  assert.ok(evaluateR9(v2Row({ severity: 'high', runbook_url: null })));
});

// ------------------------------------------------------------ phase derivation

test('phase derivation covers every branch exhaustively', () => {
  assert.equal(derivePhase(0, 0, null), 'no_data');
  assert.equal(derivePhase(5, 0, null), 'phase_0');
  assert.equal(derivePhase(5, 3, 40), 'phase_1');
  assert.equal(derivePhase(0, 3, 40), 'phase_2');
  assert.equal(derivePhase(0, 3, 100), 'done');
});

test('an empty window is no_data, not phase_0', () => {
  assert.equal(derivePhase(0, 0, null), 'no_data');
});

test('v1 reaching zero does not mark a team done while readiness is incomplete', () => {
  assert.equal(derivePhase(0, 10, 99.9), 'phase_2');
});

test('phase_1 is both schemas present, regardless of readiness', () => {
  assert.equal(derivePhase(1, 1, 100), 'phase_1');
  assert.equal(derivePhase(1, 1, 0), 'phase_1');
});
