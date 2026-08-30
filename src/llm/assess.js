import { buildBatches } from './grouping.js';
import { buildRequest, serializeRequest, assertLossless } from './request.js';
import { validateResponse, stateForVerdict, LlmResponseError } from './response.js';
import { LlmTransportError } from './client.js';
import { verdictKey } from '../db/repositories.js';
import { logger, redactError } from '../util/logger.js';
import { RULESET_VERSION } from '../versions.js';

/**
 * LLM assessment orchestration (design section 5.1; flow step 6).
 *
 * Only identities with no core finding on any row enter this stage; alerts carrying v2
 * readiness gaps are still eligible. Coverage is exhaustive - there is no classification
 * budget - so `unassessed` is a failure state rather than a design parameter, and any
 * non-zero value reads as a broken classifier.
 */

/** The initial request plus two retries. Not configurable. */
export const MAX_ATTEMPTS = 3;

/**
 * @typedef {import('../domain/normalize.js').AlertRecord} AlertRecord
 * @typedef {import('./client.js').LlmClient} LlmClient
 */

/**
 * @typedef {object} AssessmentOutcome
 * @property {'llm_flagged'|'needs_review'|'assessed_good'|'unassessed'} state
 * @property {string|null} principleId
 * @property {string|null} confidence
 * @property {string|null} justification
 * @property {string|null} unassessedReason
 * @property {boolean} reused True when a durable verdict was reused rather than requested.
 * @property {AlertRecord} alert
 */

/**
 * @typedef {object} AssessmentResult
 * @property {Map<string, AssessmentOutcome>} outcomes Keyed by alert identity.
 * @property {Array<Record<string, any>>} batchAttempts Rows for llm_batch_attempts.
 * @property {Array<Record<string, any>>} newVerdicts Rows for llm_verdicts.
 * @property {number} requestedBatches
 * @property {number} reusedVerdicts
 */

/**
 * Assess every eligible alert.
 *
 * @param {object} args
 * @param {AlertRecord[]} args.alerts Representative rows of LLM-eligible identities.
 * @param {LlmClient} args.client
 * @param {string} args.systemPrompt
 * @param {string} args.runId
 * @param {string} args.promptVersion
 * @param {string} args.modelVersion
 * @param {number} [args.maxBatchSize]
 * @param {Map<string, any>} [args.existingVerdicts] Durable verdicts keyed by verdictKey().
 * @param {Date} [args.now]
 * @returns {Promise<AssessmentResult>}
 */
export async function assessAlerts(args) {
  const {
    alerts,
    client,
    systemPrompt,
    runId,
    promptVersion,
    modelVersion,
    maxBatchSize,
    existingVerdicts = new Map(),
    now = new Date(),
  } = args;

  /** @type {Map<string, AssessmentOutcome>} */
  const outcomes = new Map();
  /** @type {Array<Record<string, any>>} */
  const batchAttempts = [];
  /** @type {Array<Record<string, any>>} */
  const newVerdicts = [];

  // 6.1 Reuse durable verdicts first. A stored verdict belongs to the prompt and model
  // version that produced it and is never recomputed under the same pair.
  /** @type {AlertRecord[]} */
  const toRequest = [];
  let reusedVerdicts = 0;

  for (const alert of alerts) {
    const stored = existingVerdicts.get(verdictKey(alert.application, alert.keyField));
    if (stored) {
      outcomes.set(alert.identity, {
        state: stateForVerdict({
          alert_id: '',
          assessment: stored.assessment,
          principle_id: stored.principle_id,
          confidence: stored.confidence,
          justification: stored.justification,
        }),
        principleId: stored.principle_id,
        confidence: stored.confidence,
        justification: stored.justification,
        unassessedReason: null,
        reused: true,
        alert,
      });
      reusedVerdicts += 1;
      continue;
    }
    toRequest.push(alert);
  }

  const batches = buildBatches(toRequest, {
    runId,
    promptVersion,
    modelVersion,
    maxBatchSize,
  });

  for (const batch of batches) {
    const request = buildRequest(batch, {
      rulesetVersion: RULESET_VERSION,
      promptVersion,
    });
    // Losslessness is asserted before the first call, so a factoring bug fails loudly
    // instead of quietly sending the model documents with fields missing.
    assertLossless(request, batch);

    // Serialized ONCE and reused byte-for-byte on every retry.
    const serialized = serializeRequest(request);

    const result = await attemptBatch({
      batch,
      serialized,
      client,
      systemPrompt,
      runId,
      now,
      batchAttempts,
    });

    if (result.verdicts) {
      result.verdicts.forEach((verdict, i) => {
        const alert = batch.alerts[i];
        const state = stateForVerdict(verdict);
        outcomes.set(alert.identity, {
          state,
          principleId: verdict.principle_id,
          confidence: verdict.confidence,
          justification: verdict.justification,
          unassessedReason: null,
          reused: false,
          alert,
        });
        newVerdicts.push({
          application: alert.application,
          key_field: alert.keyField,
          prompt_version: promptVersion,
          model_version: modelVersion,
          alert_schema: alert.schema,
          assessment: verdict.assessment,
          principle_id: verdict.principle_id,
          confidence: verdict.confidence,
          justification: verdict.justification,
          // The source row expires after three months, so the exact document the model
          // judged is stored here or the verdict becomes unauditable.
          representative_doc: JSON.stringify(alert.source),
          doc_hash: alert.docHash,
          classified_at: now,
          ruleset_version: RULESET_VERSION,
          first_run_id: runId,
        });
      });
    } else {
      // After the third failure, EVERY alert in the batch becomes unassessed with the
      // shared reason. Alerts are never retried individually.
      for (const alert of batch.alerts) {
        outcomes.set(alert.identity, {
          state: 'unassessed',
          principleId: null,
          confidence: null,
          justification: null,
          unassessedReason: result.failureReason,
          reused: false,
          alert,
        });
      }
    }
  }

  return {
    outcomes,
    batchAttempts,
    newVerdicts,
    requestedBatches: batches.length,
    reusedVerdicts,
  };
}

