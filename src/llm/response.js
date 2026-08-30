import { CITABLE_IDS } from '../rules/catalogs.js';

/**
 * Response validation (design section 5.1; flow step 6.3).
 *
 * The contract is exact and CLOSED, and a failing response is never partially accepted.
 * A verdict attached to the wrong alert is the worst output this system can produce: it
 * is a false positive that also destroys the audit trail that would have caught it. So
 * every check below rejects the whole batch rather than dropping one verdict.
 */

export const ASSESSMENTS = Object.freeze(['no_violation', 'catalog_violation', 'other']);
export const CONFIDENCES = Object.freeze(['high', 'medium', 'low']);
export const MAX_JUSTIFICATION_LENGTH = 1000;

const TOP_LEVEL_KEYS = new Set(['batch_id', 'verdicts']);
const VERDICT_KEYS = new Set([
  'alert_id',
  'assessment',
  'principle_id',
  'confidence',
  'justification',
]);

export class LlmResponseError extends Error {
  /** @param {string} message */
  constructor(message) {
    super(message);
    this.name = 'LlmResponseError';
  }
}

/**
 * @typedef {object} Verdict
 * @property {string} alert_id
 * @property {'no_violation'|'catalog_violation'|'other'} assessment
 * @property {string} principle_id
 * @property {'high'|'medium'|'low'} confidence
 * @property {string} justification
 */

/**
 * Validate a parsed response against the request it answers.
 *
 * @param {unknown} parsed
 * @param {{batchId: string, alertIds: string[]}} request
 * @returns {Verdict[]} In request order, so callers never depend on response ordering.
 * @throws {LlmResponseError}
 */
export function validateResponse(parsed, request) {
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new LlmResponseError('response is not a JSON object');
  }
  const body = /** @type {Record<string, unknown>} */ (parsed);

  for (const key of Object.keys(body)) {
    if (!TOP_LEVEL_KEYS.has(key)) {
      throw new LlmResponseError(`response carries unexpected top-level field "${key}"`);
    }
  }
  if (!('batch_id' in body)) throw new LlmResponseError('response is missing batch_id');
  if (!('verdicts' in body)) throw new LlmResponseError('response is missing verdicts');

  if (body.batch_id !== request.batchId) {
    // Never trust a response that answers a different request.
    throw new LlmResponseError('response batch_id does not match the request');
  }
  if (!Array.isArray(body.verdicts)) {
    throw new LlmResponseError('verdicts is not an array');
  }

  /** @type {Map<string, Verdict>} */
  const byAlertId = new Map();
  for (const raw of body.verdicts) {
    const verdict = validateVerdict(raw);
    if (byAlertId.has(verdict.alert_id)) {
      throw new LlmResponseError(`response contains a duplicate verdict for ${verdict.alert_id}`);
    }
    byAlertId.set(verdict.alert_id, verdict);
  }

  const expected = new Set(request.alertIds);
  for (const id of byAlertId.keys()) {
    if (!expected.has(id)) {
      throw new LlmResponseError(`response contains a verdict for unknown alert ${id}`);
    }
  }
  for (const id of expected) {
    if (!byAlertId.has(id)) {
      throw new LlmResponseError(`response is missing a verdict for alert ${id}`);
    }
  }

  // Verdict order is immaterial because ids provide the binding; returning request order
  // keeps every downstream consumer deterministic.
  return request.alertIds.map((id) => /** @type {Verdict} */ (byAlertId.get(id)));
}

/**
 * @param {unknown} raw
 * @returns {Verdict}
 */
function validateVerdict(raw) {
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new LlmResponseError('a verdict is not an object');
  }
  const v = /** @type {Record<string, unknown>} */ (raw);

  for (const key of Object.keys(v)) {
    if (!VERDICT_KEYS.has(key)) {
      throw new LlmResponseError(`verdict carries unexpected field "${key}"`);
    }
  }
  for (const key of VERDICT_KEYS) {
    if (!(key in v)) throw new LlmResponseError(`verdict is missing "${key}"`);
  }

  if (typeof v.alert_id !== 'string' || v.alert_id === '') {
    throw new LlmResponseError('verdict alert_id is not a non-empty string');
  }
  if (typeof v.assessment !== 'string' || !ASSESSMENTS.includes(v.assessment)) {
    throw new LlmResponseError(`verdict assessment ${JSON.stringify(v.assessment)} is not valid`);
  }
  if (typeof v.confidence !== 'string' || !CONFIDENCES.includes(v.confidence)) {
    throw new LlmResponseError(`verdict confidence ${JSON.stringify(v.confidence)} is not valid`);
  }
  if (typeof v.principle_id !== 'string') {
    throw new LlmResponseError('verdict principle_id is not a string');
  }
  if (typeof v.justification !== 'string' || v.justification.trim() === '') {
    throw new LlmResponseError('verdict justification is empty');
  }
  if (v.justification.length > MAX_JUSTIFICATION_LENGTH) {
    throw new LlmResponseError(
      `verdict justification exceeds ${MAX_JUSTIFICATION_LENGTH} characters`,
    );
  }

  // The assessment/principle pairing is closed. `other` is the escape hatch for a clear
  // violation absent from the catalogue - never for uncertainty - and it never counts
  // toward flagged.
  if (v.assessment === 'no_violation' && v.principle_id !== 'NONE') {
    throw new LlmResponseError('no_violation must carry principle_id NONE');
  }
  if (v.assessment === 'other' && v.principle_id !== 'OTHER') {
    throw new LlmResponseError('other must carry principle_id OTHER');
  }
  if (v.assessment === 'catalog_violation' && !CITABLE_IDS.includes(v.principle_id)) {
    throw new LlmResponseError(
      `catalog_violation cites ${JSON.stringify(v.principle_id)}, which is not in the catalogue`,
    );
  }

  return /** @type {Verdict} */ (v);
}

/**
 * Map a validated verdict onto the run-level identity state.
 *
 * A HIGH-confidence catalogue violation becomes `llm_flagged`. Medium and low confidence,
 * and every `other`, go to review. `no_violation` becomes `assessed_good`.
 *
 * Confidence is a three-value enum rather than a number on purpose: models are not
 * calibrated well enough on a 0-1 scale to justify a numeric cutoff, and a float invites a
 * threshold carrying more precision than the judgment underneath it has.
 *
 * @param {Verdict} verdict
 * @returns {'llm_flagged'|'needs_review'|'assessed_good'}
 */
export function stateForVerdict(verdict) {
  if (verdict.assessment === 'no_violation') return 'assessed_good';
  if (verdict.assessment === 'other') return 'needs_review';
  return verdict.confidence === 'high' ? 'llm_flagged' : 'needs_review';
}

/**
 * The JSON schema sent to the model for strict structured output.
 * @returns {Record<string, unknown>}
 */
export function responseJsonSchema() {
  return {
    type: 'object',
    additionalProperties: false,
    required: ['batch_id', 'verdicts'],
    properties: {
      batch_id: { type: 'string' },
      verdicts: {
        type: 'array',
        items: {
          type: 'object',
          additionalProperties: false,
          required: ['alert_id', 'assessment', 'principle_id', 'confidence', 'justification'],
          properties: {
            alert_id: { type: 'string' },
            assessment: { type: 'string', enum: [...ASSESSMENTS] },
            principle_id: { type: 'string', enum: ['NONE', 'OTHER', ...CITABLE_IDS] },
            confidence: { type: 'string', enum: [...CONFIDENCES] },
            justification: { type: 'string', maxLength: MAX_JUSTIFICATION_LENGTH },
          },
        },
      },
    },
  };
}
