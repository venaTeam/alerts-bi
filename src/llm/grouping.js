import { sha256Of } from '../util/hash.js';

/**
 * Request grouping and partitioning (design section 5.1; flow step 6.2).
 *
 * Alerts are grouped by `alert_rule_url` where present and by `application` where it is
 * not. Each group is sent independently: groups are NEVER packed together to fill
 * capacity, even when they are small, so a request never mixes alert rules or
 * applications.
 *
 * The bet this rests on: showing a rule's instances together is context a reviewer would
 * want - the model can see whether a message is genuinely per-instance or one generic
 * string repeated across forty nodes. Design section 7.1 records that this is still
 * unvalidated, which is why grouping is isolated here and testable on its own.
 */

/** Hard ceiling from design section 5.1. Configuration may lower it, never raise it. */
export const MAX_BATCH_SIZE = 200;

/**
 * @typedef {import('../domain/normalize.js').AlertRecord} AlertRecord
 */

/**
 * @typedef {object} AlertGroup
 * @property {'alert_rule_url'|'application'} type
 * @property {string} value
 * @property {AlertRecord[]} alerts Sorted deterministically.
 */

/**
 * @typedef {object} Batch
 * @property {string} batchId
 * @property {'alert_rule_url'|'application'} groupType
 * @property {string} groupValue
 * @property {number} partitionIndex
 * @property {number} partitionCount
 * @property {AlertRecord[]} alerts
 * @property {string[]} alertIds Parallel to `alerts`.
 */

/**
 * Transport identifier for one alert.
 *
 * Explicitly NOT a second business identity: it is the SHA-256 of an unambiguous encoding
 * of (schema, application, key_field). The verdict cache stays keyed on
 * (application, key_field, prompt_version, model_version) as decided.
 *
 * @param {AlertRecord} alert
 * @returns {string}
 */
export function alertTransportId(alert) {
  return sha256Of({
    schema: alert.schema,
    application: alert.application,
    key_field: alert.keyField,
  });
}

/**
 * Deterministic batch identifier.
 *
 * @param {object} parts
 * @param {string} parts.runId
 * @param {string} parts.groupType
 * @param {string} parts.groupValue
 * @param {number} parts.partitionIndex
 * @param {string[]} parts.alertIds Ordered exactly as sent.
 * @param {string} parts.promptVersion
 * @param {string} parts.modelVersion
 * @returns {string}
 */
export function batchIdOf(parts) {
  return sha256Of({
    run_id: parts.runId,
    group_type: parts.groupType,
    group_value: parts.groupValue,
    partition_index: parts.partitionIndex,
    alert_ids: parts.alertIds,
    prompt_version: parts.promptVersion,
    model_version: parts.modelVersion,
  });
}

/**
 * Is a value usable as a grouping key?
 * @param {unknown} value
 * @returns {boolean}
 */
function isUsable(value) {
  return typeof value === 'string' && value.trim() !== '';
}

/**
 * Group alerts by rule URL, falling back to application.
 *
 * API alerts do not carry an alert-rule URL at all, which is why the fallback exists and
 * why R4 does not penalise them for the absence.
 *
 * @param {AlertRecord[]} alerts
 * @returns {AlertGroup[]} Ordered by (type, value).
 */
export function groupAlerts(alerts) {
  /** @type {Map<string, AlertGroup>} */
  const groups = new Map();

  for (const alert of alerts) {
    const type = isUsable(alert.alertRuleUrl) ? 'alert_rule_url' : 'application';
    const value =
      type === 'alert_rule_url' ? /** @type {string} */ (alert.alertRuleUrl) : alert.application;
    const key = `${type}${value}`;
    let group = groups.get(key);
    if (!group) {
      group = { type, value, alerts: [] };
      groups.set(key, group);
    }
    group.alerts.push(alert);
  }

  for (const group of groups.values()) {
    // Within a group, alerts are ordered by key_field; application breaks the tie so the
    // order is total even when two applications share a key_field value.
    group.alerts.sort(
      (a, b) =>
        compareStrings(a.keyField, b.keyField) || compareStrings(a.application, b.application),
    );
  }

  return [...groups.values()].sort(
    (a, b) => compareStrings(a.type, b.type) || compareStrings(a.value, b.value),
  );
}

/**
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
function compareStrings(a, b) {
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * Split `n` items into `ceil(n / max)` balanced partitions whose sizes differ by at most
 * one.
 *
 * 401 alerts become 134, 134 and 133 - never 200, 200 and 1. A tiny tail request would
 * lose exactly the same-group context that batching exists to provide.
 *
 * @param {number} n
 * @param {number} max
 * @returns {number[]} Partition sizes, largest first.
 */
export function balancedPartitionSizes(n, max) {
  if (n <= 0) return [];
  const count = Math.ceil(n / max);
  const base = Math.floor(n / count);
  const remainder = n % count;
  return Array.from({ length: count }, (_, i) => (i < remainder ? base + 1 : base));
}

/**
 * Build the batches for one run.
 *
 * @param {AlertRecord[]} alerts Alerts eligible for assessment.
 * @param {object} options
 * @param {string} options.runId
 * @param {string} options.promptVersion
 * @param {string} options.modelVersion
 * @param {number} [options.maxBatchSize]
 * @returns {Batch[]}
 */
export function buildBatches(alerts, options) {
  const maxBatchSize = Math.min(options.maxBatchSize ?? MAX_BATCH_SIZE, MAX_BATCH_SIZE);
  /** @type {Batch[]} */
  const batches = [];

  for (const group of groupAlerts(alerts)) {
    const sizes = balancedPartitionSizes(group.alerts.length, maxBatchSize);
    let offset = 0;
    sizes.forEach((size, partitionIndex) => {
      const slice = group.alerts.slice(offset, offset + size);
      offset += size;
      const alertIds = slice.map((a) => alertTransportId(a));
      batches.push({
        batchId: batchIdOf({
          runId: options.runId,
          groupType: group.type,
          groupValue: group.value,
          partitionIndex,
          alertIds,
          promptVersion: options.promptVersion,
          modelVersion: options.modelVersion,
        }),
        groupType: group.type,
        groupValue: group.value,
        partitionIndex,
        partitionCount: sizes.length,
        alerts: slice,
        alertIds,
      });
    });
  }

  return batches;
}
