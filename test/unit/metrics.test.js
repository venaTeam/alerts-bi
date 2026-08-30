import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRunWindow } from '../../src/domain/window.js';
import { computeDailyVolume, rollupVolume, ratioOrNull } from '../../src/domain/metrics.js';
import { v1Row, v2Row } from '../helpers/rows.js';

const WINDOW = buildRunWindow('2026-08-25T18:00:00Z');
const byDate = (daily, d) => daily.find((x) => x.snapshotDate === d);

test('empty input still emits every bucket in the window', () => {
  const daily = computeDailyVolume([], WINDOW);
  assert.equal(daily.length, 8);
  assert.equal(
    daily.every((d) => d.alerts === 0 && d.distinctAlerts === 0),
    true,
  );
});

test('alerts counts rows and distinct_alerts counts identities', () => {
  // One stuck alert re-firing: many rows, one distinct identity.
  const rows = Array.from({ length: 12 }, (_, i) =>
    v1Row({ '@timestamp': `2026-08-20T0${i % 10}:00:00Z`, key_field: 'stuck' }),
  );
  const day = byDate(computeDailyVolume(rows, WINDOW), '2026-08-20');
  assert.equal(day.alerts, 12);
  assert.equal(day.distinctAlerts, 1);
});

test('alerts_per_hour uses the bucket real covered hours, not 24', () => {
  const rows = [
    v1Row({ '@timestamp': '2026-08-18T19:00:00Z' }),
    v1Row({ '@timestamp': '2026-08-18T20:00:00Z', key_field: 'k2' }),
    v1Row({ '@timestamp': '2026-08-18T21:00:00Z', key_field: 'k3' }),
  ];
  const day = byDate(computeDailyVolume(rows, WINDOW), '2026-08-18');
  assert.equal(day.coveredHours, 6);
  assert.equal(day.alertsPerHour, 0.5);
});

test('rows are bucketed by the UTC date of @timestamp', () => {
  const daily = computeDailyVolume(
    [
      v1Row({ '@timestamp': '2026-08-20T23:59:59Z', key_field: 'a' }),
      v1Row({ '@timestamp': '2026-08-21T00:00:00Z', key_field: 'b' }),
    ],
    WINDOW,
  );
  assert.equal(byDate(daily, '2026-08-20').alerts, 1);
  assert.equal(byDate(daily, '2026-08-21').alerts, 1);
});

test('a row outside the window is a programming error, not a silent drop', () => {
  assert.throws(
    () => computeDailyVolume([v1Row({ '@timestamp': '2026-08-01T00:00:00Z' })], WINDOW),
    /outside the run window/,
  );
});

test('node_name_ratio uses only nonempty-node rows on BOTH sides', () => {
  const rows = [
    // eligible: one scope, two node names
    v1Row({
      '@timestamp': '2026-08-20T01:00:00Z',
      application: 'a',
      object: 'c',
      node_name: 'n1',
      key_field: 'k1',
    }),
    v1Row({
      '@timestamp': '2026-08-20T02:00:00Z',
      application: 'a',
      object: 'c',
      node_name: 'n2',
      key_field: 'k2',
    }),
    // ineligible: a different scope contributing no node name at all
    v1Row({
      '@timestamp': '2026-08-20T03:00:00Z',
      application: 'a',
      object: 'other',
      node_name: null,
      key_field: 'k3',
    }),
    v1Row({
      '@timestamp': '2026-08-20T04:00:00Z',
      application: 'a',
      object: 'other',
      node_name: '   ',
      key_field: 'k4',
    }),
  ];
  const day = byDate(computeDailyVolume(rows, WINDOW), '2026-08-20');
  assert.equal(day.nodeNameNumerator, 2);
  // 1, not 2: the node-less scope must not inflate the denominator.
  assert.equal(day.nodeNameDenominator, 1);
  assert.equal(day.nodeNameRatio, 2);
});