/**
 * Run one batch through at most three attempts.
 *
 * Each retry sends the IDENTICAL batch as a unit: same membership, same ordering, same
 * bytes. There is no alert-by-alert fallback, because a verdict attached to the wrong
 * alert is the worst output this system can produce.
 *
 * @param {object} args
 * @returns {Promise<{verdicts: import('./response.js').Verdict[]|null, failureReason: string}>}
 */
async function attemptBatch(args) {
  const { batch, serialized, client, systemPrompt, runId, now, batchAttempts } = args;
  let failureReason = 'unknown failure';

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
    const startedAt = Date.now();
    /** @type {string} */
    let status = 'succeeded';
    /** @type {string|null} */
    let reason = null;
    /** @type {import('./response.js').Verdict[]|null} */
    let verdicts = null;

    try {
      const text = await client.complete({
        systemPrompt,
        requestText: serialized.text,
        batchId: batch.batchId,
        attempt,
      });

      /** @type {unknown} */
      let parsed;
      try {
        parsed = JSON.parse(text);
      } catch (err) {
        throw new LlmResponseError(`response is not valid JSON: ${redactError(err)}`);
      }

      verdicts = validateResponse(parsed, {
        batchId: batch.batchId,
        alertIds: batch.alertIds,
      });
    } catch (err) {
      if (err instanceof LlmTransportError) {
        // A timeout or transport error consumes one recorded attempt like any other
        // failed call.
        status = err.kind === 'timeout' ? 'timeout' : 'transport_error';
      } else if (err instanceof LlmResponseError) {
        status = 'invalid_response';
      } else {
        status = 'error';
      }
      reason = redactError(err);
      failureReason = `${status}: ${reason}`;
      verdicts = null;
    }

    batchAttempts.push({
      run_id: runId,
      batch_id: batch.batchId,
      attempt_number: attempt,
      group_type: batch.groupType,
      group_value: batch.groupValue,
      partition_index: batch.partitionIndex,
      partition_count: batch.partitionCount,
      alert_count: batch.alerts.length,
      alert_ids: JSON.stringify(batch.alertIds),
      request_hash: serialized.hash,
      // The complete payload is auditable data, kept in SQL and never in ordinary logs.
      request_payload: serialized.text,
      status,
      failure_reason: reason === null ? null : reason.slice(0, 1000),
      duration_ms: Date.now() - startedAt,
      created_at: now,
    });

    if (verdicts) {
      logger.info('llm.batch_succeeded', {
        batch_id: batch.batchId,
        attempt,
        alerts: batch.alerts.length,
      });
      return { verdicts, failureReason: '' };
    }

    logger.warn('llm.batch_attempt_failed', {
      batch_id: batch.batchId,
      attempt,
      status,
    });
  }

  logger.error('llm.batch_exhausted', {
    batch_id: batch.batchId,
    attempts: MAX_ATTEMPTS,
    alerts: batch.alerts.length,
  });
  return {
    verdicts: null,
    failureReason: `batch exhausted ${MAX_ATTEMPTS} attempts - ${failureReason}`.slice(0, 500),
  };
}

/**
 * Mark every eligible identity unassessed without calling a model.
 *
 * Used when the run is executed with LLM assessment switched off. The reason is recorded
 * per identity so the scorecard never presents "we did not look" as "it is fine".
 *
 * @param {AlertRecord[]} alerts
 * @param {string} reason
 * @returns {Map<string, AssessmentOutcome>}
 */
export function markAllUnassessed(alerts, reason) {
  /** @type {Map<string, AssessmentOutcome>} */
  const outcomes = new Map();
  for (const alert of alerts) {
    outcomes.set(alert.identity, {
      state: 'unassessed',
      principleId: null,
      confidence: null,
      justification: null,
      unassessedReason: reason,
      reused: false,
      alert,
    });
  }
  return outcomes;
}
