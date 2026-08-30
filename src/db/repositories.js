import { sql, request, withTransaction } from './pool.js';

/**
 * Repositories over the run store.
 *
 * Two write scopes exist and they are deliberately different:
 *
 * - Run-scoped rows (metrics, rule counts, findings, batch attempts, panels) are replaced
 *   wholesale when a run id is re-persisted, so a restarted run cannot leave a mixture of
 *   two attempts behind.
 * - Durable rows (llm_verdicts, panel_parses) are inserted only when absent. A stored
 *   verdict is never recomputed except on a version bump, and a frozen panel
 *   interpretation must stay stable between runs on identical input.
 */

/**
 * Map key for a durable verdict lookup.
 *
 * Length-prefixed rather than joined with a separator: application and key_field are free
 * text from the sending team, so any printable separator could appear inside a value and
 * make two different alerts collide on one key.
 *
 * @param {string} application
 * @param {string} keyField
 * @returns {string}
 */
export function verdictKey(application, keyField) {
  return `${application.length}:${application}|${keyField.length}:${keyField}`;
}

/** SQL Server caps a request at 2100 parameters. */
const MAX_PARAMETERS = 2000;

/**
 * Insert rows in parameterized batches.
 *
 * @param {any} target Pool or transaction.
 * @param {string} table
 * @param {Array<{name: string, type: any}>} columns
 * @param {Array<Record<string, unknown>>} rows
 * @returns {Promise<void>}
 */
async function insertRows(target, table, columns, rows) {
  if (rows.length === 0) return;
  const perChunk = Math.max(1, Math.floor(MAX_PARAMETERS / columns.length));
  const columnList = columns.map((c) => c.name).join(', ');

  for (let start = 0; start < rows.length; start += perChunk) {
    const chunk = rows.slice(start, start + perChunk);
    /** @type {Record<string, {type: any, value: unknown}>} */
    const params = {};
    const valueGroups = chunk.map((row, i) => {
      const placeholders = columns.map((c) => {
        const param = `${c.name}_${i}`;
        params[param] = { type: c.type, value: row[c.name] ?? null };
        return `@${param}`;
      });
      return `(${placeholders.join(', ')})`;
    });
    await request(target, params).query(
      `INSERT INTO ${table} (${columnList}) VALUES ${valueGroups.join(', ')}`,
    );
  }
}

const DAILY_METRIC_COLUMNS = [
  { name: 'run_id', type: sql.NVarChar(64) },
  { name: 'team_id', type: sql.NVarChar(128) },
  { name: 'alert_schema', type: sql.NVarChar(2) },
  { name: 'snapshot_date', type: sql.Date },
  { name: 'bucket_start', type: sql.DateTime2 },
  { name: 'bucket_end', type: sql.DateTime2 },
  { name: 'covered_hours', type: sql.Decimal(6, 3) },
  { name: 'alerts', type: sql.Int },
  { name: 'distinct_alerts', type: sql.Int },
  { name: 'alerts_per_hour', type: sql.Decimal(18, 6) },
  { name: 'node_name_numerator', type: sql.Int },
  { name: 'node_name_denominator', type: sql.Int },
  { name: 'node_name_ratio', type: sql.Decimal(18, 6) },
  { name: 'key_inflation_numerator', type: sql.Int },
  { name: 'key_inflation_denominator', type: sql.Int },
  { name: 'key_inflation_ratio', type: sql.Decimal(18, 6) },
  { name: 'flagged_by_rule', type: sql.Int },
  { name: 'flagged_by_rule_distinct', type: sql.Int },
  { name: 'flagged_by_llm', type: sql.Int },
  { name: 'flagged_by_llm_distinct', type: sql.Int },
  { name: 'needs_review', type: sql.Int },
  { name: 'assessed_good', type: sql.Int },
  { name: 'unassessed', type: sql.Int },
  { name: 'phase2_gaps', type: sql.Int },
  { name: 'suppressed', type: sql.Int },
  { name: 'suppression_unmeasured', type: sql.Int },
];

