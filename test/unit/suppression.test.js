import test from 'node:test';
import assert from 'node:assert/strict';
import { tokenize, SqlParseError } from '../../src/suppression/lexer.js';
import { parsePanelSql, collectLeaves } from '../../src/suppression/parser.js';
import { classifyField } from '../../src/suppression/fields.js';
import { resolveVariable } from '../../src/suppression/variables.js';
import {
  interpretPanel,
  evaluateSuppression,
  buildR5Findings,
  likeToRegExp,
  BLAST_RADIUS_LIMIT,
} from '../../src/suppression/evaluate.js';
import { v1Row } from '../helpers/rows.js';

/**
 * @param {string} sql
 * @param {any[]} [variables]
 * @returns {import('../../src/registry/registry.js').Panel}
 */
const panel = (sql, variables) => ({
  panel_id: 'p1',
  schema: 'v1',
  sql,
  ...(variables ? { variables } : {}),
});
const leavesOf = (sql, variables) => interpretPanel(panel(sql, variables)).leaves;
const suppressionLeaves = (sql, variables) =>
  leavesOf(sql, variables).filter((l) => l.kind === 'suppression');
const unmeasuredLeaves = (sql, variables) =>
  leavesOf(sql, variables).filter((l) => l.kind === 'unmeasured');

// ------------------------------------------------------------------- lexing

test('tokenizes strings with escaped quotes', () => {
  const tokens = tokenize("WHERE node_name != 'it''s-a-node'");
  assert.equal(tokens.find((t) => t.type === 'string').value, "it's-a-node");
});

test('tokenizes all three Grafana variable syntaxes', () => {
  for (const [text, name] of [
    ['$nodes', 'nodes'],
    ['${nodes}', 'nodes'],
    ['${nodes:csv}', 'nodes'],
    ['[[nodes]]', 'nodes'],
  ]) {
    const tokens = tokenize(`WHERE node_name != ${text}`);
    const variable = tokens.find((t) => t.type === 'variable');
    assert.equal(variable.value, name, text);
  }
});

test('skips line and block comments', () => {
  const { where } = parsePanelSql(
    "SELECT * FROM t WHERE /* hidden */ node_name != 'x' -- trailing\n",
  );
  assert.equal(where.type, 'comparison');
});

test('an unterminated string is a parse error, not a silent truncation', () => {
  assert.throws(() => tokenize("WHERE node_name != 'unclosed"), SqlParseError);
});

// ------------------------------------------------------------------ parsing

test('a query with no WHERE clause suppresses nothing', () => {
  const parsed = parsePanelSql('SELECT * FROM appchi_v1_hot');
  assert.equal(parsed.hasWhere, false);
  assert.deepEqual(leavesOf('SELECT * FROM appchi_v1_hot'), []);
});

test('AND chains flatten to top-level leaves', () => {
  const { where } = parsePanelSql(
    "SELECT * FROM t WHERE operator = 'x' AND node_name != 'a' AND severity = 'critical'",
  );
  const leaves = collectLeaves(where);
  assert.equal(leaves.length, 3);
  assert.equal(
    leaves.every((l) => l.nested === false),
    true,
  );
});

test('leaves inside parentheses that are still AND-ed stay top-level', () => {
  const { where } = parsePanelSql(
    "SELECT * FROM t WHERE (operator = 'x' AND node_name != 'a') AND severity = 'critical'",
  );
  assert.equal(
    collectLeaves(where).every((l) => l.nested === false),
    true,
  );
});

test('leaves inside an OR are marked nested', () => {
  const { where } = parsePanelSql(
    "SELECT * FROM t WHERE operator = 'x' AND (node_name != 'junk' OR severity = 'critical')",
  );
  const leaves = collectLeaves(where);
  assert.equal(leaves.find((l) => l.leaf.field?.name === 'operator').nested, false);
  assert.equal(leaves.find((l) => l.leaf.field?.name === 'node_name').nested, true);
});

test('trailing ORDER BY and LIMIT end the WHERE expression', () => {
  const { where } = parsePanelSql(
    "SELECT * FROM t WHERE node_name != 'a' ORDER BY time DESC LIMIT 100",
  );
  assert.equal(where.type, 'comparison');
});

test('a Grafana time macro parses as a predicate and is ignored', () => {
  const leaves = leavesOf("SELECT * FROM t WHERE $__timeFilter(time_created) AND node_name != 'a'");
  assert.equal(leaves[0].kind, 'ignored');
  assert.match(leaves[0].reason, /mechanical macro/);
  assert.equal(leaves[1].kind, 'suppression');
});

