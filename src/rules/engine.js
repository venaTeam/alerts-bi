import { evaluateCoreRules } from './core.js';
import { evaluateReadinessRules } from './readiness.js';
import { selectRepresentative } from '../domain/normalize.js';
import { CORE_RULE_IDS, V2_READINESS_RULE_IDS } from './catalogs.js';

/**
 * Rule execution grain and aggregation (design section 4; flow step 4).
 *
 * Core rules are evaluated on EVERY raw row, then aggregated to alert identity:
 *
 * - `count` per rule is the number of rows that actually match.
 * - `distinct_count` per rule is the number of identities with at least one matching row.
 * - Findings stay attached only to the rows that matched. They are never projected onto
 *   other rows sharing an identity, because that would move a finding onto a date where
 *   nothing was wrong.
 *
 * LLM eligibility is decided at IDENTITY level after that row-level pass: any core
 * finding anywhere in the window withholds the whole identity. That deliberately accepts
 * losing a second, advisory judgment for the alert — a deterministic finding already
 * gives the team a concrete fix, whereas evaluating only the representative could miss a
 * row-specific R5 suppression or R7 timestamp failure.
 */

/**
 * @typedef {import('../domain/normalize.js').AlertRecord} AlertRecord
 * @typedef {import('./core.js').Finding} Finding
 */

/**
 * @typedef {object} EvaluatedRow
 * @property {AlertRecord} row
 * @property {Finding[]} coreFindings
 * @property {Finding[]} readinessFindings
 */

/**
 * @typedef {object} EvaluatedIdentity
 * @property {string} identity
 * @property {'v1'|'v2'} schema
 * @property {string} application
 * @property {string} keyField
 * @property {AlertRecord} representative
 * @property {EvaluatedRow[]} rows
 * @property {string[]} coreRuleIds Distinct core rule ids matched anywhere in the window.
 * @property {string[]} readinessRuleIds Distinct readiness rule ids on the representative.
 * @property {boolean} hasCoreFinding
 * @property {boolean} llmEligible
 * @property {Set<string>} presentDates UTC dates this identity appears on.
 */

/**
 * @typedef {object} RuleBucketCount
 * @property {string} snapshotDate
 * @property {string} ruleId
 * @property {number} count Matching raw rows in this bucket.
 * @property {number} distinctCount Identities with at least one matching row in this bucket.
 */

/**
 * Order rule ids R1, R2, ... R10 numerically rather than lexically, so R10 does not sort
 * between R1 and R2 in reports.
 * @param {string} a
 * @param {string} b
 * @returns {number}
 */
export function compareRuleIds(a, b) {
  const na = Number(a.slice(1));
  const nb = Number(b.slice(1));
  if (Number.isFinite(na) && Number.isFinite(nb) && a[0] === b[0]) return na - nb;
  return a < b ? -1 : a > b ? 1 : 0;
}

/**
 * Evaluate every row of one schema and aggregate to identities.
 *
 * @param {AlertRecord[]} rows Rows of a single schema.
 * @returns {{rows: EvaluatedRow[], identities: Map<string, EvaluatedIdentity>}}
 */
export function evaluateRows(rows) {
  /** @type {EvaluatedRow[]} */
  const evaluated = rows.map((row) => ({
    row,
    coreFindings: evaluateCoreRules(row),
    readinessFindings: evaluateReadinessRules(row),
  }));

  /** @type {Map<string, EvaluatedRow[]>} */
  const grouped = new Map();
  for (const e of evaluated) {
    const bucket = grouped.get(e.row.identity);
    if (bucket) bucket.push(e);
    else grouped.set(e.row.identity, [e]);
  }

  /** @type {Map<string, EvaluatedIdentity>} */
  const identities = new Map();
  for (const [identity, group] of grouped) {
    const representative = selectRepresentative(group.map((e) => e.row));

    /** @type {Set<string>} */
    const coreRuleIds = new Set();
    for (const e of group) for (const f of e.coreFindings) coreRuleIds.add(f.ruleId);

    // Readiness is a property of the identity's current state, so it is read off the
    // representative rather than unioned over history: an alert enriched yesterday is
    // ready today, and a gap it used to have is not a gap now.
    const representativeEval = group.find((e) => e.row === representative);
    const readinessRuleIds = (representativeEval?.readinessFindings || []).map((f) => f.ruleId);

    /** @type {Set<string>} */
    const presentDates = new Set(group.map((e) => e.row.snapshotDate));

    const hasCoreFinding = coreRuleIds.size > 0;
    identities.set(identity, {
      identity,
      schema: representative.schema,
      application: representative.application,
      keyField: representative.keyField,
      representative,
      rows: group,
      coreRuleIds: [...coreRuleIds].sort(compareRuleIds),
      readinessRuleIds: readinessRuleIds.sort(compareRuleIds),
      hasCoreFinding,
      llmEligible: !hasCoreFinding,
      presentDates,
    });
  }

  return { rows: evaluated, identities };
}

