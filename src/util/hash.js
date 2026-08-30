import { createHash } from 'node:crypto';

/**
 * Deterministic, unambiguous encoding of a value for hashing.
 *
 * JSON.stringify is not usable directly: object key order is insertion order, so two
 * logically identical objects can serialize differently. Every hash in this codebase is
 * a stable identifier that must survive process restarts and code reordering, so keys are
 * sorted and every scalar is length-prefixed to remove concatenation ambiguity
 * ("ab"+"c" must not encode the same as "a"+"bc").
 *
 * @param {unknown} value
 * @returns {string}
 */
export function canonicalEncode(value) {
  if (value === null) return 'n';
  if (value === undefined) return 'u';
  const t = typeof value;
  if (typeof value === 'boolean') return value ? 'b1' : 'b0';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new TypeError(`cannot encode non-finite number: ${value}`);
    // Normalize -0 to 0 so two arithmetically equal numbers always encode alike.
    const n = Object.is(value, -0) ? 0 : value;
    const s = String(n);
    return `d${s.length}:${s}`;
  }
  if (typeof value === 'string') return `s${Buffer.byteLength(value, 'utf8')}:${value}`;
  if (Array.isArray(value)) {
    return `a${value.length}:${value.map((v) => canonicalEncode(v)).join('')}`;
  }
  if (t === 'object') {
    const keys = Object.keys(/** @type {object} */ (value)).sort();
    const parts = keys.map(
      (k) => `${canonicalEncode(k)}${canonicalEncode(/** @type {any} */ (value)[k])}`,
    );
    return `o${keys.length}:${parts.join('')}`;
  }
  throw new TypeError(`cannot encode value of type ${t}`);
}

/**
 * SHA-256 of the canonical encoding of a value, as lowercase hex.
 * @param {unknown} value
 * @returns {string}
 */
export function sha256Of(value) {
  return createHash('sha256').update(canonicalEncode(value), 'utf8').digest('hex');
}

/**
 * SHA-256 of raw text exactly as supplied. Used where the bytes themselves are the
 * identity (registry file contents, serialized LLM payloads, panel SQL text).
 * @param {string} text
 * @returns {string}
 */
export function sha256Text(text) {
  return createHash('sha256').update(text, 'utf8').digest('hex');
}
