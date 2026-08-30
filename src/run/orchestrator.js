import { loadRegistry, selectTeam, snapshotTeamEntry } from '../registry/registry.js';
import { buildRunWindow } from '../domain/window.js';
import { computeDailyVolume } from '../domain/metrics.js';
import { EsClient } from '../es/client.js';
import { readTeamAlerts } from '../es/reader.js';
import {
  evaluateRows,
  attachRowFindings,
  computeDailyRuleCounts,
  computeDailyFlagged,
} from '../rules/engine.js';
import { phase2ReadinessPct } from '../rules/readiness.js';
import { derivePhase } from '../rules/phase.js';
import { evaluateSuppression, buildR5Findings } from '../suppression/evaluate.js';
import { assessAlerts, markAllUnassessed } from '../llm/assess.js';
import { buildPrompt } from '../llm/prompt.js';
import { FakeLlmClient } from '../llm/client-fake.js';
import { OpenAiLlmClient } from '../llm/client-openai.js';
import { findVerdicts, verdictKey } from '../db/repositories.js';
import { sha256Of } from '../util/hash.js';
import { logger } from '../util/logger.js';
import { RULESET_VERSION, PROMPT_VERSION, PARSER_VERSION, APP_VERSION } from '../versions.js';

/**
 * The run orchestrator (flow steps 1-8).
 *
 * One selected team, one exact 168-hour UTC window, in the order the flow document
 * fixes: validate the registry, read Elasticsearch, count, apply deterministic rules,
 * evaluate suppression, assess the remainder, derive the phase, and persist.
 *
 * It never defaults to all teams.
 */

const SCHEMAS = /** @type {const} */ (['v1', 'v2']);

/**
 * Deterministic run identifier.
 *
 * Derived from the inputs that define the run rather than randomly generated, so
 * re-running the same team over the same frozen `run_at` and versions replaces its own
 * rows instead of accumulating near-duplicate runs. Two genuinely different runs - a
 * different clock, registry or version - always get different ids.
 *
 * @param {object} parts
 * @returns {string}
 */
export function computeRunId(parts) {
  return sha256Of(parts);
}

/**
 * @typedef {import('../domain/normalize.js').AlertRecord} AlertRecord
 */

/**
 * @typedef {object} PersistencePayload
 * @property {Record<string, any>} run
 * @property {Array<Record<string, any>>} dailyMetrics
 * @property {Array<Record<string, any>>} ruleCounts
 * @property {Array<Record<string, any>>} findings
 * @property {Array<Record<string, any>>} batchAttempts
 * @property {Array<Record<string, any>>} verdicts
 * @property {Array<Record<string, any>>} panelParses
 * @property {Array<Record<string, any>>} runPanels
 */

/**
 * Choose the LLM client for a run.
 *
 * Tests and mock runs use the deterministic fake; the live adapter is only constructed
 * when the model is explicitly enabled and configured.
 *
 * @param {import('../config/env.js').AppConfig} config
 * @param {{llm?: boolean}} options
 * @returns {{client: import('../llm/client.js').LlmClient|null, reason: string|null}}
 */
export function selectLlmClient(config, options) {
  if (options.llm === false) {
    return { client: null, reason: 'LLM assessment was disabled for this run' };
  }
  if (!config.llm.enabled) {
    return { client: null, reason: 'LLM assessment is disabled by configuration (LLM_ENABLED)' };
  }
  return { client: new OpenAiLlmClient(config.llm), reason: null };
}

/**
 * Execute one run and return the payload to persist.
 *
 * Nothing is written here: persistence is the caller's step, so a run can be computed and
 * inspected without touching the store.
 *
 * @param {object} args
 * @param {string} args.teamId
 * @param {Date} args.runAt
 * @param {import('../config/env.js').AppConfig} args.config
 * @param {string} [args.registryPath]
 * @param {import('../llm/client.js').LlmClient|null} [args.llmClient]
 * @param {string|null} [args.llmDisabledReason]
 * @param {any} [args.pool] Optional pool for durable verdict lookup.
 * @param {EsClient} [args.esClient]
 * @returns {Promise<{payload: PersistencePayload, summary: Record<string, any>}>}
 */