const RULE_COUNT_COLUMNS = [
  { name: 'run_id', type: sql.NVarChar(64) },
  { name: 'team_id', type: sql.NVarChar(128) },
  { name: 'alert_schema', type: sql.NVarChar(2) },
  { name: 'snapshot_date', type: sql.Date },
  { name: 'rule_id', type: sql.NVarChar(8) },
  { name: 'ruleset_version', type: sql.NVarChar(32) },
  { name: 'match_count', type: sql.Int },
  { name: 'distinct_count', type: sql.Int },
];

const FINDING_COLUMNS = [
  { name: 'run_id', type: sql.NVarChar(64) },
  { name: 'alert_schema', type: sql.NVarChar(2) },
  { name: 'application', type: sql.NVarChar(256) },
  { name: 'key_field', type: sql.NVarChar(512) },
  { name: 'representative_at', type: sql.DateTime2 },
  { name: 'representative_hash', type: sql.NVarChar(64) },
  { name: 'representative_doc', type: sql.NVarChar(sql.MAX) },
  { name: 'message', type: sql.NVarChar(sql.MAX) },
  { name: 'severity', type: sql.NVarChar(32) },
  { name: 'component', type: sql.NVarChar(256) },
  { name: 'node_name', type: sql.NVarChar(256) },
  { name: 'environment', type: sql.NVarChar(64) },
  { name: 'provider', type: sql.NVarChar(32) },
  { name: 'alert_rule_url', type: sql.NVarChar(1024) },
  { name: 'row_count', type: sql.Int },
  { name: 'first_seen', type: sql.DateTime2 },
  { name: 'last_seen', type: sql.DateTime2 },
  { name: 'core_rule_ids', type: sql.NVarChar(128) },
  { name: 'readiness_rule_ids', type: sql.NVarChar(128) },
  { name: 'findings_evidence', type: sql.NVarChar(sql.MAX) },
  { name: 'quality_state', type: sql.NVarChar(16) },
  { name: 'llm_principle_id', type: sql.NVarChar(8) },
  { name: 'llm_confidence', type: sql.NVarChar(8) },
  { name: 'llm_justification', type: sql.NVarChar(1000) },
  { name: 'unassessed_reason', type: sql.NVarChar(500) },
];

const BATCH_ATTEMPT_COLUMNS = [
  { name: 'run_id', type: sql.NVarChar(64) },
  { name: 'batch_id', type: sql.NVarChar(64) },
  { name: 'attempt_number', type: sql.Int },
  { name: 'group_type', type: sql.NVarChar(16) },
  { name: 'group_value', type: sql.NVarChar(1024) },
  { name: 'partition_index', type: sql.Int },
  { name: 'partition_count', type: sql.Int },
  { name: 'alert_count', type: sql.Int },
  { name: 'alert_ids', type: sql.NVarChar(sql.MAX) },
  { name: 'request_hash', type: sql.NVarChar(64) },
  { name: 'request_payload', type: sql.NVarChar(sql.MAX) },
  { name: 'status', type: sql.NVarChar(24) },
  { name: 'failure_reason', type: sql.NVarChar(1000) },
  { name: 'duration_ms', type: sql.Int },
  { name: 'created_at', type: sql.DateTime2 },
];

const RUN_PANEL_COLUMNS = [
  { name: 'run_id', type: sql.NVarChar(64) },
  { name: 'panel_id', type: sql.NVarChar(128) },
  { name: 'alert_schema', type: sql.NVarChar(2) },
  { name: 'sql_text_hash', type: sql.NVarChar(64) },
  { name: 'parser_version', type: sql.NVarChar(32) },
  { name: 'suppression_leaves', type: sql.Int },
  { name: 'unmeasured_leaves', type: sql.Int },
  { name: 'notes', type: sql.NVarChar(sql.MAX) },
];

/**
 * Persist a complete run atomically.
 *
 * All of it or none of it: a half-written run is indistinguishable from a complete one
 * once the report is rendered from SQL, which is exactly the failure the store exists to
 * prevent.
 *
 * @param {any} pool
 * @param {object} payload
 * @param {Record<string, any>} payload.run
 * @param {Array<Record<string, any>>} payload.dailyMetrics
 * @param {Array<Record<string, any>>} payload.ruleCounts
 * @param {Array<Record<string, any>>} payload.findings
 * @param {Array<Record<string, any>>} payload.batchAttempts
 * @param {Array<Record<string, any>>} payload.verdicts
 * @param {Array<Record<string, any>>} payload.panelParses
 * @param {Array<Record<string, any>>} payload.runPanels
 * @returns {Promise<void>}
 */
