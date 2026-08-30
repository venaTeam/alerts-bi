/**
 * Structured console logger.
 *
 * Deliberately narrow: design section "Configuration and security" forbids alert
 * documents, credentials and complete LLM payloads in ordinary logs. Log identifiers,
 * hashes, counts, timings and redacted errors only. Audit payloads belong in SQL.
 */

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };
const configured = (process.env.LOG_LEVEL || 'info').toLowerCase();
const threshold = LEVELS[configured] ?? LEVELS.info;

/** Secret-ish keys that must never reach stdout. */
const REDACT_KEYS = new Set([
  'password',
  'apikey',
  'api_key',
  'authorization',
  'token',
  'secret',
  'connectionstring',
  'connection_string',
]);

/**
 * @param {Record<string, unknown>} fields
 * @returns {Record<string, unknown>}
 */
function redact(fields) {
  /** @type {Record<string, unknown>} */
  const out = {};
  for (const [k, v] of Object.entries(fields)) {
    out[k] = REDACT_KEYS.has(k.toLowerCase().replace(/[^a-z_]/g, '')) ? '[redacted]' : v;
  }
  return out;
}

/**
 * @param {string} level
 * @param {string} event
 * @param {Record<string, unknown>} [fields]
 */
function emit(level, event, fields = {}) {
  if (LEVELS[level] < threshold) return;
  const line = { ts: new Date().toISOString(), level, event, ...redact(fields) };
  const text = JSON.stringify(line);
  if (level === 'error' || level === 'warn') process.stderr.write(`${text}\n`);
  else process.stdout.write(`${text}\n`);
}

export const logger = {
  /** @param {string} event @param {Record<string, unknown>} [f] */
  debug: (event, f) => emit('debug', event, f),
  /** @param {string} event @param {Record<string, unknown>} [f] */
  info: (event, f) => emit('info', event, f),
  /** @param {string} event @param {Record<string, unknown>} [f] */
  warn: (event, f) => emit('warn', event, f),
  /** @param {string} event @param {Record<string, unknown>} [f] */
  error: (event, f) => emit('error', event, f),
};

/**
 * Reduce an error to a short, non-leaking summary suitable for logs and SQL columns.
 * @param {unknown} err
 * @returns {string}
 */
export function redactError(err) {
  if (!err) return 'unknown error';
  const e = /** @type {any} */ (err);
  const name = e.name || 'Error';
  const message = String(e.message || e).slice(0, 300);
  const status = e.status ?? e.statusCode;
  return status ? `${name}(${status}): ${message}` : `${name}: ${message}`;
}
