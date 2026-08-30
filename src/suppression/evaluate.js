import { parsePanelSql, collectLeaves, SqlParseError } from './parser.js';
import { classifyField, recordPropertyFor, MECHANICAL_MACROS } from './fields.js';
import { resolveOperand } from './variables.js';
import { sha256Text } from '../util/hash.js';
import { PARSER_VERSION } from '../versions.js';
import { logger } from '../util/logger.js';

/**
 * Panel-suppression measurement (design section 5.2; flow step 5).
 *
 * Its entire job is one thing: find the predicates by which a team deliberately filters
 * its own alerts out of its own panel, and mark those alerts under core rule 5.
 *
 * This step feeds `flagged`, so a scoping predicate misread as suppression would mark
 * good alerts as bad — the single most expensive error this design can make. Every
 * ambiguity therefore resolves to `unmeasured` rather than to a suppression.
 */

/** A suppression leaf may not reach further than this share of the team's owned rows. */
export const BLAST_RADIUS_LIMIT = 0.5;

/**
 * @typedef {import('../domain/normalize.js').AlertRecord} AlertRecord
 * @typedef {import('../registry/registry.js').Panel} Panel
 */

/**
 * @typedef {object} LeafOutcome
 * @property {'suppression'|'ignored'|'unmeasured'} kind
 * @property {string} [field]
 * @property {string} [operator]
 * @property {string} [reason]
 * @property {string[]} [values]
 * @property {number} [matchedRows]
 */

/**
 * @typedef {object} PanelInterpretation
 * @property {string} panelId
 * @property {'v1'|'v2'} schema
 * @property {string} sqlTextHash
 * @property {string} parserVersion
 * @property {'parsed'|'unparseable'} safetyState
 * @property {string|null} unmeasuredReason
 * @property {LeafOutcome[]} leaves
 * @property {string[]} unknownFields
 */

/**
 * Convert a SQL LIKE pattern to an anchored regular expression.
 *
 * Matching is case-SENSITIVE. A panel runs against SQL Server, whose default collation is
 * case-insensitive, but both sides of this comparison are written by the same team and
 * match exactly in practice. Case-insensitive matching could only ever widen the
 * suppression set, and widening is the direction that marks good alerts bad.
 *
 * @param {string} pattern
 * @returns {RegExp}
 */
