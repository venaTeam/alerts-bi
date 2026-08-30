import test from 'node:test';
import assert from 'node:assert/strict';
import {
  csvCell,
  toCsv,
  dailyMetricsCsv,
  ruleCountsCsv,
  alertWorklistCsv,
} from '../../src/report/csv.js';
import { escapeHtml, rollupSchema, renderScorecard } from '../../src/report/html.js';
import { OUTPUT_FILES } from '../../src/report/render.js';
import { sampleDaily, sampleFinding, sampleRun } from '../helpers/sql.js';

// ----------------------------------------------------------------- CSV safety

test('formula prefixes are neutralized so a cell cannot execute in a spreadsheet', () => {
  for (const dangerous of ['=1+1', '+1', '-1', '@SUM(A1)', "=cmd|' /c calc'!A0"]) {
    assert.equal(csvCell(dangerous).startsWith("'"), true, dangerous);
  }
});

test('a leading formula character inside the text is left alone', () => {
  assert.equal(csvCell('cpu = 90%'), 'cpu = 90%');
});

test('quotes, commas and newlines are escaped rather than breaking the row', () => {
  assert.equal(csvCell('a,b'), '"a,b"');
  assert.equal(csvCell('say "hi"'), '"say ""hi"""');
  assert.equal(csvCell('line1\nline2'), '"line1\nline2"');
});

test('null and undefined become empty cells, not the strings null or undefined', () => {
  assert.equal(csvCell(null), '');
  assert.equal(csvCell(undefined), '');
});

test('dates are written as ISO instants', () => {
  assert.equal(csvCell(new Date('2026-08-20T12:00:00Z')), '2026-08-20T12:00:00.000Z');
});

test('rows are joined with CRLF per RFC 4180', () => {
  assert.equal(toCsv(['a', 'b'], [[1, 2]]), 'a,b\r\n1,2\r\n');
});

test('a neutralized cell that also needs quoting gets both', () => {
  assert.equal(csvCell('=a,b'), `"'=a,b"`);
});

// --------------------------------------------------------------- CSV contract

test('exactly the three approved CSV exports plus the scorecard are written', () => {
  assert.deepEqual(
    [...OUTPUT_FILES],
    ['scorecard.html', 'daily_metrics.csv', 'rule_counts.csv', 'alert_worklist.csv'],
  );
});

test('daily_metrics.csv carries every stored metric column', () => {
  const csv = dailyMetricsCsv([sampleDaily()]);
  const headers = csv.split('\r\n')[0].split(',');
  for (const column of [
    'snapshot_date',
    'covered_hours',
    'alerts',
    'distinct_alerts',
    'alerts_per_hour',
    'node_name_numerator',
    'node_name_denominator',
    'node_name_ratio',
    'key_inflation_numerator',
    'key_inflation_denominator',
    'key_inflation_ratio',
    'flagged_by_rule',
    'flagged_by_rule_distinct',
    'flagged_by_llm',
    'flagged_by_llm_distinct',
    'needs_review',
    'assessed_good',
    'unassessed',
    'phase2_gaps',
    'suppressed',
    'suppression_unmeasured',
  ]) {
    assert.ok(headers.includes(column), `missing column ${column}`);
  }
});

test('rule_counts.csv carries the ruleset version with every count', () => {
  const csv = ruleCountsCsv([
    {
      run_id: 'r',
      team_id: 't',
      alert_schema: 'v1',
      snapshot_date: '2026-08-20',
      rule_id: 'R2',
      ruleset_version: '1.0.0',
      match_count: 3,
      distinct_count: 1,
    },
  ]);
  assert.match(csv, /R2,1\.0\.0,3,1/);
});

test('the work list keeps full values, including a long justification', () => {
  const long = 'x'.repeat(900);
  const csv = alertWorklistCsv([sampleFinding({ llm_justification: long })]);
  assert.ok(csv.includes(long), 'CSV must not truncate what the HTML shortens');
});

test('a work-list message containing a comma stays one field', () => {
  const csv = alertWorklistCsv([sampleFinding({ message: 'cart failed, retries exhausted' })]);
  assert.match(csv, /"cart failed, retries exhausted"/);
});

// -------------------------------------------------------------- HTML escaping

test('HTML-escaping neutralizes markup in alert content', () => {
  assert.equal(
    escapeHtml('<script>alert("x")</script>'),
    '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;',
  );
  assert.equal(escapeHtml("it's"), 'it&#39;s');
  assert.equal(escapeHtml('a & b'), 'a &amp; b');
});

test('a hostile alert message cannot inject markup into the scorecard', () => {
  const html = renderScorecard(
    sampleRun(),
    [sampleDaily()],
    [],
    [
      sampleFinding({
        quality_state: 'rule_flagged',
        core_rule_ids: 'R2',
        message: '<img src=x onerror="alert(1)">',
        application: '</td></tr><script>bad()</script>',
      }),
    ],
    [],
    [],
  );
  assert.equal(html.includes('<img src=x'), false);
  assert.equal(html.includes('<script>bad()</script>'), false);
  assert.match(html, /&lt;img src=x/);
});