export async function executeRun(args) {
  const startedAt = new Date();
  const {
    teamId,
    runAt,
    config,
    registryPath,
    llmClient = null,
    llmDisabledReason = null,
    pool = null,
  } = args;

  // 1. Registry validation completes before any Elasticsearch query: a registry mistake
  // silently changes which alerts belong to a team, and that error is invisible in the
  // output.
  const loaded = loadRegistry(registryPath);
  const team = selectTeam(loaded, teamId);

  const window = buildRunWindow(runAt);
  const modelVersion = llmClient?.modelVersion ?? null;
  const runId = computeRunId({
    team_id: team.team_id,
    run_at: window.runAt.toISOString(),
    window_start: window.windowStart.toISOString(),
    registry_sha256: loaded.fileSha256,
    ruleset_version: RULESET_VERSION,
    prompt_version: PROMPT_VERSION,
    model_version: modelVersion,
    app_version: APP_VERSION,
  });

  logger.info('run.started', {
    run_id: runId,
    team_id: team.team_id,
    window_start: window.windowStart.toISOString(),
    window_end: window.windowEnd.toISOString(),
    registry_version: loaded.registryVersion,
  });

  // 2. Read both schemas, scoped to this team's operators only.
  const esClient = args.esClient ?? new EsClient(config.es);
  const read = await readTeamAlerts(esClient, team, window);
  /** @type {Record<'v1'|'v2', AlertRecord[]>} */
  const rowsBySchema = { v1: read.v1.rows, v2: read.v2.rows };

  // 3-4. Evaluate every raw row, then aggregate to identity.
  /** @type {Record<'v1'|'v2', ReturnType<typeof evaluateRows>>} */
  const evaluation = {
    v1: evaluateRows(rowsBySchema.v1),
    v2: evaluateRows(rowsBySchema.v2),
  };

  // 5. Suppression, which produces core rule 5 and can withhold identities from the LLM.
  /** @type {Record<'v1'|'v2', ReturnType<typeof evaluateSuppression>>} */
  const suppression = {
    v1: evaluateSuppression(
      rowsBySchema.v1,
      (team.panels || []).filter((p) => p.schema === 'v1'),
    ),
    v2: evaluateSuppression(
      rowsBySchema.v2,
      (team.panels || []).filter((p) => p.schema === 'v2'),
    ),
  };
  for (const schema of SCHEMAS) {
    attachRowFindings(
      evaluation[schema],
      buildR5Findings(
        suppression[schema].suppressedRows,
        (team.panels || []).filter((p) => p.schema === schema),
      ),
    );
  }

  // 6. Assess the identities that carry no core finding.
  /** @type {AlertRecord[]} */
  const eligible = [];
  for (const schema of SCHEMAS) {
    for (const identity of evaluation[schema].identities.values()) {
      if (identity.llmEligible) eligible.push(identity.representative);
    }
  }

  /** @type {Map<string, import('../llm/assess.js').AssessmentOutcome>} */
  let outcomes;
  /** @type {Array<Record<string, any>>} */
  let batchAttempts = [];
  /** @type {Array<Record<string, any>>} */
  let newVerdicts = [];
  let llmAssessed = false;

  if (llmClient) {
    const existingVerdicts = pool
      ? await findVerdicts(
          pool,
          PROMPT_VERSION,
          llmClient.modelVersion,
          eligible.map((a) => ({ application: a.application, keyField: a.keyField })),
        )
      : new Map();

    const { systemPrompt } = buildPrompt();
    const assessment = await assessAlerts({
      alerts: eligible,
      client: llmClient,
      systemPrompt,
      runId,
      promptVersion: PROMPT_VERSION,
      modelVersion: llmClient.modelVersion,
      maxBatchSize: config.llm.maxBatchSize,
      existingVerdicts,
      now: startedAt,
    });
    outcomes = assessment.outcomes;
    batchAttempts = assessment.batchAttempts;
    newVerdicts = assessment.newVerdicts;
    llmAssessed = true;
    logger.info('run.llm_complete', {
      run_id: runId,
      eligible: eligible.length,
      batches: assessment.requestedBatches,
      reused: assessment.reusedVerdicts,
    });
  } else {
    // "Good" is never inferred by subtracting flagged from total, so a run without the
    // model reports its eligible identities as unassessed with an explicit reason.
    outcomes = markAllUnassessed(eligible, llmDisabledReason ?? 'LLM assessment did not run');
  }

  // 7. Phase derivation from distinct identity presence and readiness.
  const v2Representatives = [...evaluation.v2.identities.values()].map((i) => i.representative);
  const readiness = phase2ReadinessPct(v2Representatives);
  const phase = derivePhase(
    evaluation.v1.identities.size,
    evaluation.v2.identities.size,
    readiness,
  );

  // 8. Build the persistence payload.
  const snapshotDates = window.buckets.map((b) => b.snapshotDate);
  /** @type {Array<Record<string, any>>} */
  const dailyMetrics = [];
  /** @type {Array<Record<string, any>>} */
  const ruleCounts = [];
  /** @type {Array<Record<string, any>>} */
  const findings = [];
  /** @type {Array<Record<string, any>>} */
  const runPanels = [];
  /** @type {Array<Record<string, any>>} */
  const panelParses = [];

  for (const schema of SCHEMAS) {
    const daily = computeDailyVolume(rowsBySchema[schema], window);
    const flagged = computeDailyFlagged(evaluation[schema].rows, snapshotDates);
    const quality = allocateQualityByDate(evaluation[schema], outcomes, snapshotDates);
    const suppressedByDate = countSuppressedByDate(evaluation[schema], snapshotDates);

    daily.forEach((day, index) => {
      const flaggedDay = flagged.get(day.snapshotDate) ?? {
        flaggedByRule: 0,
        flaggedByRuleDistinct: 0,
      };
      const q = quality.get(day.snapshotDate);
      dailyMetrics.push({
        run_id: runId,
        team_id: team.team_id,
        alert_schema: schema,
        snapshot_date: day.snapshotDate,
        bucket_start: day.bucketStart,
        bucket_end: day.bucketEnd,
        covered_hours: day.coveredHours,
        alerts: day.alerts,
        distinct_alerts: day.distinctAlerts,
        alerts_per_hour: day.alertsPerHour,
        node_name_numerator: day.nodeNameNumerator,
        node_name_denominator: day.nodeNameDenominator,
        node_name_ratio: day.nodeNameRatio,
        key_inflation_numerator: day.keyInflationNumerator,
        key_inflation_denominator: day.keyInflationDenominator,
        key_inflation_ratio: day.keyInflationRatio,
        flagged_by_rule: flaggedDay.flaggedByRule,
        flagged_by_rule_distinct: flaggedDay.flaggedByRuleDistinct,
        flagged_by_llm: q.flaggedByLlm,
        flagged_by_llm_distinct: q.flaggedByLlmDistinct,
        needs_review: q.needsReview,
        assessed_good: q.assessedGood,
        unassessed: q.unassessed,
        phase2_gaps: q.phase2Gaps,
        suppressed: suppressedByDate.get(day.snapshotDate) ?? 0,
        // A leaf is unmeasured regardless of date, so the run-level count is allocated to
        // the schema's first bucket and zero elsewhere. Summing the daily column then
        // yields the run total exactly once instead of eight times.
        suppression_unmeasured: index === 0 ? suppression[schema].unmeasuredLeaves : 0,
      });
    });

    for (const count of computeDailyRuleCounts(evaluation[schema].rows, snapshotDates)) {
      ruleCounts.push({
        run_id: runId,
        team_id: team.team_id,
        alert_schema: schema,
        snapshot_date: count.snapshotDate,
        rule_id: count.ruleId,
        ruleset_version: RULESET_VERSION,
        match_count: count.count,
        distinct_count: count.distinctCount,
      });
    }

    for (const identity of evaluation[schema].identities.values()) {
      findings.push(buildFindingRow(runId, identity, outcomes.get(identity.identity) ?? null));
    }

    for (const interpretation of suppression[schema].interpretations) {
      const suppressionLeaves = interpretation.leaves.filter(
        (l) => l.kind === 'suppression',
      ).length;
      const unmeasured = interpretation.leaves.filter((l) => l.kind === 'unmeasured').length;
      runPanels.push({
        run_id: runId,
        panel_id: interpretation.panelId,
        alert_schema: interpretation.schema,
        sql_text_hash: interpretation.sqlTextHash,
        parser_version: interpretation.parserVersion,
        suppression_leaves: suppressionLeaves,
        unmeasured_leaves: interpretation.safetyState === 'unparseable' ? 1 : unmeasured,
        notes: JSON.stringify({
          unknown_fields: interpretation.unknownFields,
          unmeasured_reason: interpretation.unmeasuredReason,
        }),
      });
      panelParses.push({
        sql_text_hash: interpretation.sqlTextHash,
        parser_version: PARSER_VERSION,
        parsed_result: JSON.stringify(interpretation.leaves),
        safety_state: interpretation.safetyState,
        unmeasured_reason: interpretation.unmeasuredReason,
        created_at: startedAt,
      });
    }
  }

  const run = {
    run_id: runId,
    run_at: window.runAt,
    team_id: team.team_id,
    team_display_name: team.display_name,
    window_start: window.windowStart,
    window_end: window.windowEnd,
    registry_version: loaded.registryVersion,
    registry_sha256: loaded.fileSha256,
    registry_entry_snapshot: snapshotTeamEntry(team),
    ruleset_version: RULESET_VERSION,
    prompt_version: PROMPT_VERSION,
    model_version: modelVersion,
    llm_assessed: llmAssessed,
    phase_derived: phase,
    phase2_readiness_pct: readiness,
    app_version: APP_VERSION,
    status: 'completed',
    started_at: startedAt,
    completed_at: new Date(),
    error_summary: null,
  };

  return {
    payload: {
      run,
      dailyMetrics,
      ruleCounts,
      findings,
      batchAttempts,
      verdicts: newVerdicts,
      panelParses: dedupeBy(panelParses, (p) => `${p.sql_text_hash}|${p.parser_version}`),
      runPanels,
    },
    summary: {
      runId,
      teamId: team.team_id,
      phase,
      readiness,
      v1Rows: rowsBySchema.v1.length,
      v2Rows: rowsBySchema.v2.length,
      v1Identities: evaluation.v1.identities.size,
      v2Identities: evaluation.v2.identities.size,
      llmEligible: eligible.length,
      llmAssessed,
    },
  };
}

