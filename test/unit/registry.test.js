import test from 'node:test';
import assert from 'node:assert/strict';
import {
  validateRegistry,
  loadRegistry,
  selectTeam,
  snapshotTeamEntry,
  RegistryError,
  DEFAULT_REGISTRY_PATH,
} from '../../src/registry/registry.js';
import { sha256Text } from '../../src/util/hash.js';
import { readFileSync } from 'node:fs';

/** @returns {any} a minimal valid registry document */
function base() {
  return {
    registry_version: '1.0.0',
    teams: [
      { team_id: 'team-a', display_name: 'Team A', v1_operators: ['op-a'], v2_operator: null },
    ],
  };
}

test('accepts a minimal valid registry', () => {
  const r = validateRegistry(base());
  assert.equal(r.teams.length, 1);
});

test('rejects a document that is not schema-valid', () => {
  const doc = base();
  delete doc.teams[0].display_name;
  assert.throws(() => validateRegistry(doc), RegistryError);
});

test('rejects unknown properties so a typo is never silently ignored', () => {
  const doc = base();
  doc.teams[0].v1_operator = ['typo-singular'];
  assert.throws(() => validateRegistry(doc), /additionalProperties|must NOT have additional/);
});

test('requires at least one source operator', () => {
  const doc = base();
  doc.teams[0].v1_operators = [];
  doc.teams[0].v2_operator = null;
  assert.throws(() => validateRegistry(doc), /no source operator/);
});

test('allows an empty v1 list when a v2 operator exists (migrated team)', () => {
  const doc = base();
  doc.teams[0].v1_operators = [];
  doc.teams[0].v2_operator = 'team-a';
  assert.doesNotThrow(() => validateRegistry(doc));
});

test('allows a null v2 operator when v1 operators exist (pre-migration team)', () => {
  assert.doesNotThrow(() => validateRegistry(base()));
});

test('rejects the same operator assigned to two teams', () => {
  const doc = base();
  doc.teams.push({
    team_id: 'team-b',
    display_name: 'Team B',
    v1_operators: ['op-a'],
    v2_operator: null,
  });
  assert.throws(() => validateRegistry(doc), /claimed by both/);
});

test('operator uniqueness is case-sensitive: case variants are distinct values', () => {
  const doc = base();
  doc.teams.push({
    team_id: 'team-b',
    display_name: 'Team B',
    v1_operators: ['OP-A'],
    v2_operator: null,
  });
  assert.doesNotThrow(() => validateRegistry(doc));
});

test('one team may carry the same value as v1 operator and v2 operator', () => {
  const doc = base();
  doc.teams[0].v2_operator = 'op-a';
  assert.doesNotThrow(() => validateRegistry(doc));
});

test('rejects a v2 operator colliding with another team v1 operator', () => {
  const doc = base();
  doc.teams.push({
    team_id: 'team-b',
    display_name: 'Team B',
    v1_operators: [],
    v2_operator: 'op-a',
  });
  assert.throws(() => validateRegistry(doc), /claimed by both/);
});

test('rejects duplicate team ids', () => {
  const doc = base();
  doc.teams.push({
    team_id: 'team-a',
    display_name: 'Team A again',
    v1_operators: ['op-z'],
    v2_operator: null,
  });
  assert.throws(() => validateRegistry(doc), /duplicate team_id/);
});

test('rejects duplicate operator values inside one v1 list', () => {
  const doc = base();
  doc.teams[0].v1_operators = ['op-a', 'op-a'];
  assert.throws(() => validateRegistry(doc), RegistryError);
});

test('rejects a constant variable with no value', () => {
  const doc = base();
  doc.teams[0].panels = [
    {
      panel_id: 'p1',
      schema: 'v1',
      sql: 'SELECT * FROM t WHERE operator = $op',
      variables: [{ name: 'op', type: 'constant' }],
    },
  ];
  assert.throws(() => validateRegistry(doc), /is constant but has no "value"/);
});

test('rejects a custom variable with no values list', () => {
  const doc = base();
  doc.teams[0].panels = [
    {
      panel_id: 'p1',
      schema: 'v1',
      sql: 'SELECT * FROM t WHERE node_name != $nodes',
      variables: [{ name: 'nodes', type: 'custom' }],
    },
  ];
  assert.throws(() => validateRegistry(doc), /is custom but has no "values"/);
});

test('accepts a query variable with no value (it is never executed)', () => {
  const doc = base();
  doc.teams[0].panels = [
    {
      panel_id: 'p1',
      schema: 'v1',
      sql: 'SELECT * FROM t WHERE node_name != $nodes',
      variables: [{ name: 'nodes', type: 'query' }],
    },
  ];
  assert.doesNotThrow(() => validateRegistry(doc));
});

test('rejects duplicate panel ids within a team', () => {
  const doc = base();
  doc.teams[0].panels = [
    { panel_id: 'p1', schema: 'v1', sql: 'SELECT 1' },
    { panel_id: 'p1', schema: 'v1', sql: 'SELECT 2' },
  ];
  assert.throws(() => validateRegistry(doc), /duplicate panel_id/);
});

test('the checked-in registry loads, and its hash covers the complete file', () => {
  const loaded = loadRegistry();
  assert.equal(loaded.registryVersion, loaded.registry.registry_version);
  assert.equal(loaded.fileSha256, sha256Text(readFileSync(DEFAULT_REGISTRY_PATH, 'utf8')));
  assert.equal(loaded.fileSha256.length, 64);
});

test('selectTeam returns exactly one entry and never defaults', () => {
  const loaded = loadRegistry();
  const team = selectTeam(loaded, 'checkout-api');
  assert.equal(team.team_id, 'checkout-api');
  assert.deepEqual(team.v1_operators, ['checkout', 'Checkout-API']);
  assert.equal(team.v2_operator, 'checkout-api');
});

test('selectTeam fails loudly on an unknown team and lists the known ones', () => {
  const loaded = loadRegistry();
  // Asserts membership rather than position: the list is sorted, so pinning the first
  // entry would break every time a team is added.
  assert.throws(() => selectTeam(loaded, 'no-such-team'), /is not in the registry/);
  assert.throws(() => selectTeam(loaded, 'no-such-team'), /Known teams: .*checkout-api/);
});

test('the team snapshot is a stable immutable JSON string', () => {
  const loaded = loadRegistry();
  const team = selectTeam(loaded, 'fraud-detection');
  const snap = snapshotTeamEntry(team);
  assert.equal(snapshotTeamEntry(team), snap);
  assert.deepEqual(JSON.parse(snap).v1_operators, ['fraud-detection']);
});

test('a missing registry file fails before any query could run', () => {
  assert.throws(() => loadRegistry('config/does-not-exist.json'), /cannot read registry/);
});
