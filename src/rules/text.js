/**
 * Text normalization shared by the deterministic rules (design section 4).
 *
 * Two distinct normalizations exist and they are not interchangeable:
 *
 * - `normalizeMessage` (R1, R2, R10) trims, lowercases, collapses repeated whitespace and
 *   removes surrounding punctuation, then matches the COMPLETE value against a catalogue.
 * - `normalizeFieldValue` (R3, R8, R9 placeholders) trims, lowercases and collapses
 *   whitespace but does NOT strip punctuation, because `n/a` is itself a catalogue value.
 *
 * Matching is always whole-value equality. Substring matching is explicitly rejected:
 * `backup completed with 10 failures` is not a heartbeat, and `test-payments-service` is
 * not a placeholder. Message length alone never flags an alert.
 */

/** Unicode punctuation at either end of the value. */
const SURROUNDING_PUNCTUATION = /^\p{P}+|\p{P}+$/gu;

/**
 * @param {unknown} value
 * @returns {string|null} null when the value is not a string
 */
function asString(value) {
  return typeof value === 'string' ? value : null;
}

/**
 * Trim, lowercase and collapse repeated whitespace.
 * @param {unknown} value
 * @returns {string|null}
 */
export function normalizeFieldValue(value) {
  const s = asString(value);
  if (s === null) return null;
  return s.trim().toLowerCase().replace(/\s+/g, ' ');
}

/**
 * Field normalization plus removal of surrounding punctuation.
 *
 * Stripping punctuation can expose whitespace that was sitting inside it
 * (`" error occurred ."`), so the value is trimmed once more afterwards.
 *
 * @param {unknown} value
 * @returns {string|null}
 */
export function normalizeMessage(value) {
  const s = normalizeFieldValue(value);
  if (s === null) return null;
  return s.replace(SURROUNDING_PUNCTUATION, '').trim();
}

/**
 * Is a value absent for rule purposes: not a string, or empty/whitespace-only?
 * @param {unknown} value
 * @returns {boolean}
 */
export function isBlank(value) {
  const s = asString(value);
  return s === null || s.trim() === '';
}