export function likeToRegExp(pattern) {
  let out = '';
  for (let i = 0; i < pattern.length; i++) {
    const ch = pattern[i];
    if (ch === '%') out += '[\\s\\S]*';
    else if (ch === '_') out += '[\\s\\S]';
    else out += ch.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${out}$`);
}

/**
 * Does a leaf exclude this row from the panel?
 *
 * Deliberately positive-match semantics: a leaf excludes a row only when the row's value
 * actually equals (or LIKE-matches) the value the team wrote. Strict SQL three-valued
 * logic would also filter out rows whose field is NULL — `node_name != 'X'` is UNKNOWN
 * when node_name is NULL — but that is an artefact of NULL handling, not a team's
 * admission that the alert is worthless, and treating it as one would flag every
 * node-less alert of every team that writes a single node exclusion.
 *
 * @param {LeafOutcome & {property: string, values: string[], operator: string}} leaf
 * @param {AlertRecord} row
 * @returns {boolean}
 */
function leafExcludesRow(leaf, row) {
  const raw = /** @type {any} */ (row)[leaf.property];
  if (typeof raw !== 'string') return false;

  if (leaf.operator === 'NOT LIKE') {
    return leaf.values.some((pattern) => likeToRegExp(pattern).test(raw));
  }
  return leaf.values.includes(raw);
}

/**
 * Interpret one panel's SQL into suppression leaves.
 *
 * @param {Panel} panel
 * @returns {PanelInterpretation}
 */
export function interpretPanel(panel) {
  const sqlTextHash = sha256Text(panel.sql);
  const definitions = panel.variables || [];

  /** @type {PanelInterpretation} */
  const interpretation = {
    panelId: panel.panel_id,
    schema: panel.schema,
    sqlTextHash,
    parserVersion: PARSER_VERSION,
    safetyState: 'parsed',
    unmeasuredReason: null,
    leaves: [],
    unknownFields: [],
  };

  /** @type {{where: any|null}} */
  let parsed;
  try {
    parsed = parsePanelSql(panel.sql);
  } catch (err) {
    // An unparseable panel is not a suppression finding. A team with no panel, an
    // unparseable panel, or a panel nobody can find gets null visibility and the run
    // completes normally.
    interpretation.safetyState = 'unparseable';
    interpretation.unmeasuredReason =
      err instanceof SqlParseError ? err.message : 'panel SQL could not be parsed';
    return interpretation;
  }

  if (!parsed.where) return interpretation;

  for (const { leaf, nested } of collectLeaves(parsed.where)) {
    interpretation.leaves.push(interpretLeaf(leaf, nested, definitions, interpretation));
  }
  return interpretation;
}

/**
 * @param {any} leaf
 * @param {boolean} nested
 * @param {import('../registry/registry.js').PanelVariable[]} definitions
 * @param {PanelInterpretation} interpretation
 * @returns {LeafOutcome}
 */
function interpretLeaf(leaf, nested, definitions, interpretation) {
  // Mechanical constructs carry no ownership or suppression meaning.
  if (leaf.type === 'call') {
    if (leaf.macro && MECHANICAL_MACROS.has(leaf.name)) {
      return { kind: 'ignored', reason: `mechanical macro $${leaf.name}` };
    }
    return { kind: 'ignored', reason: `function call ${leaf.name}() is not interpreted` };
  }

  const fieldOperand = leaf.field;
  if (!fieldOperand || fieldOperand.kind !== 'field') {
    return { kind: 'ignored', reason: 'predicate does not compare a plain column' };
  }

  const fieldName = fieldOperand.name;
  const fieldClass = classifyField(fieldName);

  if (fieldClass === 'classification') {
    // Scoping, not suppression - and this distinction is the one that cannot be dropped.
    return { kind: 'ignored', field: fieldName, reason: 'classification dimension' };
  }

  if (fieldClass === 'unknown') {
    if (!interpretation.unknownFields.includes(fieldName)) {
      interpretation.unknownFields.push(fieldName);
    }
    logger.info('suppression.unknown_field', {
      panel_id: interpretation.panelId,
      field: fieldName,
    });
    return { kind: 'ignored', field: fieldName, reason: 'unknown field, ignored and logged' };
  }

  // Instance-level field: only a NEGATION is suppression.
  const negation = negationOf(leaf);
  if (!negation) {
    return { kind: 'ignored', field: fieldName, reason: 'instance field, but not a negation' };
  }

  if (nested) {
    // Rewrite safety: a leaf inside an OR or a NOT cannot be lifted out without changing
    // the query's meaning, so it is present but unmeasured.
    return {
      kind: 'unmeasured',
      field: fieldName,
      operator: negation.operator,
      reason: 'suppression leaf is nested inside OR/NOT and cannot be evaluated safely',
    };
  }

  /** @type {string[]} */
  const values = [];
  for (const operand of negation.operands) {
    const resolved = resolveOperand(operand, definitions);
    if (!resolved.resolved) {
      return {
        kind: 'unmeasured',
        field: fieldName,
        operator: negation.operator,
        reason: resolved.reason ?? 'operand could not be resolved',
      };
    }
    values.push(...resolved.values);
  }

  if (values.length === 0) {
    return {
      kind: 'unmeasured',
      field: fieldName,
      operator: negation.operator,
      reason: 'negation resolved to no values',
    };
  }

  const property = recordPropertyFor(fieldName);
  if (!property) {
    return {
      kind: 'unmeasured',
      field: fieldName,
      operator: negation.operator,
      reason: 'instance field has no corresponding alert attribute',
    };
  }

  return {
    kind: 'suppression',
    field: fieldName,
    operator: negation.operator,
    values,
    // `property` is carried for evaluation; it is not part of the published shape.
    ...{ property },
  };
}

/**
 * Recognize the negation forms and return the operands they exclude.
 * @param {any} leaf
 * @returns {{operator: string, operands: any[]}|null}
 */
function negationOf(leaf) {
  if (leaf.type === 'comparison' && (leaf.operator === '!=' || leaf.operator === '<>')) {
    return { operator: leaf.operator, operands: [leaf.value] };
  }
  if (leaf.type === 'in' && leaf.negated) {
    return { operator: 'NOT IN', operands: leaf.values };
  }
  if (leaf.type === 'like' && leaf.negated) {
    return { operator: 'NOT LIKE', operands: [leaf.pattern] };
  }
  return null;
}

/**
 * Evaluate suppression for one schema's owned rows against the team's panels.
 *
 * Multi-panel semantics: a row counts as suppressed only if EVERY applicable panel
 * excludes it. A row filtered out of one panel but visible in another is not hidden from
 * the team, and flagging it would be a false positive of exactly the kind this section
 * warns about. Rows are counted once regardless of how many panels exclude them.
 *
 * @param {AlertRecord[]} rows Owned rows of a single schema.
 * @param {Panel[]} panels The team's panels for that schema.
 * @returns {{
 *   suppressedRows: Set<AlertRecord>,
 *   unmeasuredLeaves: number,
 *   interpretations: PanelInterpretation[],
 *   notes: string[]
 * }}
 */
export function evaluateSuppression(rows, panels) {
  /** @type {string[]} */
  const notes = [];
  /** @type {PanelInterpretation[]} */
  const interpretations = panels.map((panel) => interpretPanel(panel));
  let unmeasuredLeaves = 0;

  // Per panel, the set of rows that panel excludes.
  /** @type {Array<Set<AlertRecord>>} */
  const excludedPerPanel = [];

  for (const interpretation of interpretations) {
    /** @type {Set<AlertRecord>} */
    const excluded = new Set();

    if (interpretation.safetyState === 'unparseable') {
      unmeasuredLeaves += 1;
      notes.push(`panel ${interpretation.panelId}: ${interpretation.unmeasuredReason}`);
      // An unparseable panel cannot be shown to exclude anything, and unanimity requires
      // every panel to exclude a row, so it contributes an empty set - which correctly
      // prevents any row from being unanimously suppressed on incomplete evidence.
      excludedPerPanel.push(excluded);
      continue;
    }

    for (const leaf of interpretation.leaves) {
      if (leaf.kind === 'unmeasured') {
        unmeasuredLeaves += 1;
        notes.push(`panel ${interpretation.panelId}: ${leaf.field} - ${leaf.reason}`);
        continue;
      }
      if (leaf.kind !== 'suppression') continue;

      const typed = /** @type {any} */ (leaf);
      /** @type {AlertRecord[]} */
      const matched = rows.filter((row) => leafExcludesRow(typed, row));

      // Blast-radius guard: a multi-value variable expanding to everything turns
      // `node_name != '$nodes'` into an exclusion of the team's entire inventory, and
      // marking half a team's alerts bad on a parse artefact is the most expensive error
      // available here. No legitimate suppression clause has that reach.
      if (rows.length > 0 && matched.length / rows.length > BLAST_RADIUS_LIMIT) {
        leaf.kind = 'unmeasured';
        leaf.reason = `blast radius ${matched.length}/${rows.length} exceeds ${BLAST_RADIUS_LIMIT * 100}% of owned rows; routed to human review`;
        leaf.matchedRows = matched.length;
        unmeasuredLeaves += 1;
        notes.push(`panel ${interpretation.panelId}: ${leaf.field} - ${leaf.reason}`);
        continue;
      }

      leaf.matchedRows = matched.length;
      for (const row of matched) excluded.add(row);
    }

    excludedPerPanel.push(excluded);
  }

  /** @type {Set<AlertRecord>} */
  const suppressedRows = new Set();
  if (excludedPerPanel.length > 0) {
    // Unanimity: intersect across panels.
    const [first, ...rest] = excludedPerPanel;
    for (const row of first) {
      if (rest.every((set) => set.has(row))) suppressedRows.add(row);
    }
  }

  return { suppressedRows, unmeasuredLeaves, interpretations, notes };
}

/**
 * Build the R5 findings for the suppressed rows.
 *
 * @param {Set<AlertRecord>} suppressedRows
 * @param {Panel[]} panels
 * @returns {Map<AlertRecord, import('../rules/core.js').Finding>}
 */
export function buildR5Findings(suppressedRows, panels) {
  /** @type {Map<AlertRecord, import('../rules/core.js').Finding>} */
  const findings = new Map();
  const panelIds = panels.map((p) => p.panel_id);
  for (const row of suppressedRows) {
    findings.set(row, {
      ruleId: 'R5',
      set: 'core',
      evidence: { panels: panelIds, reason: 'excluded by every supplied panel for this schema' },
    });
  }
  return findings;
}