/**
 * Allocate identity-level LLM states to UTC dates.
 *
 * An LLM verdict belongs to the identity, not to a row: a high-confidence catalogue
 * violation projects `flagged_by_llm` onto every raw row under that identity, and counts
 * the identity once in `flagged_by_llm_distinct` for every bucket in which it appears.
 * `needs_review`, `assessed_good` and `unassessed` likewise count the identity once in
 * every bucket where it appears.
 *
 * That differs deliberately from deterministic findings, which stay on the dates of the
 * rows that actually matched.
 *
 * @param {ReturnType<typeof evaluateRows>} evaluation
 * @param {Map<string, import('../llm/assess.js').AssessmentOutcome>} outcomes
 * @param {string[]} snapshotDates
 * @returns {Map<string, {flaggedByLlm: number, flaggedByLlmDistinct: number, needsReview: number, assessedGood: number, unassessed: number, phase2Gaps: number}>}
 */
export function allocateQualityByDate(evaluation, outcomes, snapshotDates) {
  const acc = new Map(
    snapshotDates.map((date) => [
      date,
      {
        flaggedByLlm: 0,
        flaggedByLlmDistinct: 0,
        needsReview: 0,
        assessedGood: 0,
        unassessed: 0,
        phase2Gaps: 0,
      },
    ]),
  );

  for (const identity of evaluation.identities.values()) {
    // Readiness gaps are orthogonal to the quality state and may coexist with any of them.
    if (identity.schema === 'v2' && identity.readinessRuleIds.length > 0) {
      for (const date of identity.presentDates) {
        const entry = acc.get(date);
        if (entry) entry.phase2Gaps += 1;
      }
    }

    if (identity.hasCoreFinding) continue; // rule_flagged: not an LLM state
    const outcome = outcomes.get(identity.identity);
    if (!outcome) continue;

    /** @type {Map<string, number>} rows per date under this identity */
    const rowsPerDate = new Map();
    for (const evaluated of identity.rows) {
      rowsPerDate.set(
        evaluated.row.snapshotDate,
        (rowsPerDate.get(evaluated.row.snapshotDate) ?? 0) + 1,
      );
    }

    for (const date of identity.presentDates) {
      const entry = acc.get(date);
      if (!entry) continue;
      if (outcome.state === 'llm_flagged') {
        entry.flaggedByLlm += rowsPerDate.get(date) ?? 0;
        entry.flaggedByLlmDistinct += 1;
      } else if (outcome.state === 'needs_review') {
        entry.needsReview += 1;
      } else if (outcome.state === 'assessed_good') {
        entry.assessedGood += 1;
      } else if (outcome.state === 'unassessed') {
        entry.unassessed += 1;
      }
    }
  }

  return acc;
}

