import test from 'node:test';
import assert from 'node:assert/strict';
import {
  identityOf,
  splitIdentity,
  normalizeRow,
  selectRepresentative,
  groupByIdentity,
} from '../../src/domain/normalize.js';
import { v1Row, v2Row } from '../helpers/rows.js';

test('identity is application + key_field', () => {
  const row = v1Row({ application: 'pay', key_field: 'k1' });
  assert.equal(row.identity, identityOf('pay', 'k1'));
});

test('identity encoding cannot collide across a shifted boundary', () => {
  assert.notEqual(identityOf('ab', 'c'), identityOf('a', 'bc'));
  assert.notEqual(identityOf('a|b', 'c'), identityOf('a', 'b|c'));
});

test('identity round-trips back to its parts', () => {
  for (const [app, key] of [
    ['pay', 'k1'],
    ['a|b', 'c:d'],
    ['', ''],
    ['app:with:colons', '12:34'],
  ]) {
    const { application, keyField } = splitIdentity(identityOf(app, key));
    assert.equal(application, app);
    assert.equal(keyField, key);
  }
});

test('v1 maps object onto component and carries no v2-only fields', () => {
  const row = v1Row({ object: 'settlement-queue' });
  assert.equal(row.schema, 'v1');
  assert.equal(row.component, 'settlement-queue');
  assert.equal(row.status, null);
  assert.equal(row.impact, null);
  assert.equal(row.environment, null);
});

test('v2 maps component and keeps its own fields', () => {
  const row = v2Row({ component: 'edge-handler', environment: 'integration' });
  assert.equal(row.schema, 'v2');
  assert.equal(row.component, 'edge-handler');
  assert.equal(row.status, 'firing');
  assert.equal(row.environment, 'integration');
});

test('impact and runbook_url keep their raw type so R8/R9 can see a non-string', () => {
  const row = v2Row({ impact: 42, runbook_url: { nested: true } });
  assert.equal(row.impact, 42);
  assert.deepEqual(row.runbookUrl, { nested: true });
});

test('the complete source document is retained', () => {
  const row = v2Row({ site: 'dc-1' });
  assert.equal(row.source.site, 'dc-1');
  assert.equal(row.source['@timestamp'], '2026-08-20T12:00:00.000Z');
});

test('docHash is stable across key order and changes with content', () => {
  const a = normalizeRow('v1', {
    application: 'x',
    key_field: 'k',
    '@timestamp': '2026-08-20T00:00:00Z',
    message: 'm',
  });
  const b = normalizeRow('v1', {
    message: 'm',
    '@timestamp': '2026-08-20T00:00:00Z',
    key_field: 'k',
    application: 'x',
  });
  const c = normalizeRow('v1', {
    application: 'x',
    key_field: 'k',
    '@timestamp': '2026-08-20T00:00:00Z',
    message: 'n',
  });
  assert.equal(a.docHash, b.docHash);
  assert.notEqual(a.docHash, c.docHash);
});

test('snapshotDate is the UTC date of @timestamp', () => {
  assert.equal(v1Row({ '@timestamp': '2026-08-20T23:59:59.999Z' }).snapshotDate, '2026-08-20');
  assert.equal(v1Row({ '@timestamp': '2026-08-21T00:00:00.000Z' }).snapshotDate, '2026-08-21');
});

test('an unusable @timestamp is rejected', () => {
  assert.throws(
    () => normalizeRow('v1', { application: 'a', key_field: 'k', '@timestamp': 'nope' }),
    /unusable @timestamp/,
  );
});

test('the representative is the most recent row in the window', () => {
  const rows = [
    v1Row({ '@timestamp': '2026-08-20T10:00:00Z', message: 'older' }),
    v1Row({ '@timestamp': '2026-08-20T12:00:00Z', message: 'newest' }),
    v1Row({ '@timestamp': '2026-08-20T11:00:00Z', message: 'middle' }),
  ];
  assert.equal(selectRepresentative(rows).message, 'newest');
});

test('representative selection is deterministic when timestamps tie', () => {
  const a = v1Row({ '@timestamp': '2026-08-20T12:00:00Z', message: 'variant-a' });
  const b = v1Row({ '@timestamp': '2026-08-20T12:00:00Z', message: 'variant-b' });
  const forward = selectRepresentative([a, b]);
  const reversed = selectRepresentative([b, a]);
  assert.equal(forward.docHash, reversed.docHash);
});

test('groupByIdentity separates identities and keeps input order', () => {
  const rows = [
    v1Row({ key_field: 'k1', message: 'first' }),
    v1Row({ key_field: 'k2' }),
    v1Row({ key_field: 'k1', message: 'second' }),
  ];
  const groups = groupByIdentity(rows);
  assert.equal(groups.size, 2);
  assert.deepEqual(
    groups.get(identityOf('app-1', 'k1')).map((r) => r.message),
    ['first', 'second'],
  );
});

test('the same key_field under two applications is two identities', () => {
  const groups = groupByIdentity([
    v1Row({ application: 'app-a', key_field: 'shared' }),
    v1Row({ application: 'app-b', key_field: 'shared' }),
  ]);
  assert.equal(groups.size, 2);
});
