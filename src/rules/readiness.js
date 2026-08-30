import { normalizeMessage, normalizeFieldValue } from './text.js';
import { PLACEHOLDER_VALUES, R10_TECHNICAL_CAUSE_IMPACTS } from './catalogs.js';

/**
 * V2 phase-2 readiness gaps R8-R10 (design section 4; flow step 4).
 *
 * These are a readiness number, not a quality number. They stay out of `flagged`, and
 * they never withhold an identity from the LLM: a missing `impact` is a phase-2 gap, but
 * the alert must still be assessed for core problems such as being informational.
 *
 * Folding them into `flagged` before `impact` and `runbook_url` are mandatory would make
 * a team that migrated correctly appear to regress (design section 3.6).
 */

/**
 * @typedef {import('../domain/normalize.js').AlertRecord} AlertRecord
 * @typedef {import('./core.js').Finding} Finding
 */

/**
 * R8 — missing or unusable `impact`.
 *
 * A present but poor impact such as `high cpu` is NOT R8; that is R10's job, or the LLM's
 * under principle P9.
 *
 * @param {AlertRecord} row
 * @returns {Finding|null}
 */
export function evaluateR8(row) {
  if (row.schema !== 'v2') return null;
  const impact = row.impact;

  if (impact === undefined || impact === null) {
    return { ruleId: 'R8', set: 'v2_readiness', evidence: { reason: 'missing' } };
  }
  if (typeof impact !== 'string') {
    return {
      ruleId: 'R8',
      set: 'v2_readiness',
      evidence: { reason: 'not_a_string', type: typeof impact },
    };
  }
  if (impact.trim() === '') {
    return { ruleId: 'R8', set: 'v2_readiness', evidence: { reason: 'empty' } };
  }
  const normalized = normalizeFieldValue(impact);
  if (normalized !== null && PLACEHOLDER_VALUES.includes(normalized)) {
    return { ruleId: 'R8', set: 'v2_readiness', evidence: { reason: 'placeholder', normalized } };
  }
  return null;
}

/**
 * Is a value a valid absolute HTTP(S) URL?
 *
 * The URL parser accepts many schemes, so the protocol is checked explicitly: a
 * `mailto:` or `file:` runbook is not something an on-call engineer can open from the
 * alert.
 *
 * @param {string} value
 * @returns {boolean}
 */
function isAbsoluteHttpUrl(value) {
  /** @type {URL} */
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return false;
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false;
  // `http:///path` parses but names no host, so it cannot be opened.
  return parsed.host !== '';
}

/**
 * R9 — missing or invalid absolute HTTP(S) `runbook_url`.
 *
 * Reported as a readiness gap for every severity. Severity only changes what it means for
 * phase completion: a `critical` match is a mandatory completion failure, while `high` and
 * `warning` stay visible without blocking completion under the current
 * 100%-on-critical criterion.
 *
 * @param {AlertRecord} row
 * @returns {Finding|null}
 */
export function evaluateR9(row) {
  if (row.schema !== 'v2') return null;
  const url = row.runbookUrl;
  const severity = normalizeFieldValue(row.severity);
  const base = { severity: row.severity ?? null, blocks_completion: severity === 'critical' };

  if (url === undefined || url === null) {
    return { ruleId: 'R9', set: 'v2_readiness', evidence: { ...base, reason: 'missing' } };
  }
  if (typeof url !== 'string') {
    return {
      ruleId: 'R9',
      set: 'v2_readiness',
      evidence: { ...base, reason: 'not_a_string', type: typeof url },
    };
  }
  if (url.trim() === '') {
    return { ruleId: 'R9', set: 'v2_readiness', evidence: { ...base, reason: 'empty' } };
  }
  const normalized = normalizeFieldValue(url);
  if (normalized !== null && PLACEHOLDER_VALUES.includes(normalized)) {
    return {
      ruleId: 'R9',
      set: 'v2_readiness',
      evidence: { ...base, reason: 'placeholder', normalized },
    };
  }
  if (!isAbsoluteHttpUrl(url.trim())) {
    return {
      ruleId: 'R9',
      set: 'v2_readiness',
      evidence: { ...base, reason: 'not_absolute_http' },
    };
  }
  return null;
}

/**
 * R10 — `impact` exactly matches the technical-cause catalogue.
 *
 * Deliberately narrow, and deliberately duplicated by LLM principle P9: R10 is the regex
 * proxy, P9 is the judgment. `high cpu causes checkout latency` does not match and
 * continues to the model.
 *
 * @param {AlertRecord} row
 * @returns {Finding|null}
 */
export function evaluateR10(row) {
  if (row.schema !== 'v2') return null;
  if (typeof row.impact !== 'string') return null;
  const normalized = normalizeMessage(row.impact);
  if (normalized === null || !R10_TECHNICAL_CAUSE_IMPACTS.includes(normalized)) return null;
  return { ruleId: 'R10', set: 'v2_readiness', evidence: { field: 'impact', normalized } };
}

/**
 * Evaluate every v2 readiness rule for one row.
 * @param {AlertRecord} row
 * @returns {Finding[]}
 */
export function evaluateReadinessRules(row) {
  /** @type {Finding[]} */
  const findings = [];
  for (const rule of [evaluateR8, evaluateR9, evaluateR10]) {
    const finding = rule(row);
    if (finding) findings.push(finding);
  }
  return findings;
}

/**
 * Is a v2 identity ready for phase-2 completion?
 *
 * Evaluated on the identity's most recent representative row. Ready means no R8 gap, no
 * R10 match, and — only when `severity = critical` — no R9 gap. A missing runbook on
 * `high` or `warning` stays visible in `phase2_gaps` and on the work list but does not
 * reduce the completion percentage.
 *
 * @param {AlertRecord} representative
 * @returns {boolean}
 */
export function isCompletionReady(representative) {
  if (representative.schema !== 'v2') {
    throw new TypeError('phase-2 readiness is only defined for v2 identities');
  }
  if (evaluateR8(representative)) return false;
  if (evaluateR10(representative)) return false;
  const severity = normalizeFieldValue(representative.severity);
  if (severity === 'critical' && evaluateR9(representative)) return false;
  return true;
}

/**
 * `phase2_readiness_pct` — completion-ready v2 identities over all distinct v2 identities.
 *
 * `null` when there are no v2 identities: zero percent would assert that a team failed a
 * measurement that was never taken.
 *
 * @param {AlertRecord[]} v2Representatives One representative per distinct v2 identity.
 * @returns {number|null}
 */
export function phase2ReadinessPct(v2Representatives) {
  if (v2Representatives.length === 0) return null;
  const ready = v2Representatives.filter((r) => isCompletionReady(r)).length;
  return (ready / v2Representatives.length) * 100;
}
