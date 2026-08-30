import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRunWindow, utcDateKey, isInWindow, WINDOW_HOURS } from '../../src/domain/window.js';

test('window is the exact half-open 168-hour UTC range ending at run_at', () => {
  const w = buildRunWindow('2026-08-25T18:00:00Z');
  assert.equal(w.windowEnd.toISOString(), '2026-08-25T18:00:00.000Z');
  assert.equal(w.windowStart.toISOString(), '2026-08-18T18:00:00.000Z');
  assert.equal((w.windowEnd.getTime() - w.windowStart.getTime()) / 3600000, WINDOW_HOURS);
});

test('a mid-day run touches eight UTC dates with partial first and last buckets', () => {
  const w = buildRunWindow('2026-08-25T18:00:00Z');
  assert.equal(w.buckets.length, 8);
  assert.equal(w.buckets[0].snapshotDate, '2026-08-18');
  assert.equal(w.buckets[0].coveredHours, 6);
  assert.equal(w.buckets[7].snapshotDate, '2026-08-25');
  assert.equal(w.buckets[7].coveredHours, 18);
  for (const b of w.buckets.slice(1, 7)) assert.equal(b.coveredHours, 24);
});

test('covered hours always sum to exactly 168', () => {
  for (const at of [
    '2026-08-25T18:00:00Z',
    '2026-08-25T00:00:00Z',
    '2026-08-25T23:59:59.999Z',
    '2026-01-01T07:13:44.512Z',
    '2026-03-01T12:00:00Z',
  ]) {
    const w = buildRunWindow(at);
    const total = w.buckets.reduce((a, b) => a + b.coveredHours, 0);
    assert.equal(total, WINDOW_HOURS, `total for ${at}`);
  }
});

test('a run at exactly midnight yields seven whole buckets and no empty eighth', () => {
  const w = buildRunWindow('2026-08-25T00:00:00Z');
  assert.equal(w.buckets.length, 7);
  assert.equal(w.buckets[0].snapshotDate, '2026-08-18');
  assert.equal(w.buckets[6].snapshotDate, '2026-08-24');
  for (const b of w.buckets) assert.equal(b.coveredHours, 24);
});

test('buckets are contiguous, ascending and non-overlapping', () => {
  const w = buildRunWindow('2026-08-25T18:00:00Z');
  assert.equal(w.buckets[0].bucketStart.getTime(), w.windowStart.getTime());
  assert.equal(w.buckets[w.buckets.length - 1].bucketEnd.getTime(), w.windowEnd.getTime());
  for (let i = 1; i < w.buckets.length; i++) {
    assert.equal(w.buckets[i].bucketStart.getTime(), w.buckets[i - 1].bucketEnd.getTime());
  }
});

test('the window is inclusive at the start and exclusive at the end', () => {
  const w = buildRunWindow('2026-08-25T18:00:00Z');
  assert.equal(isInWindow(w, w.windowStart), true);
  assert.equal(isInWindow(w, new Date(w.windowStart.getTime() - 1)), false);
  assert.equal(isInWindow(w, new Date(w.windowEnd.getTime() - 1)), true);
  assert.equal(isInWindow(w, w.windowEnd), false);
});

test('window crossing a month boundary buckets by real UTC dates', () => {
  const w = buildRunWindow('2026-03-03T06:00:00Z');
  assert.deepEqual(
    w.buckets.map((b) => b.snapshotDate),
    [
      '2026-02-24',
      '2026-02-25',
      '2026-02-26',
      '2026-02-27',
      '2026-02-28',
      '2026-03-01',
      '2026-03-02',
      '2026-03-03',
    ],
  );
  assert.equal(w.buckets[0].coveredHours, 18);
  assert.equal(w.buckets[7].coveredHours, 6);
});

test('utcDateKey uses UTC, not local time', () => {
  assert.equal(utcDateKey('2026-08-25T23:59:59.999Z'), '2026-08-25');
  assert.equal(utcDateKey('2026-08-26T00:00:00.000Z'), '2026-08-26');
});

test('an invalid run_at fails loudly', () => {
  assert.throws(() => buildRunWindow('not-a-date'), /invalid run_at/);
});