/**
 * Attach externally-derived findings (R5 from the suppression evaluator) to already
 * evaluated rows, then recompute the identity-level aggregates that depend on them.
 *
 * R5 arrives late because suppression needs the team's panels and the complete owned row
 * set, which a single-row rule cannot see. It is a core finding like any other, so it
 * must be able to withhold an identity from the LLM.
 *
 * @param {{rows: EvaluatedRow[], identities: Map<string, EvaluatedIdentity>}} evaluation
 * @param {Map<AlertRecord, Finding>} findingsByRow
 * @returns {void}
 */
export function attachRowFindings(evaluation, findingsByRow) {
  if (findingsByRow.size === 0) return;

  for (const e of evaluation.rows) {
    const finding = findingsByRow.get(e.row);
    if (!finding) continue;
    if (finding.set === 'core') e.coreFindings.push(finding);
    else e.readinessFindings.push(finding);
  }

  for (const identity of evaluation.identities.values()) {
    /** @type {Set<string>} */
    const coreRuleIds = new Set();
    for (const e of identity.rows) for (const f of e.coreFindings) coreRuleIds.add(f.ruleId);
    identity.coreRuleIds = [...coreRuleIds].sort(compareRuleIds);
    identity.hasCoreFinding = coreRuleIds.size > 0;
    identity.llmEligible = !identity.hasCoreFinding;
  }
}

/**
 * Per-rule daily counts.
 *
 * A deterministic match is attributed to the UTC bucket containing the raw row that
 * matched. `distinctCount` counts an identity once in each bucket where at least one of
 * its rows matched — not once per window, and not on dates where it did not match.
 *
 * Readiness rules R8-R10 are counted here too, so the report can show the gap breakdown,
 * but they are excluded from `flagged_by_rule` below.
 *
 * @param {EvaluatedRow[]} evaluatedRows
 * @param {string[]} snapshotDates Every bucket date in the window, ascending.
 * @returns {RuleBucketCount[]}
 */
export function computeDailyRuleCounts(evaluatedRows, snapshotDates) {
  /** @type {Map<string, {count: number, identities: Set<string>}>} keyed by date and rule */
  const acc = new Map();

  for (const e of evaluatedRows) {
    const date = e.row.snapshotDate;
    for (const finding of [...e.coreFindings, ...e.readinessFindings]) {
      const key = `${date}|${finding.ruleId}`;
      let entry = acc.get(key);
      if (!entry) {
        entry = { count: 0, identities: new Set() };
        acc.set(key, entry);
      }
      entry.count += 1;
      entry.identities.add(e.row.identity);
    }
  }

  /** @type {RuleBucketCount[]} */
  const out = [];
  for (const snapshotDate of snapshotDates) {
    for (const ruleId of [...CORE_RULE_IDS, ...V2_READINESS_RULE_IDS]) {
      const entry = acc.get(`${snapshotDate}|${ruleId}`);
      if (!entry) continue;
      out.push({
        snapshotDate,
        ruleId,
        count: entry.count,
        distinctCount: entry.identities.size,
      });
    }
  }
  return out;
}

/**
 * `flagged_by_rule` and `flagged_by_rule_distinct` per bucket.
 *
 * The union of rows with at least one CORE finding, and the count of identities with at
 * least one core match in that bucket. Readiness gaps are excluded by construction.
 *
 * @param {EvaluatedRow[]} evaluatedRows
 * @param {string[]} snapshotDates
 * @returns {Map<string, {flaggedByRule: number, flaggedByRuleDistinct: number}>}
 */
export function computeDailyFlagged(evaluatedRows, snapshotDates) {
  /** @type {Map<string, {rows: number, identities: Set<string>}>} */
  const acc = new Map();
  for (const date of snapshotDates) acc.set(date, { rows: 0, identities: new Set() });

  for (const e of evaluatedRows) {
    if (e.coreFindings.length === 0) continue;
    const entry = acc.get(e.row.snapshotDate);
    if (!entry) continue;
    entry.rows += 1;
    entry.identities.add(e.row.identity);
  }

  /** @type {Map<string, {flaggedByRule: number, flaggedByRuleDistinct: number}>} */
  const out = new Map();
  for (const [date, entry] of acc) {
    out.set(date, { flaggedByRule: entry.rows, flaggedByRuleDistinct: entry.identities.size });
  }
  return out;
}

/**
 * Count of v2 identities carrying at least one readiness gap on their representative.
 * @param {Iterable<EvaluatedIdentity>} identities
 * @returns {number}
 */
export function countPhase2GapIdentities(identities) {
  let n = 0;
  for (const identity of identities) {
    if (identity.schema === 'v2' && identity.readinessRuleIds.length > 0) n += 1;
  }
  return n;
}