// ------------------------------------------------------------------- rollups

test('the scorecard rollup sums rows and divides distinct measures by seven', () => {
  const daily = [
    sampleDaily({ snapshot_date: '2026-08-20', alerts: 100, distinct_alerts: 3 }),
    sampleDaily({ snapshot_date: '2026-08-21', alerts: 68, distinct_alerts: 4 }),
  ];
  const rollup = rollupSchema(daily);
  assert.equal(rollup.alerts, 168);
  assert.equal(rollup.alertsPerHour, 1, 'divides by the full 168 hours, not by covered hours');
  assert.equal(rollup.distinctPerDay, 1);
});

test('diagnostic rollups divide summed numerators by summed denominators', () => {
  const rollup = rollupSchema([
    sampleDaily({ node_name_numerator: 2, node_name_denominator: 1 }),
    sampleDaily({ snapshot_date: '2026-08-21', node_name_numerator: 1, node_name_denominator: 1 }),
  ]);
  assert.equal(rollup.nodeNameRatio, 1.5, 'not the mean of 2 and 1');
});

test('a zero denominator rolls up to null, never zero', () => {
  const rollup = rollupSchema([
    sampleDaily({ node_name_numerator: 0, node_name_denominator: 0, key_inflation_denominator: 0 }),
  ]);
  assert.equal(rollup.nodeNameRatio, null);
  assert.equal(rollup.keyInflationRatio, null);
});

// ------------------------------------------------------------ scorecard shape

/** @returns {string} */
function scorecard(overrides = {}) {
  return renderScorecard(
    sampleRun(overrides.run),
    overrides.daily ?? [
      sampleDaily(),
      sampleDaily({ alert_schema: 'v2', alerts: 2, distinct_alerts: 2 }),
    ],
    overrides.ruleCounts ?? [],
    overrides.findings ?? [sampleFinding()],
    overrides.panels ?? [],
    overrides.attempts ?? [],
  );
}

test('the scorecard contains every required section', () => {
  const html = scorecard();
  for (const heading of [
    'Run metadata',
    'Migration phase',
    'Volume',
    'Data-quality diagnostics',
    'Quality',
    'Dashboard visibility',
    'Rule and principle breakdown',
    'Daily breakdown',
    'Work list',
    'Limitations',
  ]) {
    assert.ok(html.includes(heading), `missing section: ${heading}`);
  }
});

test('the scorecard is self-contained: no external resources', () => {
  const html = scorecard();
  assert.equal(/<script/i.test(html), false, 'no scripts at all');
  assert.equal(/src=["']https?:/i.test(html), false);
  assert.equal(/<link[^>]+stylesheet/i.test(html), false);
  assert.match(html, /<style>/);
});

test('the scorecard shows run and version metadata so a result is reproducible', () => {
  const html = scorecard();
  for (const label of [
    'Registry version',
    'Registry SHA-256',
    'Ruleset version',
    'Prompt version',
    'Model version',
    'Run id',
  ]) {
    assert.ok(html.includes(label), label);
  }
  assert.match(html, /2026-08-30\.1/);
  assert.match(html, /2026-08-18T18:00:00\.000Z/);
});

test('the scorecard carries no cross-run comparison or leaderboard language', () => {
  const html = scorecard().toLowerCase();
  for (const forbidden of [
    'previous run',
    'last week',
    'leaderboard',
    'improvement',
    'trend',
    'baseline',
  ]) {
    // "no trend" and "no ... baseline" appear in Limitations, so check for the claim form.
    assert.equal(
      new RegExp(`(compared|versus|vs\\.?)[^.]{0,40}${forbidden}`).test(html),
      false,
      forbidden,
    );
  }
  assert.match(html, /no trend, delta, baseline or improvement percentage/);
});

test('the scorecard separates the v1 and v2 volume rather than combining it', () => {
  const html = scorecard();
  assert.match(html, /v1 \(Appchi\)/);
  assert.match(html, /v2 \(Appchi V2\)/);
  assert.match(html, /Row counts are never compared across schemas/);
});

test('a run without the model states plainly that nothing was examined', () => {
  const html = scorecard({
    run: { llm_assessed: false, model_version: null },
    daily: [sampleDaily({ assessed_good: 0, unassessed: 3 })],
  });
  assert.match(html, /not examined/);
  assert.match(html, /unassessed/);
});

test('phase-2 readiness renders as a dash when there are no v2 identities', () => {
  const html = scorecard({ run: { phase2_readiness_pct: null, phase_derived: 'phase_0' } });
  assert.match(html, /Phase-2 readiness/);
  assert.ok(html.includes('—'));
});

test('a team with no panels says so instead of showing an empty table', () => {
  assert.match(scorecard({ panels: [] }), /No panel queries were supplied/);
});