export async function persistRun(pool, payload) {
  await withTransaction(pool, async (tx) => {
    const runId = payload.run.run_id;

    // Replace this run's own rows. ON DELETE CASCADE covers the children, but they are
    // deleted explicitly so the intent survives a future schema change.
    for (const table of [
      'daily_metrics',
      'daily_rule_counts',
      'alert_findings',
      'llm_batch_attempts',
      'run_panels',
    ]) {
      await request(tx, { runId: { type: sql.NVarChar(64), value: runId } }).query(
        `DELETE FROM ${table} WHERE run_id = @runId`,
      );
    }
    await request(tx, { runId: { type: sql.NVarChar(64), value: runId } }).query(
      'DELETE FROM runs WHERE run_id = @runId',
    );

    await request(tx, {
      run_id: { type: sql.NVarChar(64), value: payload.run.run_id },
      run_at: { type: sql.DateTime2, value: payload.run.run_at },
      team_id: { type: sql.NVarChar(128), value: payload.run.team_id },
      team_display_name: { type: sql.NVarChar(256), value: payload.run.team_display_name },
      window_start: { type: sql.DateTime2, value: payload.run.window_start },
      window_end: { type: sql.DateTime2, value: payload.run.window_end },
      registry_version: { type: sql.NVarChar(64), value: payload.run.registry_version },
      registry_sha256: { type: sql.NVarChar(64), value: payload.run.registry_sha256 },
      registry_entry_snapshot: {
        type: sql.NVarChar(sql.MAX),
        value: payload.run.registry_entry_snapshot,
      },
      ruleset_version: { type: sql.NVarChar(32), value: payload.run.ruleset_version },
      prompt_version: { type: sql.NVarChar(32), value: payload.run.prompt_version },
      model_version: { type: sql.NVarChar(128), value: payload.run.model_version },
      llm_assessed: { type: sql.Bit, value: payload.run.llm_assessed },
      phase_derived: { type: sql.NVarChar(16), value: payload.run.phase_derived },
      phase2_readiness_pct: {
        type: sql.Decimal(9, 6),
        value: payload.run.phase2_readiness_pct,
      },
      app_version: { type: sql.NVarChar(32), value: payload.run.app_version },
      status: { type: sql.NVarChar(16), value: payload.run.status },
      started_at: { type: sql.DateTime2, value: payload.run.started_at },
      completed_at: { type: sql.DateTime2, value: payload.run.completed_at },
      error_summary: { type: sql.NVarChar(1000), value: payload.run.error_summary },
    }).query(`
      INSERT INTO runs (
        run_id, run_at, team_id, team_display_name, window_start, window_end,
        registry_version, registry_sha256, registry_entry_snapshot,
        ruleset_version, prompt_version, model_version, llm_assessed,
        phase_derived, phase2_readiness_pct, app_version, status,
        started_at, completed_at, error_summary
      ) VALUES (
        @run_id, @run_at, @team_id, @team_display_name, @window_start, @window_end,
        @registry_version, @registry_sha256, @registry_entry_snapshot,
        @ruleset_version, @prompt_version, @model_version, @llm_assessed,
        @phase_derived, @phase2_readiness_pct, @app_version, @status,
        @started_at, @completed_at, @error_summary
      )
    `);

    await insertRows(tx, 'daily_metrics', DAILY_METRIC_COLUMNS, payload.dailyMetrics);
    await insertRows(tx, 'daily_rule_counts', RULE_COUNT_COLUMNS, payload.ruleCounts);
    await insertRows(tx, 'alert_findings', FINDING_COLUMNS, payload.findings);
    await insertRows(tx, 'llm_batch_attempts', BATCH_ATTEMPT_COLUMNS, payload.batchAttempts);
    await insertRows(tx, 'run_panels', RUN_PANEL_COLUMNS, payload.runPanels);

    for (const verdict of payload.verdicts) await insertVerdictIfAbsent(tx, verdict);
    for (const parse of payload.panelParses) await insertPanelParseIfAbsent(tx, parse);
  });
}

/**
 * Insert a verdict only when its cache key is not already present.
 *
 * A stored verdict belongs to the prompt and model version that produced it and is never
 * recomputed under the same pair, so an existing row always wins.
 *
 * @param {any} target
 * @param {Record<string, any>} v
 * @returns {Promise<void>}
 */