test('a table-qualified column resolves to the bare column name', () => {
  const leaves = suppressionLeaves("SELECT * FROM t a WHERE a.node_name != 'junk'");
  assert.equal(leaves.length, 1);
  assert.equal(leaves[0].field, 'node_name');
});

test('an unparseable panel is unmeasured, never an assumed suppression', () => {
  const interpretation = interpretPanel(panel('SELECT * FROM t WHERE node_name !='));
  assert.equal(interpretation.safetyState, 'unparseable');
  assert.ok(interpretation.unmeasuredReason);
  assert.deepEqual(interpretation.leaves, []);
});

// ------------------------------------------------------- field classification

test('the field table classifies instance fields and classification dimensions', () => {
  for (const f of [
    'node_name',
    'message',
    'object',
    'component',
    'key_field',
    'alert_rule_url',
    'application',
  ]) {
    assert.equal(classifyField(f), 'instance', f);
  }
  for (const f of ['severity', 'environment', 'status', 'provider', 'operator']) {
    assert.equal(classifyField(f), 'classification', f);
  }
  assert.equal(classifyField('some_new_column'), 'unknown');
});

test('a negation on a classification dimension is scoping, not suppression', () => {
  // This is the error that would mark a team's entire non-critical inventory bad.
  assert.deepEqual(suppressionLeaves("SELECT * FROM t WHERE severity = 'critical'"), []);
  assert.deepEqual(suppressionLeaves("SELECT * FROM t WHERE environment != 'test'"), []);
  assert.deepEqual(suppressionLeaves("SELECT * FROM t WHERE status != 'resolved'"), []);
});

test('an identity predicate on operator is ignored: ownership comes from the registry', () => {
  assert.deepEqual(
    suppressionLeaves("SELECT * FROM t WHERE operator IN ('batch-team','BATCH_JOBS')"),
    [],
  );
});

test('an unknown field is ignored and recorded, never guessed at', () => {
  const interpretation = interpretPanel(panel("SELECT * FROM t WHERE mystery_col != 'x'"));
  assert.deepEqual(interpretation.unknownFields, ['mystery_col']);
  assert.equal(interpretation.leaves[0].kind, 'ignored');
});

test('an instance field compared positively is not suppression', () => {
  assert.deepEqual(suppressionLeaves("SELECT * FROM t WHERE application = 'checkout'"), []);
  assert.deepEqual(suppressionLeaves("SELECT * FROM t WHERE node_name IN ('a','b')"), []);
  assert.deepEqual(suppressionLeaves("SELECT * FROM t WHERE message LIKE '%error%'"), []);
});

test('all three negation forms on an instance field are suppression', () => {
  assert.equal(suppressionLeaves("SELECT * FROM t WHERE node_name != 'a'")[0].operator, '!=');
  assert.equal(suppressionLeaves("SELECT * FROM t WHERE node_name <> 'a'")[0].operator, '<>');
  assert.equal(
    suppressionLeaves("SELECT * FROM t WHERE node_name NOT IN ('a','b')")[0].operator,
    'NOT IN',
  );
  assert.equal(
    suppressionLeaves("SELECT * FROM t WHERE message NOT LIKE '%test%'")[0].operator,
    'NOT LIKE',
  );
});

test('application exclusion counts as suppression', () => {
  const leaves = suppressionLeaves("SELECT * FROM t WHERE application != 'legacy-app'");
  assert.equal(leaves.length, 1);
  assert.deepEqual(leaves[0].values, ['legacy-app']);
});

// ------------------------------------------------------------ rewrite safety

test('a suppression leaf nested inside an OR is unmeasured, not applied', () => {
  const leaves = unmeasuredLeaves(
    "SELECT * FROM t WHERE operator = 'x' AND (node_name != 'junk' OR severity = 'critical')",
  );
  assert.equal(leaves.length, 1);
  assert.match(leaves[0].reason, /nested inside OR/);
});

test('a suppression leaf under a NOT is unmeasured', () => {
  const leaves = unmeasuredLeaves("SELECT * FROM t WHERE NOT (node_name != 'junk')");
  assert.equal(leaves.length, 1);
});

// ------------------------------------------------------- template variables