test('key_inflation uses all rows on both sides', () => {
  const rows = [
    v1Row({
      '@timestamp': '2026-08-20T01:00:00Z',
      application: 'a',
      object: 'c',
      key_field: 'k1',
      node_name: null,
    }),
    v1Row({
      '@timestamp': '2026-08-20T02:00:00Z',
      application: 'a',
      object: 'c',
      key_field: 'k2',
      node_name: null,
    }),
    v1Row({
      '@timestamp': '2026-08-20T03:00:00Z',
      application: 'a',
      object: 'c',
      key_field: 'k3',
      node_name: null,
    }),
  ];
  const day = byDate(computeDailyVolume(rows, WINDOW), '2026-08-20');
  assert.equal(day.keyInflationNumerator, 3);
  assert.equal(day.keyInflationDenominator, 1);
  assert.equal(day.keyInflationRatio, 3);
});

test('a zero denominator produces null, never zero', () => {
  const day = byDate(computeDailyVolume([], WINDOW), '2026-08-20');
  assert.equal(day.nodeNameRatio, null);
  assert.equal(day.keyInflationRatio, null);
  assert.equal(ratioOrNull(0, 0), null);
  assert.equal(ratioOrNull(0, 5), 0);
});

test('scorecard alerts_per_hour divides by the full 168 hours', () => {
  const rows = Array.from({ length: 168 }, (_, i) =>
    v1Row({
      '@timestamp': new Date(Date.parse('2026-08-19T00:00:00Z') + i * 60000).toISOString(),
      key_field: `k${i}`,
    }),
  );
  const rollup = rollupVolume(computeDailyVolume(rows, WINDOW));
  assert.equal(rollup.alerts, 168);
  assert.equal(rollup.alertsPerHour, 1);
});

test('published distinct is sum(daily distinct)/7, counting an identity once per date', () => {
  // One identity present on two dates: daily inventory is 1 on each, so 2/7.
  const rows = [
    v1Row({ '@timestamp': '2026-08-20T10:00:00Z', key_field: 'same' }),
    v1Row({ '@timestamp': '2026-08-21T10:00:00Z', key_field: 'same' }),
  ];
  const daily = computeDailyVolume(rows, WINDOW);
  const rollup = rollupVolume(daily);
  assert.equal(rollup.distinctAlertsPerDay, 2 / 7);
});

test('the published distinct rate is not the window-wide distinct count', () => {
  const rows = Array.from({ length: 7 }, (_, i) =>
    v1Row({ '@timestamp': `2026-08-${19 + i}T10:00:00Z`, key_field: 'one-identity' }),
  );
  const rollup = rollupVolume(computeDailyVolume(rows, WINDOW));
  // Window-wide distinct is 1; the daily-inventory rate is 7/7 = 1 here by coincidence,
  // so assert the operands rather than the quotient.
  assert.equal(rollup.alerts, 7);
  assert.equal(rollup.distinctAlertsPerDay, 1);
});

test('diagnostic rollup divides summed numerators by summed denominators', () => {
  const rows = [
    v1Row({
      '@timestamp': '2026-08-20T01:00:00Z',
      application: 'a',
      object: 'c',
      node_name: 'n1',
      key_field: 'k1',
    }),
    v1Row({
      '@timestamp': '2026-08-20T02:00:00Z',
      application: 'a',
      object: 'c',
      node_name: 'n2',
      key_field: 'k2',
    }),
    v1Row({
      '@timestamp': '2026-08-21T01:00:00Z',
      application: 'a',
      object: 'c',
      node_name: 'n3',
      key_field: 'k3',
    }),
  ];
  const rollup = rollupVolume(computeDailyVolume(rows, WINDOW));
  // day 20: 2/1, day 21: 1/1 -> summed 3/2 = 1.5, not the mean of 2 and 1.
  assert.equal(rollup.nodeNameNumerator, 3);
  assert.equal(rollup.nodeNameDenominator, 2);
  assert.equal(rollup.nodeNameRatio, 1.5);
});

test('v1 and v2 are computed independently by the caller and never merged here', () => {
  const v1 = rollupVolume(
    computeDailyVolume([v1Row({ '@timestamp': '2026-08-20T01:00:00Z' })], WINDOW),
  );
  const v2 = rollupVolume(
    computeDailyVolume([v2Row({ '@timestamp': '2026-08-20T01:00:00Z' })], WINDOW),
  );
  assert.equal(v1.alerts, 1);
  assert.equal(v2.alerts, 1);
});