export async function insertVerdictIfAbsent(target, v) {
  await request(target, {
    application: { type: sql.NVarChar(256), value: v.application },
    key_field: { type: sql.NVarChar(512), value: v.key_field },
    prompt_version: { type: sql.NVarChar(32), value: v.prompt_version },
    model_version: { type: sql.NVarChar(128), value: v.model_version },
    alert_schema: { type: sql.NVarChar(2), value: v.alert_schema },
    assessment: { type: sql.NVarChar(24), value: v.assessment },
    principle_id: { type: sql.NVarChar(8), value: v.principle_id },
    confidence: { type: sql.NVarChar(8), value: v.confidence },
    justification: { type: sql.NVarChar(1000), value: v.justification },
    representative_doc: { type: sql.NVarChar(sql.MAX), value: v.representative_doc },
    doc_hash: { type: sql.NVarChar(64), value: v.doc_hash },
    classified_at: { type: sql.DateTime2, value: v.classified_at },
    ruleset_version: { type: sql.NVarChar(32), value: v.ruleset_version },
    first_run_id: { type: sql.NVarChar(64), value: v.first_run_id },
  }).query(`
    IF NOT EXISTS (
      SELECT 1 FROM llm_verdicts
      WHERE application = @application AND key_field = @key_field
        AND prompt_version = @prompt_version AND model_version = @model_version
    )
    INSERT INTO llm_verdicts (
      application, key_field, prompt_version, model_version, alert_schema,
      assessment, principle_id, confidence, justification,
      representative_doc, doc_hash, classified_at, ruleset_version, first_run_id
    ) VALUES (
      @application, @key_field, @prompt_version, @model_version, @alert_schema,
      @assessment, @principle_id, @confidence, @justification,
      @representative_doc, @doc_hash, @classified_at, @ruleset_version, @first_run_id
    )
  `);
}

/**
 * @param {any} target
 * @param {Record<string, any>} p
 * @returns {Promise<void>}
 */
export async function insertPanelParseIfAbsent(target, p) {
  await request(target, {
    sql_text_hash: { type: sql.NVarChar(64), value: p.sql_text_hash },
    parser_version: { type: sql.NVarChar(32), value: p.parser_version },
    parsed_result: { type: sql.NVarChar(sql.MAX), value: p.parsed_result },
    safety_state: { type: sql.NVarChar(24), value: p.safety_state },
    unmeasured_reason: { type: sql.NVarChar(1000), value: p.unmeasured_reason },
    created_at: { type: sql.DateTime2, value: p.created_at },
  }).query(`
    IF NOT EXISTS (
      SELECT 1 FROM panel_parses
      WHERE sql_text_hash = @sql_text_hash AND parser_version = @parser_version
    )
    INSERT INTO panel_parses (
      sql_text_hash, parser_version, parsed_result, safety_state, unmeasured_reason, created_at
    ) VALUES (
      @sql_text_hash, @parser_version, @parsed_result, @safety_state, @unmeasured_reason, @created_at
    )
  `);
}

/**
 * Durable verdict lookup for one prompt/model pair.
 *
 * Section 3.3 measured that keys do not recur across days, so the real work happens
 * within a run; a cross-run hit is a bonus from overlapping windows, never a saving to
 * plan around.
 *
 * @param {any} pool
 * @param {string} promptVersion
 * @param {string} modelVersion
 * @param {Array<{application: string, keyField: string}>} keys
 * @returns {Promise<Map<string, any>>} keyed `applicationkey_field`
 */
export async function findVerdicts(pool, promptVersion, modelVersion, keys) {
  /** @type {Map<string, any>} */
  const found = new Map();
  if (keys.length === 0) return found;

  const perChunk = 500;
  for (let start = 0; start < keys.length; start += perChunk) {
    const chunk = keys.slice(start, start + perChunk);
    /** @type {Record<string, {type: any, value: unknown}>} */
    const params = {
      prompt_version: { type: sql.NVarChar(32), value: promptVersion },
      model_version: { type: sql.NVarChar(128), value: modelVersion },
    };
    const predicates = chunk.map((k, i) => {
      params[`app_${i}`] = { type: sql.NVarChar(256), value: k.application };
      params[`key_${i}`] = { type: sql.NVarChar(512), value: k.keyField };
      return `(application = @app_${i} AND key_field = @key_${i})`;
    });
    const result = await request(pool, params).query(`
      SELECT * FROM llm_verdicts
      WHERE prompt_version = @prompt_version AND model_version = @model_version
        AND (${predicates.join(' OR ')})
    `);
    for (const row of result.recordset) {
      found.set(verdictKey(row.application, row.key_field), row);
    }
  }
  return found;
}

