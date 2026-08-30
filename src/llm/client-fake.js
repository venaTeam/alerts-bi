import { LlmTransportError } from './client.js';

/**
 * Deterministic `LlmClient` fake (design section 5.1, decided 2026-08-29).
 *
 * Tests use this, never the network. Scripted results are selected by (batch_id, attempt
 * number), which is what makes "the second attempt succeeds" and "all three attempts
 * fail" expressible without timing or randomness.
 *
 * It also records every call, so a test can assert the two properties that matter most:
 * exactly three attempts, and byte-identical retry payloads.
 */

/**
 * @typedef {object} ScriptedResult
 * @property {'ok'|'raw'|'transport_error'|'timeout'|'invalid_json'} kind
 * @property {string} [text] Raw response text for `raw` and `invalid_json`.
 * @property {(request: any) => any} [build] Response builder for `ok`.
 */

export class FakeLlmClient {
  /**
   * @param {object} [options]
   * @param {string} [options.modelVersion]
   * @param {Map<string, ScriptedResult>} [options.script] Keyed `batchId#attempt`.
   * @param {ScriptedResult} [options.fallback] Used when no script entry matches.
   */
  constructor(options = {}) {
    this.modelVersion = options.modelVersion ?? 'fake-model-1';
    this.script = options.script ?? new Map();
    this.fallback = options.fallback ?? { kind: 'ok' };
    /** @type {Array<{batchId: string, attempt: number, requestText: string, systemPrompt: string}>} */
    this.calls = [];
  }

  /**
   * Script a result for one batch attempt.
   * @param {string} batchId
   * @param {number} attempt
   * @param {ScriptedResult} result
   * @returns {this}
   */
  on(batchId, attempt, result) {
    this.script.set(`${batchId}#${attempt}`, result);
    return this;
  }

  /**
   * Every request text seen for a batch, in attempt order. A test asserts these are
   * identical to prove retries resend the same bytes.
   * @param {string} batchId
   * @returns {string[]}
   */
  payloadsFor(batchId) {
    return this.calls.filter((c) => c.batchId === batchId).map((c) => c.requestText);
  }

  /**
   * @param {{systemPrompt: string, requestText: string, batchId: string, attempt: number}} args
   * @returns {Promise<string>}
   */
  async complete({ systemPrompt, requestText, batchId, attempt }) {
    this.calls.push({ batchId, attempt, requestText, systemPrompt });

    const result = this.script.get(`${batchId}#${attempt}`) ?? this.fallback;

    switch (result.kind) {
      case 'transport_error':
        throw new LlmTransportError(result.text ?? 'scripted transport failure', 'transport');
      case 'timeout':
        throw new LlmTransportError(result.text ?? 'scripted timeout', 'timeout');
      case 'invalid_json':
        return result.text ?? '{not valid json';
      case 'raw':
        return /** @type {string} */ (result.text);
      case 'ok':
      default:
        return JSON.stringify(this.buildDefault(JSON.parse(requestText), result));
    }
  }

  /**
   * Default success response: every alert assessed, no violation.
   * @param {any} request
   * @param {ScriptedResult} result
   * @returns {any}
   */
  buildDefault(request, result) {
    if (result.build) return result.build(request);
    return {
      batch_id: request.batch_id,
      verdicts: request.alerts.map((/** @type {any} */ alert) => ({
        alert_id: alert.alert_id,
        assessment: 'no_violation',
        principle_id: 'NONE',
        confidence: 'high',
        justification: 'Deterministic fake: no violation.',
      })),
    };
  }
}

/**
 * Build a scripted success whose verdicts are chosen per alert.
 *
 * @param {(alert: any, index: number, request: any) => object} decide
 * @returns {ScriptedResult}
 */
export function scriptedVerdicts(decide) {
  return {
    kind: 'ok',
    build: (request) => ({
      batch_id: request.batch_id,
      verdicts: request.alerts.map((/** @type {any} */ alert, /** @type {number} */ i) => ({
        alert_id: alert.alert_id,
        ...decide(alert, i, request),
      })),
    }),
  };
}