test('custom, constant and interval variables resolve from the frozen definitions', () => {
  assert.deepEqual(
    resolveVariable('nodes', [{ name: 'nodes', type: 'custom', values: ['a', 'b'] }]).values,
    ['a', 'b'],
  );
  assert.deepEqual(
    resolveVariable('env', [{ name: 'env', type: 'constant', value: 'prod' }]).values,
    ['prod'],
  );
  assert.deepEqual(
    resolveVariable('step', [{ name: 'step', type: 'interval', value: '5m' }]).values,
    ['5m'],
  );
});

test('a query variable is never executed and makes its leaf unmeasured', () => {
  const leaves = unmeasuredLeaves("SELECT * FROM t WHERE node_name != '$nodes'", [
    { name: 'nodes', type: 'query' },
  ]);
  assert.equal(leaves.length, 0, 'a quoted variable is a literal string, not a variable');

  const unquoted = unmeasuredLeaves('SELECT * FROM t WHERE node_name != $nodes', [
    { name: 'nodes', type: 'query' },
  ]);
  assert.equal(unquoted.length, 1);
  assert.match(unquoted[0].reason, /query variable and is never executed/);
});

test('a missing variable definition is unmeasured, never guessed', () => {
  const leaves = unmeasuredLeaves('SELECT * FROM t WHERE node_name != $nodes', []);
  assert.equal(leaves.length, 1);
  assert.match(leaves[0].reason, /no frozen definition supplied/);
});

test('a resolved multi-value variable expands to its complete selected list', () => {
  const leaves = suppressionLeaves('SELECT * FROM t WHERE node_name NOT IN ($nodes)', [
    { name: 'nodes', type: 'custom', values: ['a', 'b', 'c'], multi: true },
  ]);
  assert.deepEqual(leaves[0].values, ['a', 'b', 'c']);
});

// ------------------------------------------------------------ LIKE semantics

test('LIKE wildcards translate to anchored patterns', () => {
  assert.equal(likeToRegExp('%test%').test('this-is-a-test-node'), true);
  assert.equal(likeToRegExp('test%').test('node-test'), false);
  assert.equal(likeToRegExp('test_').test('tests'), true);
  assert.equal(likeToRegExp('a.b').test('axb'), false, 'a dot must be literal, not any-char');
});

test('matching is case-sensitive, which can only narrow the suppression set', () => {
  assert.equal(likeToRegExp('%TEST%').test('a-test-node'), false);
});

// ------------------------------------------------------------- row exclusion

const rows = [
  v1Row({ key_field: 'k1', node_name: 'legacy-heartbeat-node' }),
  v1Row({ key_field: 'k2', node_name: 'real-node-1' }),
  v1Row({ key_field: 'k3', node_name: 'real-node-2' }),
  v1Row({ key_field: 'k4', node_name: null, message: 'a real failure' }),
];

test('a suppression leaf excludes exactly the rows the team named', () => {
  const result = evaluateSuppression(rows, [
    panel("SELECT * FROM t WHERE node_name != 'legacy-heartbeat-node'"),
  ]);
  assert.equal(result.suppressedRows.size, 1);
  assert.equal([...result.suppressedRows][0].nodeName, 'legacy-heartbeat-node');
});

test('a row whose field is NULL is not suppressed by a negation on that field', () => {
  // Strict SQL three-valued logic would filter it out, but that is a NULL artefact and
  // not the team's admission that the alert is worthless.
  const result = evaluateSuppression(rows, [
    panel("SELECT * FROM t WHERE node_name != 'legacy-heartbeat-node'"),
  ]);
  assert.equal(
    [...result.suppressedRows].some((r) => r.nodeName === null),
    false,
  );
});

test('rows are counted once regardless of how many leaves exclude them', () => {
  const result = evaluateSuppression(rows, [
    panel(
      "SELECT * FROM t WHERE node_name != 'legacy-heartbeat-node' AND node_name NOT IN ('legacy-heartbeat-node')",
    ),
  ]);
  assert.equal(result.suppressedRows.size, 1);
});

// -------------------------------------------------------- multi-panel rules

test('a row hidden by every panel is suppressed', () => {
  const result = evaluateSuppression(rows, [
    {
      panel_id: 'a',
      schema: 'v1',
      sql: "SELECT * FROM t WHERE node_name != 'legacy-heartbeat-node'",
    },
    {
      panel_id: 'b',
      schema: 'v1',
      sql: "SELECT * FROM t WHERE node_name NOT IN ('legacy-heartbeat-node')",
    },
  ]);
  assert.equal(result.suppressedRows.size, 1);
});

