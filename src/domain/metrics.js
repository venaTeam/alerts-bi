import { WINDOW_HOURS } from './window.js';

/**
 * Volume and diagnostic metrics (design section 3.3 and 3.7; flow step 3).
 *
 * Two counts always travel together: `alerts` (raw rows — pipeline load) and
 * `distinct_alerts` (distinct `application + key_field` — how many things actually fired).
 * One stuck v1 alert is 288 rows a day and one distinct alert, and a team needs the first
 * number to care and the second to act.
 *
 * Every distinct figure is published as a daily rate, never as a window total: a 7-day
 * total is 7x a 1-day total for arithmetic reasons alone.
 */

/** Days in the reporting window; the divisor for the published distinct daily rate. */
export const WINDOW_DAYS = 7;

/**
 * @typedef {import('./normalize.js').AlertRecord} AlertRecord
 * @typedef {import('./window.js').DailyBucket} DailyBucket
 * @typedef {import('./window.js').RunWindow} RunWindow
 */

/**
 * @typedef {object} DailyVolume
 * @property {string} snapshotDate
 * @property {Date} bucketStart
 * @property {Date} bucketEnd
 * @property {number} coveredHours
 * @property {number} alerts
 * @property {number} distinctAlerts
 * @property {number} alertsPerHour
 * @property {number} nodeNameNumerator
 * @property {number} nodeNameDenominator
 * @property {number|null} nodeNameRatio
 * @property {number} keyInflationNumerator
 * @property {number} keyInflationDenominator
 * @property {number|null} keyInflationRatio
 */

/**
 * @typedef {object} VolumeRollup
 * @property {number} alerts Sum of bucket row counts.
 * @property {number} alertsPerHour Rows divided by the full 168 hours.
 * @property {number} distinctAlertsPerDay `sum(daily distinct) / 7`.
 * @property {number|null} nodeNameRatio `sum(numerators) / sum(denominators)`.
 * @property {number|null} keyInflationRatio `sum(numerators) / sum(denominators)`.
 * @property {number} nodeNameNumerator
 * @property {number} nodeNameDenominator
 * @property {number} keyInflationNumerator
 * @property {number} keyInflationDenominator
 */

/**
 * Length-prefixed tuple key, so ('a','bc') and ('ab','c') never collide.
 * @param {...(string|null|undefined)} parts
 * @returns {string}
 */
function tupleKey(...parts) {
  return parts.map((p) => `${(p ?? '').length}:${p ?? ''}`).join('|');
}

/**
 * A `node_name` participates in the node diagnostic only when it is present and not
 * whitespace-only. Design section 3.7: only nonempty-node rows are used, on *both* sides
 * of the ratio, otherwise the denominator would silently include scopes that could never
 * contribute a node.
 *
 * @param {AlertRecord} row
 * @returns {boolean}
 */
function hasNodeName(row) {
  return typeof row.nodeName === 'string' && row.nodeName.trim() !== '';
}

/**
 * A zero denominator yields `null`, never zero: "no eligible rows" and "a ratio of zero"
 * are different statements and must not be conflated in the report.
 *
 * @param {number} numerator
 * @param {number} denominator
 * @returns {number|null}
 */
export function ratioOrNull(numerator, denominator) {
  return denominator === 0 ? null : numerator / denominator;
}

/**
 * Compute one schema's daily volume rows over the run window.
 *
 * Buckets with no rows are still emitted: a day a team fired nothing is a real
 * observation about that week, and dropping it would make the report's daily breakdown
 * depend on activity.
 *
 * @param {AlertRecord[]} rows Rows of a single schema, all inside the window.
 * @param {RunWindow} window
 * @returns {DailyVolume[]}
 */
export function computeDailyVolume(rows, window) {
  /** @type {Map<string, AlertRecord[]>} */
  const byDate = new Map();
  for (const b of window.buckets) byDate.set(b.snapshotDate, []);
  for (const row of rows) {
    const bucket = byDate.get(row.snapshotDate);
    if (!bucket) {
      throw new RangeError(
        `row at ${row.timestamp.toISOString()} falls outside the run window buckets`,
      );
    }
    bucket.push(row);
  }

  return window.buckets.map((bucket) => {
    const dayRows = byDate.get(bucket.snapshotDate) || [];

    /** distinct application+key_field — the bucket's distinct alert inventory */
    const identities = new Set();
    /** distinct (application, component) over ALL rows — key-inflation denominator */
    const allScopes = new Set();
    /** distinct (application, component, node_name) over nonempty-node rows */
    const nodeTuples = new Set();
    /** distinct (application, component) over those same nonempty-node rows */
    const nodeScopes = new Set();

    for (const row of dayRows) {
      identities.add(row.identity);
      const scope = tupleKey(row.application, row.component);
      allScopes.add(scope);
      if (hasNodeName(row)) {
        nodeTuples.add(tupleKey(row.application, row.component, row.nodeName));
        nodeScopes.add(scope);
      }
    }

    const alerts = dayRows.length;
    const nodeNameNumerator = nodeTuples.size;
    const nodeNameDenominator = nodeScopes.size;
    const keyInflationNumerator = identities.size;
    const keyInflationDenominator = allScopes.size;

    return {
      snapshotDate: bucket.snapshotDate,
      bucketStart: bucket.bucketStart,
      bucketEnd: bucket.bucketEnd,
      coveredHours: bucket.coveredHours,
      alerts,
      distinctAlerts: identities.size,
      alertsPerHour: alerts / bucket.coveredHours,
      nodeNameNumerator,
      nodeNameDenominator,
      nodeNameRatio: ratioOrNull(nodeNameNumerator, nodeNameDenominator),
      keyInflationNumerator,
      keyInflationDenominator,
      keyInflationRatio: ratioOrNull(keyInflationNumerator, keyInflationDenominator),
    };
  });
}

/**
 * Roll daily rows up to the 168-hour scorecard figures.
 *
 * `distinctAlertsPerDay` is `sum(daily distinct) / 7` and NOT the distinct identity count
 * across the whole window: this measures average daily inventory, so an identity present
 * on two dates contributes once to each. The complete-window distinct count is still used
 * internally for deduplication, but it is not the published headline.
 *
 * Diagnostics divide summed numerators by summed denominators rather than averaging
 * already-rounded daily ratios.
 *
 * @param {DailyVolume[]} daily
 * @returns {VolumeRollup}
 */
export function rollupVolume(daily) {
  let alerts = 0;
  let distinctSum = 0;
  let nodeNum = 0;
  let nodeDen = 0;
  let keyNum = 0;
  let keyDen = 0;

  for (const d of daily) {
    alerts += d.alerts;
    distinctSum += d.distinctAlerts;
    nodeNum += d.nodeNameNumerator;
    nodeDen += d.nodeNameDenominator;
    keyNum += d.keyInflationNumerator;
    keyDen += d.keyInflationDenominator;
  }

  return {
    alerts,
    alertsPerHour: alerts / WINDOW_HOURS,
    distinctAlertsPerDay: distinctSum / WINDOW_DAYS,
    nodeNameNumerator: nodeNum,
    nodeNameDenominator: nodeDen,
    nodeNameRatio: ratioOrNull(nodeNum, nodeDen),
    keyInflationNumerator: keyNum,
    keyInflationDenominator: keyDen,
    keyInflationRatio: ratioOrNull(keyNum, keyDen),
  };
}
