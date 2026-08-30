import { sha256Of } from '../util/hash.js';
import { utcDateKey } from './window.js';

/**
 * Schema adapters mapping v1 (Appchi) and v2 (Appchi V2) documents onto one internal
 * record, without erasing schema-specific fields (blueprint section 2.4).
 *
 * The complete source document is always retained: it is the representative payload sent
 * to the model and the audit record stored with a verdict, and Elasticsearch row ids are
 * deliberately not used as business identity because source documents expire after three
 * months.
 */

/** Source fields carried by a v1 document, in a fixed order for stable serialization. */
export const V1_FIELDS = [
  'application',
  'object',
  'message',
  'severity',
  'operator',
  'key_field',
  'time_created',
  'node_name',
  'network',
  'alert_rule_url',
  'provider',
  '@timestamp',
];

/** Source fields carried by a v2 document, in a fixed order for stable serialization. */
export const V2_FIELDS = [
  'application',
  'component',
  'message',
  'severity',
  'status',
  'impact',
  'runbook_url',
  'environment',
  'site',
  'operator',
  'key_field',
  'time_created',
  'node_name',
  'network',
  'alert_rule_url',
  'provider',
  '@timestamp',
];

/**
 * @typedef {object} AlertRecord
 * @property {'v1'|'v2'} schema
 * @property {string} application
 * @property {string} keyField
 * @property {string} identity Composite `application + key_field` identity key.
 * @property {string|null} component `object` in v1, `component` in v2.
 * @property {string|null} message
 * @property {string|null} severity
 * @property {string|null} operator
 * @property {string|null} nodeName
 * @property {string|null} network
 * @property {string|null} alertRuleUrl
 * @property {string|null} provider
 * @property {string|null} status v2 only.
 * @property {unknown} impact v2 only; kept unnarrowed so R8 can see a non-string.
 * @property {unknown} runbookUrl v2 only; kept unnarrowed for the same reason.
 * @property {string|null} environment v2 only.
 * @property {string|null} site v2 only.
 * @property {string|null} timeCreated Raw value as supplied.
 * @property {Date} timestamp `@timestamp`, the receipt time.
 * @property {string} snapshotDate UTC calendar date of `@timestamp`.
 * @property {Record<string, unknown>} source Complete source document.
 * @property {string} docHash SHA-256 over the canonical encoding of `source`.
 */

/**
 * Build the composite alert identity.
 *
 * `application + key_field` is the only alert identity in this design (sections 3.7, 5.1).
 * The parts are length-prefixed so no pair of values can produce a colliding key.
 *
 * @param {string} application
 * @param {string} keyField
 * @returns {string}
 */
export function identityOf(application, keyField) {
  const a = String(application ?? '');
  const k = String(keyField ?? '');
  return `${a.length}:${a}|${k.length}:${k}`;
}

/**
 * Split an identity key back into its parts.
 * @param {string} identity
 * @returns {{application: string, keyField: string}}
 */
export function splitIdentity(identity) {
  const firstColon = identity.indexOf(':');
  const appLen = Number(identity.slice(0, firstColon));
  const application = identity.slice(firstColon + 1, firstColon + 1 + appLen);
  const rest = identity.slice(firstColon + 1 + appLen + 1);
  const secondColon = rest.indexOf(':');
  const keyField = rest.slice(secondColon + 1);
  return { application, keyField };
}

/**
 * @param {unknown} v
 * @returns {string|null}
 */
function strOrNull(v) {
  if (v === null || v === undefined) return null;
  return typeof v === 'string' ? v : String(v);
}

/**
 * Adapt one raw Elasticsearch `_source` document.
 *
 * @param {'v1'|'v2'} schema
 * @param {Record<string, unknown>} source
 * @returns {AlertRecord}
 */
export function normalizeRow(schema, source) {
  const application = strOrNull(source.application) ?? '';
  const keyField = strOrNull(source.key_field) ?? '';
  const tsRaw = source['@timestamp'];
  const timestamp = new Date(/** @type {string} */ (tsRaw));
  if (Number.isNaN(timestamp.getTime())) {
    throw new TypeError(`row has an unusable @timestamp: ${JSON.stringify(tsRaw)}`);
  }

  return {
    schema,
    application,
    keyField,
    identity: identityOf(application, keyField),
    component: strOrNull(schema === 'v1' ? source.object : source.component),
    message: strOrNull(source.message),
    severity: strOrNull(source.severity),
    operator: strOrNull(source.operator),
    nodeName: strOrNull(source.node_name),
    network: strOrNull(source.network),
    alertRuleUrl: strOrNull(source.alert_rule_url),
    provider: strOrNull(source.provider),
    status: schema === 'v2' ? strOrNull(source.status) : null,
    // impact and runbook_url stay unnarrowed: R8 and R9 must be able to observe a
    // non-string value, which strOrNull would have hidden by stringifying it.
    impact: schema === 'v2' ? source.impact : null,
    runbookUrl: schema === 'v2' ? source.runbook_url : null,
    environment: schema === 'v2' ? strOrNull(source.environment) : null,
    site: schema === 'v2' ? strOrNull(source.site) : null,
    timeCreated: strOrNull(source.time_created),
    timestamp,
    snapshotDate: utcDateKey(timestamp),
    source,
    docHash: sha256Of(source),
  };
}

/**
 * The representative document for an identity is its most recent row within the run
 * window (design section 5.1: a *defined* representative, so two runs over the same data
 * cannot pick different rows and reach different verdicts).
 *
 * Two rows can share the newest `@timestamp` — a v1 alert re-fired at the same second, or
 * a re-index. The document hash breaks that tie, which keeps selection deterministic
 * without depending on Elasticsearch row order or on ids that expire.
 *
 * @param {AlertRecord[]} rows Non-empty; all rows for one identity.
 * @returns {AlertRecord}
 */
export function selectRepresentative(rows) {
  if (rows.length === 0) throw new RangeError('cannot select a representative from zero rows');
  let best = rows[0];
  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];
    const dt = r.timestamp.getTime() - best.timestamp.getTime();
    if (dt > 0 || (dt === 0 && r.docHash < best.docHash)) best = r;
  }
  return best;
}

/**
 * Group rows by identity, preserving input order within each group.
 * @param {AlertRecord[]} rows
 * @returns {Map<string, AlertRecord[]>}
 */
export function groupByIdentity(rows) {
  /** @type {Map<string, AlertRecord[]>} */
  const map = new Map();
  for (const row of rows) {
    const existing = map.get(row.identity);
    if (existing) existing.push(row);
    else map.set(row.identity, [row]);
  }
  return map;
}
