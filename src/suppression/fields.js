/**
 * The authoritative field-classification table (design section 5.2).
 *
 * The discriminator between suppression and scoping is NOT the operator — scoping
 * predicates are commonly negations too. It is what the field identifies:
 *
 * - an INSTANCE-LEVEL field points at specific alerts the team knows are junk, so a
 *   negation on it is the team's own written admission that those alerts are worthless;
 * - a CLASSIFICATION DIMENSION narrows the view, and reading `severity = 'critical'` as
 *   suppression would mark a team's entire non-critical inventory as bad alerts.
 *
 * Unknown fields are ignored and their names logged, never guessed at. That biases the
 * parse toward false negatives, which is the direction the design demands: an
 * unrecognised field quietly under-reports `suppressed`, whereas guessing could mark good
 * alerts bad — and a team only has to catch us wrong once.
 */

/** A negation on one of these is suppression. */
export const INSTANCE_FIELDS = new Set([
  'node_name',
  'message',
  'object',
  'component',
  'key_field',
  'alert_rule_url',
  // Debatable, and deliberately included: `application != 'legacy-app'` hides an entire
  // application's alerts from the team that owns them, which is the phase-0 problem in its
  // purest form. A team legitimately running one panel per application is protected by the
  // multi-panel unanimity rule rather than by this table.
  'application',
]);

/** Narrowing the view. Ignored entirely, negated or not. */
export const CLASSIFICATION_FIELDS = new Set([
  'severity',
  'environment',
  'status',
  'provider',
  'operator',
]);

/** Mechanical constructs that carry no ownership or suppression meaning. */
export const MECHANICAL_MACROS = new Set(['__timeFilter', '__timeFrom', '__timeTo', '__interval']);

/**
 * @typedef {'instance'|'classification'|'unknown'} FieldClass
 */

/**
 * @param {string} fieldName
 * @returns {FieldClass}
 */
export function classifyField(fieldName) {
  const name = fieldName.toLowerCase();
  if (INSTANCE_FIELDS.has(name)) return 'instance';
  if (CLASSIFICATION_FIELDS.has(name)) return 'classification';
  return 'unknown';
}

/**
 * Which internal record property backs a panel field name.
 *
 * `object` and `component` are the same concept under the two schemas, so a v1 panel
 * writing `object` and a v2 panel writing `component` both resolve to the record's
 * component field.
 *
 * @param {string} fieldName
 * @returns {string|null}
 */
export function recordPropertyFor(fieldName) {
  switch (fieldName.toLowerCase()) {
    case 'node_name':
      return 'nodeName';
    case 'message':
      return 'message';
    case 'object':
    case 'component':
      return 'component';
    case 'key_field':
      return 'keyField';
    case 'alert_rule_url':
      return 'alertRuleUrl';
    case 'application':
      return 'application';
    default:
      return null;
  }
}
