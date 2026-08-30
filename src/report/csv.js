/**
 * CSV export (design section 6, "Exact MVP output contract").
 *
 * Exactly three files: `daily_metrics.csv`, `rule_counts.csv` and `alert_worklist.csv`.
 * Ordering is deterministic, CSV carries full values even where the HTML shortens display
 * text, and spreadsheet-formula prefixes are neutralized.
 */

/** Cells beginning with these are interpreted as formulas by spreadsheet software. */
const FORMULA_PREFIXES = ['=', '+', '-', '@'];

/**
 * Render one CSV cell.
 *
 * A cell whose text begins with `=`, `+`, `-` or `@` is prefixed with a single quote:
 * alert messages and node names are free text written by other teams, and a value such as
 * `=cmd|' /c calc'!A0` reaching a spreadsheet is a code-execution path, not a formatting
 * quirk. The escape is visible in the cell rather than silently altering the value.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function csvCell(value) {
  if (value === null || value === undefined) return '';
  let text = value instanceof Date ? value.toISOString() : String(value);

  if (FORMULA_PREFIXES.includes(text[0])) text = `'${text}`;

  if (/[",\r\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

/**
 * Render rows as CSV text with CRLF line endings (RFC 4180).
 *
 * @param {string[]} headers
 * @param {Array<Array<unknown>>} rows
 * @returns {string}
 */
export function toCsv(headers, rows) {
  const lines = [headers.map((h) => csvCell(h)).join(',')];
  for (const row of rows) lines.push(row.map((cell) => csvCell(cell)).join(','));
  return `${lines.join('\r\n')}\r\n`;
}

/**
 * @param {unknown} value
 * @returns {string}
 */
function isoDate(value) {
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  return String(value ?? '').slice(0, 10);
}

/**
 * @param {unknown} value
 * @returns {string}
 */
function isoInstant(value) {
  return value instanceof Date ? value.toISOString() : String(value ?? '');
}

/**
 * `daily_metrics.csv` - one row per schema and UTC date.
 *
 * @param {any[]} dailyMetrics Rows read back from SQL, already ordered.
 * @returns {string}
 */
export function dailyMetricsCsv(dailyMetrics) {
  const headers = [
    'run_id',
    'team_id',
    'schema',
    'snapshot_date',
    'bucket_start',
    'bucket_end',
    'covered_hours',
    'alerts',
    'distinct_alerts',
    'alerts_per_hour',
    'node_name_numerator',
    'node_name_denominator',
    'node_name_ratio',
    'key_inflation_numerator',
    'key_inflation_denominator',
    'key_inflation_ratio',
    'flagged_by_rule',
    'flagged_by_rule_distinct',
    'flagged_by_llm',
    'flagged_by_llm_distinct',
    'needs_review',
    'assessed_good',
    'unassessed',
    'phase2_gaps',
    'suppressed',
    'suppression_unmeasured',
  ];
  const rows = dailyMetrics.map((r) => [
    r.run_id,
    r.team_id,
    r.alert_schema,
    isoDate(r.snapshot_date),
    isoInstant(r.bucket_start),
    isoInstant(r.bucket_end),
    r.covered_hours,
    r.alerts,
    r.distinct_alerts,
    r.alerts_per_hour,
    r.node_name_numerator,
    r.node_name_denominator,
    r.node_name_ratio,
    r.key_inflation_numerator,
    r.key_inflation_denominator,
    r.key_inflation_ratio,
    r.flagged_by_rule,
    r.flagged_by_rule_distinct,
    r.flagged_by_llm,
    r.flagged_by_llm_distinct,
    r.needs_review,
    r.assessed_good,
    r.unassessed,
    r.phase2_gaps,
    r.suppressed,
    r.suppression_unmeasured,
  ]);
  return toCsv(headers, rows);
}

/**
 * `rule_counts.csv` - the per-rule daily breakdown that `flagged` drills into.
 *
 * @param {any[]} ruleCounts
 * @returns {string}
 */
export function ruleCountsCsv(ruleCounts) {
  const headers = [
    'run_id',
    'team_id',
    'schema',
    'snapshot_date',
    'rule_id',
    'ruleset_version',
    'match_count',
    'distinct_count',
  ];
  const rows = ruleCounts.map((r) => [
    r.run_id,
    r.team_id,
    r.alert_schema,
    isoDate(r.snapshot_date),
    r.rule_id,
    r.ruleset_version,
    r.match_count,
    r.distinct_count,
  ]);
  return toCsv(headers, rows);
}

/**
 * `alert_worklist.csv` - one row per distinct identity, never divided by seven.
 *
 * This is the concrete list of what to fix that the BI promises each measured team, so it
 * carries full untruncated values.
 *
 * @param {any[]} findings
 * @returns {string}
 */
export function alertWorklistCsv(findings) {
  const headers = [
    'run_id',
    'schema',
    'application',
    'key_field',
    'quality_state',
    'core_rule_ids',
    'readiness_rule_ids',
    'llm_principle_id',
    'llm_confidence',
    'llm_justification',
    'unassessed_reason',
    'row_count',
    'first_seen',
    'last_seen',
    'severity',
    'component',
    'node_name',
    'environment',
    'provider',
    'alert_rule_url',
    'message',
  ];
  const rows = findings.map((r) => [
    r.run_id,
    r.alert_schema,
    r.application,
    r.key_field,
    r.quality_state,
    r.core_rule_ids,
    r.readiness_rule_ids,
    r.llm_principle_id,
    r.llm_confidence,
    r.llm_justification,
    r.unassessed_reason,
    r.row_count,
    isoInstant(r.first_seen),
    isoInstant(r.last_seen),
    r.severity,
    r.component,
    r.node_name,
    r.environment,
    r.provider,
    r.alert_rule_url,
    r.message,
  ]);
  return toCsv(headers, rows);
}
