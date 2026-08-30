/**
 * Run window and UTC daily bucketing (design section 6; flow steps 1 and 3).
 *
 * `run_at` is captured once. The window is the exact half-open UTC range
 * [run_at - 168h, run_at). A rolling 168-hour range normally touches eight UTC calendar
 * dates, so the first and last buckets are usually partial; each bucket therefore carries
 * its own real boundaries and `covered_hours` rather than being assumed to be a full day.
 */

export const WINDOW_HOURS = 168;
const MS_PER_HOUR = 3600000;
const MS_PER_DAY = 24 * MS_PER_HOUR;

/**
 * @typedef {object} DailyBucket
 * @property {string} snapshotDate UTC calendar date, `YYYY-MM-DD`.
 * @property {Date} bucketStart Inclusive.
 * @property {Date} bucketEnd Exclusive.
 * @property {number} coveredHours Real hours of this date inside the window.
 */

/**
 * @typedef {object} RunWindow
 * @property {Date} runAt
 * @property {Date} windowStart Inclusive.
 * @property {Date} windowEnd Exclusive; equals runAt.
 * @property {DailyBucket[]} buckets Ascending by date.
 */

/**
 * UTC calendar date of an instant, as `YYYY-MM-DD`.
 * @param {Date|number|string} at
 * @returns {string}
 */
export function utcDateKey(at) {
  const d = at instanceof Date ? at : new Date(at);
  return d.toISOString().slice(0, 10);
}

/**
 * Midnight UTC starting the calendar date containing `at`.
 * @param {Date|number} at
 * @returns {number} epoch ms
 */
function utcMidnight(at) {
  const ms = at instanceof Date ? at.getTime() : at;
  return Math.floor(ms / MS_PER_DAY) * MS_PER_DAY;
}

/**
 * Build the run window and its daily buckets.
 *
 * @param {Date|string|number} runAt
 * @returns {RunWindow}
 */
export function buildRunWindow(runAt) {
  const end = runAt instanceof Date ? new Date(runAt.getTime()) : new Date(runAt);
  if (Number.isNaN(end.getTime())) throw new TypeError(`invalid run_at: ${String(runAt)}`);
  const start = new Date(end.getTime() - WINDOW_HOURS * MS_PER_HOUR);

  /** @type {DailyBucket[]} */
  const buckets = [];
  // Walk calendar dates from the one containing window_start up to the one containing the
  // last instant inside the window. window_end is exclusive, so a run at exactly midnight
  // must not open an empty bucket for the date it stops at.
  const lastInstant = end.getTime() - 1;
  for (let day = utcMidnight(start); day <= utcMidnight(lastInstant); day += MS_PER_DAY) {
    const bucketStart = Math.max(start.getTime(), day);
    const bucketEnd = Math.min(end.getTime(), day + MS_PER_DAY);
    if (bucketEnd <= bucketStart) continue;
    buckets.push({
      snapshotDate: utcDateKey(day),
      bucketStart: new Date(bucketStart),
      bucketEnd: new Date(bucketEnd),
      coveredHours: (bucketEnd - bucketStart) / MS_PER_HOUR,
    });
  }

  return { runAt: end, windowStart: start, windowEnd: end, buckets };
}

/**
 * Is an instant inside the half-open window?
 * @param {RunWindow} window
 * @param {Date|number} at
 * @returns {boolean}
 */
export function isInWindow(window, at) {
  const ms = at instanceof Date ? at.getTime() : at;
  return ms >= window.windowStart.getTime() && ms < window.windowEnd.getTime();
}