/**
 * `suppressed` is rule 5's row count promoted to a headline column, so it is counted on
 * the dates of the rows that actually matched - which keeps it a subset of
 * `flagged_by_rule` rather than an addition to it.
 *
 * @param {ReturnType<typeof evaluateRows>} evaluation
 * @param {string[]} snapshotDates
 * @returns {Map<string, number>}
 */
function countSuppressedByDate(evaluation, snapshotDates) {
  const acc = new Map(snapshotDates.map((d) => [d, 0]));
  for (const evaluated of evaluation.rows) {
    if (!evaluated.coreFindings.some((f) => f.ruleId === 'R5')) continue;
    const date = evaluated.row.snapshotDate;
    if (acc.has(date)) acc.set(date, (acc.get(date) ?? 0) + 1);
  }
  return acc;
}

/**
 * @param {string} runId
 * @param {import('../rules/engine.js').EvaluatedIdentity} identity
 * @param {import('../llm/assess.js').AssessmentOutcome|null} outcome
 * @returns {Record<string, any>}
 */
function buildFindingRow(runId, identity, outcome) {
  const rep = identity.representative;
  const timestamps = identity.rows.map((r) => r.row.timestamp.getTime());

  // Evidence is summarized per rule rather than per row: a v1 alert re-firing every five
  // minutes would otherwise store 2,000 near-identical evidence objects.
  /** @type {Map<string, {rule_id: string, matched_rows: number, sample_evidence: any}>} */
  const evidence = new Map();
  for (const evaluated of identity.rows) {
    for (const finding of [...evaluated.coreFindings, ...evaluated.readinessFindings]) {
      const existing = evidence.get(finding.ruleId);
      if (existing) existing.matched_rows += 1;
      else
        evidence.set(finding.ruleId, {
          rule_id: finding.ruleId,
          matched_rows: 1,
          sample_evidence: finding.evidence,
        });
    }
  }

  const state = identity.hasCoreFinding ? 'rule_flagged' : (outcome?.state ?? 'unassessed');

  return {
    run_id: runId,
    alert_schema: identity.schema,
    application: identity.application,
    key_field: identity.keyField,
    representative_at: rep.timestamp,
    representative_hash: rep.docHash,
    representative_doc: JSON.stringify(rep.source),
    message: rep.message,
    severity: rep.severity,
    component: rep.component,
    node_name: rep.nodeName,
    environment: rep.environment,
    provider: rep.provider,
    alert_rule_url: rep.alertRuleUrl,
    row_count: identity.rows.length,
    first_seen: new Date(Math.min(...timestamps)),
    last_seen: new Date(Math.max(...timestamps)),
    core_rule_ids: identity.coreRuleIds.join(','),
    readiness_rule_ids: identity.readinessRuleIds.join(','),
    findings_evidence: JSON.stringify([...evidence.values()]),
    quality_state: state,
    llm_principle_id: state === 'rule_flagged' ? null : (outcome?.principleId ?? null),
    llm_confidence: state === 'rule_flagged' ? null : (outcome?.confidence ?? null),
    llm_justification: state === 'rule_flagged' ? null : (outcome?.justification ?? null),
    unassessed_reason:
      state === 'unassessed'
        ? (outcome?.unassessedReason ?? 'identity was not assessed in this run')
        : null,
  };
}

/**
 * @template T
 * @param {T[]} items
 * @param {(item: T) => string} keyOf
 * @returns {T[]}
 */
function dedupeBy(items, keyOf) {
  /** @type {Map<string, T>} */
  const seen = new Map();
  for (const item of items) {
    const key = keyOf(item);
    if (!seen.has(key)) seen.set(key, item);
  }
  return [...seen.values()];
}

export { FakeLlmClient, verdictKey };
