import { canonicalEncode, sha256Text } from '../util/hash.js';

/**
 * Request payload construction (design section 5.1; flow step 6.2).
 *
 * The payload factors out only what the batch demonstrably shares. Grouping by
 * `alert_rule_url` or `application` does NOT prove that `component`, `severity`,
 * `provider`, `impact` or `runbook_url` are identical, so a field moves into the group
 * header only when its value is equal across every alert in that batch.
 *
 * This is lossless: every full document is reconstructable from header plus row. It is
 * the same content sent once instead of repeatedly, not an extraction that withholds
 * context from the model.
 */

/**
 * @typedef {import('./grouping.js').Batch} Batch
 */

/**
 * @typedef {object} LlmRequest
 * @property {string} batch_id
 * @property {{type: string, value: string}} group
 * @property {string} ruleset_version
 * @property {string} prompt_version
 * @property {Record<string, unknown>} shared_fields
 * @property {Array<{alert_id: string, schema: string, fields: Record<string, unknown>}>} alerts
 */

/**
 * Compute the fields identical across every document in the batch.
 *
 * A key present in some documents and absent in others can never be shared: reconstructing
 * would then invent the key on the documents that lacked it.
 *
 * @param {Array<Record<string, unknown>>} documents
 * @returns {Set<string>}
 */
export function sharedFieldNames(documents) {
  /** @type {Set<string>} */
  const shared = new Set();
  if (documents.length === 0) return shared;

  const [first, ...rest] = documents;
  for (const key of Object.keys(first)) {
    const encoded = canonicalEncode(first[key]);
    const identicalEverywhere = rest.every(
      (doc) =>
        Object.prototype.hasOwnProperty.call(doc, key) && canonicalEncode(doc[key]) === encoded,
    );
    if (identicalEverywhere) shared.add(key);
  }
  return shared;
}

/**
 * Build the logical request object for one batch.
 *
 * `schema` always stays on the alert envelope, never in `shared_fields`, so a reader never
 * has to consult the header to know which schema an alert belongs to.
 *
 * @param {Batch} batch
 * @param {object} versions
 * @param {string} versions.rulesetVersion
 * @param {string} versions.promptVersion
 * @returns {LlmRequest}
 */
export function buildRequest(batch, versions) {
  const documents = batch.alerts.map((a) => a.source);
  const shared = sharedFieldNames(documents);

  /** @type {Record<string, unknown>} */
  const sharedFields = {};
  for (const key of [...shared].sort()) sharedFields[key] = documents[0][key];

  const alerts = batch.alerts.map((alert, i) => {
    /** @type {Record<string, unknown>} */
    const fields = {};
    for (const key of Object.keys(alert.source).sort()) {
      if (shared.has(key)) continue;
      fields[key] = alert.source[key];
    }
    return { alert_id: batch.alertIds[i], schema: alert.schema, fields };
  });

  return {
    batch_id: batch.batchId,
    group: { type: batch.groupType, value: batch.groupValue },
    ruleset_version: versions.rulesetVersion,
    prompt_version: versions.promptVersion,
    shared_fields: sharedFields,
    alerts,
  };
}

/**
 * Reconstruct one alert's complete source document from header plus row.
 *
 * Exported because losslessness is a property that must be asserted, not assumed: the
 * pipeline verifies it before the first attempt, so a factoring bug fails the batch rather
 * than quietly sending the model a document with fields missing.
 *
 * @param {LlmRequest} request
 * @param {{fields: Record<string, unknown>}} alert
 * @returns {Record<string, unknown>}
 */
export function reconstructDocument(request, alert) {
  return { ...request.shared_fields, ...alert.fields };
}

/**
 * Verify that every alert in the request reconstructs to its original document.
 *
 * @param {LlmRequest} request
 * @param {Batch} batch
 * @returns {void}
 * @throws {Error} when factoring lost or altered any field
 */
export function assertLossless(request, batch) {
  request.alerts.forEach((alert, i) => {
    const rebuilt = reconstructDocument(request, alert);
    const original = batch.alerts[i].source;
    if (canonicalEncode(rebuilt) !== canonicalEncode(original)) {
      throw new Error(
        `factored payload is not lossless for alert ${alert.alert_id} in batch ${request.batch_id}`,
      );
    }
  });
}

/**
 * Serialize the request exactly once.
 *
 * The serialized text is persisted before the first attempt and reused BYTE-FOR-BYTE on
 * retries: "retry the identical batch as a unit" is only meaningful if the bytes are the
 * same, and re-serializing per attempt would leave that guarantee resting on object key
 * order.
 *
 * @param {LlmRequest} request
 * @returns {{text: string, hash: string}}
 */
export function serializeRequest(request) {
  const text = JSON.stringify(request);
  return { text, hash: sha256Text(text) };
}