/**
 * @param {any} pool
 * @param {string} sqlTextHash
 * @param {string} parserVersion
 * @returns {Promise<any|null>}
 */
export async function findPanelParse(pool, sqlTextHash, parserVersion) {
  const result = await request(pool, {
    hash: { type: sql.NVarChar(64), value: sqlTextHash },
    version: { type: sql.NVarChar(32), value: parserVersion },
  }).query('SELECT * FROM panel_parses WHERE sql_text_hash = @hash AND parser_version = @version');
  return result.recordset[0] ?? null;
}

// ------------------------------------------------------------------ read-back
// The report renderer uses only these: nothing is recomputed from Elasticsearch.

/**
 * @param {any} pool
 * @param {string} runId
 * @returns {Promise<any|null>}
 */
export async function getRun(pool, runId) {
  const result = await request(pool, { runId: { type: sql.NVarChar(64), value: runId } }).query(
    'SELECT * FROM runs WHERE run_id = @runId',
  );
  return result.recordset[0] ?? null;
}

/**
 * Most recent run for a team, used when a report is re-rendered without a run id.
 * @param {any} pool
 * @param {string} teamId
 * @returns {Promise<any|null>}
 */
export async function getLatestRun(pool, teamId) {
  const result = await request(pool, { teamId: { type: sql.NVarChar(128), value: teamId } }).query(
    "SELECT TOP 1 * FROM runs WHERE team_id = @teamId AND status = 'completed' ORDER BY run_at DESC",
  );
  return result.recordset[0] ?? null;
}

/**
 * @param {any} pool
 * @param {string} runId
 * @returns {Promise<any[]>}
 */
export async function getDailyMetrics(pool, runId) {
  const result = await request(pool, { runId: { type: sql.NVarChar(64), value: runId } }).query(
    'SELECT * FROM daily_metrics WHERE run_id = @runId ORDER BY alert_schema ASC, snapshot_date ASC',
  );
  return result.recordset;
}

/**
 * @param {any} pool
 * @param {string} runId
 * @returns {Promise<any[]>}
 */
export async function getRuleCounts(pool, runId) {
  const result = await request(pool, { runId: { type: sql.NVarChar(64), value: runId } }).query(`
    SELECT * FROM daily_rule_counts
    WHERE run_id = @runId
    ORDER BY alert_schema ASC, snapshot_date ASC,
             CAST(SUBSTRING(rule_id, 2, 8) AS INT) ASC
  `);
  return result.recordset;
}

/**
 * @param {any} pool
 * @param {string} runId
 * @returns {Promise<any[]>}
 */
export async function getFindings(pool, runId) {
  const result = await request(pool, { runId: { type: sql.NVarChar(64), value: runId } }).query(`
    SELECT * FROM alert_findings
    WHERE run_id = @runId
    ORDER BY alert_schema ASC, application ASC, key_field ASC
  `);
  return result.recordset;
}

/**
 * @param {any} pool
 * @param {string} runId
 * @returns {Promise<any[]>}
 */
export async function getBatchAttempts(pool, runId) {
  const result = await request(pool, { runId: { type: sql.NVarChar(64), value: runId } }).query(`
    SELECT * FROM llm_batch_attempts
    WHERE run_id = @runId
    ORDER BY batch_id ASC, attempt_number ASC
  `);
  return result.recordset;
}

/**
 * @param {any} pool
 * @param {string} runId
 * @returns {Promise<any[]>}
 */
export async function getRunPanels(pool, runId) {
  const result = await request(pool, { runId: { type: sql.NVarChar(64), value: runId } }).query(
    'SELECT * FROM run_panels WHERE run_id = @runId ORDER BY panel_id ASC',
  );
  return result.recordset;
}