test('a row visible in one panel is not suppressed, even if another hides it', () => {
  const result = evaluateSuppression(rows, [
    {
      panel_id: 'a',
      schema: 'v1',
      sql: "SELECT * FROM t WHERE node_name != 'legacy-heartbeat-node'",
    },
    {
      panel_id: 'b',
      schema: /** @type {const} */ ('v1'),
      sql: "SELECT * FROM t WHERE severity = 'error'",
    },
  ]);
  assert.equal(result.suppressedRows.size, 0);
});

test('an unparseable panel prevents unanimity rather than being skipped', () => {
  const result = evaluateSuppression(rows, [
    {
      panel_id: 'a',
      schema: 'v1',
      sql: "SELECT * FROM t WHERE node_name != 'legacy-heartbeat-node'",
    },
    {
      panel_id: 'b',
      schema: /** @type {const} */ ('v1'),
      sql: 'SELECT * FROM t WHERE node_name !=',
    },
  ]);
  assert.equal(result.suppressedRows.size, 0);
  assert.ok(result.unmeasuredLeaves >= 1);
});

test('a team with no panels has no rule-5 findings and the run completes', () => {
  const result = evaluateSuppression(rows, []);
  assert.equal(result.suppressedRows.size, 0);
  assert.equal(result.unmeasuredLeaves, 0);
});

// --------------------------------------------------------- blast-radius guard

test('a leaf reaching more than half the owned rows is unmeasured, not applied', () => {
  const result = evaluateSuppression(rows, [
    // Excludes 3 of 4 rows: the $__all failure mode.
    panel(
      "SELECT * FROM t WHERE node_name NOT IN ('legacy-heartbeat-node','real-node-1','real-node-2')",
    ),
  ]);
  assert.equal(result.suppressedRows.size, 0);
  assert.equal(result.unmeasuredLeaves, 1);
  assert.match(result.interpretations[0].leaves[0].reason, /blast radius 3\/4/);
});

test('a leaf at exactly the 50% limit still applies', () => {
  const result = evaluateSuppression(rows, [
    panel("SELECT * FROM t WHERE node_name NOT IN ('legacy-heartbeat-node','real-node-1')"),
  ]);
  assert.equal(result.suppressedRows.size, 2);
  assert.equal(result.unmeasuredLeaves, 0);
  assert.equal(BLAST_RADIUS_LIMIT, 0.5);
});

test('an all-selected variable is caught by the blast-radius guard, not applied', () => {
  const result = evaluateSuppression(rows, [
    panel('SELECT * FROM t WHERE node_name NOT IN ($nodes)', [
      {
        name: 'nodes',
        type: 'custom',
        values: ['legacy-heartbeat-node', 'real-node-1', 'real-node-2'],
        multi: true,
        all_selected: true,
      },
    ]),
  ]);
  assert.equal(result.suppressedRows.size, 0);
  assert.equal(result.unmeasuredLeaves, 1);
});

// -------------------------------------------------------------- R5 findings

test('suppressed rows become core R5 findings naming the panels', () => {
  const panels = [panel("SELECT * FROM t WHERE node_name != 'legacy-heartbeat-node'")];
  const { suppressedRows } = evaluateSuppression(rows, panels);
  const findings = buildR5Findings(suppressedRows, panels);
  assert.equal(findings.size, 1);
  const finding = [...findings.values()][0];
  assert.equal(finding.ruleId, 'R5');
  assert.equal(finding.set, 'core');
  assert.deepEqual(finding.evidence.panels, ['p1']);
});

test('the interpretation is keyed by SQL-text hash and parser version', () => {
  const a = interpretPanel(panel("SELECT * FROM t WHERE node_name != 'x'"));
  const b = interpretPanel(panel("SELECT * FROM t WHERE node_name != 'x'"));
  const c = interpretPanel(panel("SELECT * FROM t WHERE node_name != 'y'"));
  assert.equal(a.sqlTextHash, b.sqlTextHash);
  assert.notEqual(a.sqlTextHash, c.sqlTextHash);
  assert.equal(a.parserVersion, '1.0.0');
});

test('the real registry panels all interpret without a parse failure', async () => {
  const { loadRegistry } = await import('../../src/registry/registry.js');
  for (const team of loadRegistry().registry.teams) {
    for (const p of team.panels || []) {
      const interpretation = interpretPanel(p);
      assert.equal(
        interpretation.safetyState,
        'parsed',
        `${p.panel_id}: ${interpretation.unmeasuredReason}`,
      );
    }
  }
});
