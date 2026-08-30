import { normalizeMessage, normalizeFieldValue, isBlank } from './text.js';
import { R1_GENERIC_MESSAGES, R2_HEARTBEAT_MESSAGES, PLACEHOLDER_VALUES } from './catalogs.js';

/**
 * Core deterministic rules (design section 4). Core rules apply to both schemas and are
 * the only findings valid for reading v1 and v2 side by side.
 *
 * Every rule is evaluated on every raw row, returns a finding or null, and carries
 * evidence naming exactly what matched — a team disputing a number must be able to see
 * "rule 2 matched the literal string `i am alive`".
 *
 * R5 (self-suppression) is not here: it comes from the panel-suppression evaluator, which
 * needs the team's panels and the owned row set, not a single row.
 */

/**
 * @typedef {import('../domain/normalize.js').AlertRecord} AlertRecord
 */

/**
 * @typedef {object} Finding
 * @property {string} ruleId
 * @property {'core'|'v2_readiness'} set
 * @property {Record<string, any>} evidence Heterogeneous by rule: each rule names exactly what matched.
 */

/** Milliseconds in the R7 validity interval. */
const TWENTY_FOUR_HOURS_MS = 24 * 3600000;

/**
 * R1 — generic message matching the versioned catalogue.
 *
 * Whole-message equality only. `Disk full` and `OOM` are short but potentially meaningful
 * and continue to the LLM; length is never evidence.
 *
 * @param {AlertRecord} row
 * @returns {Finding|null}
 */
export function evaluateR1(row) {
  const normalized = normalizeMessage(row.message);
  if (normalized === null || !R1_GENERIC_MESSAGES.includes(normalized)) return null;
  return { ruleId: 'R1', set: 'core', evidence: { field: 'message', normalized } };
}

/**
 * R2 — informational / heartbeat message matching the versioned catalogue.
 *
 * Also whole-message: `backup completed with 10 failures` and `service is not healthy`
 * are not heartbeats and continue through the remaining checks.
 *
 * @param {AlertRecord} row
 * @returns {Finding|null}
 */
export function evaluateR2(row) {
  const normalized = normalizeMessage(row.message);
  if (normalized === null || !R2_HEARTBEAT_MESSAGES.includes(normalized)) return null;
  return { ruleId: 'R2', set: 'core', evidence: { field: 'message', normalized } };
}

/**
 * R3 — placeholder or missing required identity/ownership metadata.
 *
 * Required on both schemas: `application`, `operator`, and the schema's component field.
 * `node_name` is optional and is checked only when it is supplied — an absent or empty
 * node name is valid, and flagging it would penalise every alert that legitimately has no
 * node scope.
 *
 * @param {AlertRecord} row
 * @returns {Finding|null}
 */
export function evaluateR3(row) {
  const componentField = row.schema === 'v1' ? 'object' : 'component';
  /** @type {Array<{field: string, value: unknown, required: boolean}>} */
  const checks = [
    { field: 'application', value: row.application, required: true },
    { field: 'operator', value: row.operator, required: true },
    { field: componentField, value: row.component, required: true },
    { field: 'node_name', value: row.nodeName, required: false },
  ];

  /** @type {Array<{field: string, reason: string, normalized?: string}>} */
  const violations = [];
  for (const check of checks) {
    const blank = isBlank(check.value);
    if (blank) {
      // Empty matches only on required fields; an absent optional node_name is valid.
      if (check.required) violations.push({ field: check.field, reason: 'empty' });
      continue;
    }
    const normalized = normalizeFieldValue(check.value);
    if (normalized !== null && PLACEHOLDER_VALUES.includes(normalized)) {
      violations.push({ field: check.field, reason: 'placeholder', normalized });
    }
  }

  if (violations.length === 0) return null;
  return { ruleId: 'R3', set: 'core', evidence: { violations } };
}

/**
 * R4 — a Grafana alert with no alert-rule link.
 *
 * Scoped to `provider = grafana` only. API alerts do not carry `alert_rule_url` at all,
 * so its absence is not evidence against them; they continue to the LLM and fall back to
 * application grouping for batching.
 *
 * @param {AlertRecord} row
 * @returns {Finding|null}
 */
export function evaluateR4(row) {
  const provider = normalizeFieldValue(row.provider);
  if (provider !== 'grafana') return null;
  if (!isBlank(row.alertRuleUrl)) return null;
  return {
    ruleId: 'R4',
    set: 'core',
    evidence: { provider: row.provider, alert_rule_url: row.alertRuleUrl ?? null },
  };
}

/**
 * R7 — invalid v1 `time_created`.
 *
 * `@timestamp` is the receipt time. The valid interval is
 * [`@timestamp` - 24h, `@timestamp`], INCLUSIVE at both ends; a value later than receipt
 * or older than 24 hours at receipt is flagged.
 *
 * v1 requires `time_created`, so the rule checks validity rather than presence — but an
 * absent or unparseable value cannot fall inside the interval and is not valid. That also
 * matches characteristic 7 of the standard, which calls a missing event timestamp a bad
 * alert outright.
 *
 * v2 stamps this field itself, which is why the rule is v1-only by construction.
 *
 * @param {AlertRecord} row
 * @returns {Finding|null}
 */
export function evaluateR7(row) {
  if (row.schema !== 'v1') return null;

  const receipt = row.timestamp.getTime();
  const earliest = receipt - TWENTY_FOUR_HOURS_MS;

  if (isBlank(row.timeCreated)) {
    return {
      ruleId: 'R7',
      set: 'core',
      evidence: { reason: 'missing', time_created: row.timeCreated ?? null },
    };
  }

  const created = Date.parse(/** @type {string} */ (row.timeCreated));
  if (Number.isNaN(created)) {
    return {
      ruleId: 'R7',
      set: 'core',
      evidence: { reason: 'unparseable', time_created: row.timeCreated },
    };
  }
  if (created > receipt) {
    return {
      ruleId: 'R7',
      set: 'core',
      evidence: {
        reason: 'future',
        time_created: row.timeCreated,
        timestamp: row.timestamp.toISOString(),
      },
    };
  }
  if (created < earliest) {
    return {
      ruleId: 'R7',
      set: 'core',
      evidence: {
        reason: 'older_than_24h',
        time_created: row.timeCreated,
        timestamp: row.timestamp.toISOString(),
      },
    };
  }
  return null;
}

/**
 * Evaluate every core rule that a single row can decide on its own.
 *
 * R5 is added afterwards by the suppression evaluator, which is why it is absent here.
 *
 * @param {AlertRecord} row
 * @returns {Finding[]}
 */
export function evaluateCoreRules(row) {
  /** @type {Finding[]} */
  const findings = [];
  for (const rule of [evaluateR1, evaluateR2, evaluateR3, evaluateR4, evaluateR7]) {
    const finding = rule(row);
    if (finding) findings.push(finding);
  }
  return findings;
}
