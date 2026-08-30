import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';

/**
 * Minimal .env loader.
 *
 * A dependency is not warranted: the file format used here is `KEY=value` with optional
 * `#` comments and optional surrounding quotes. Real process environment always wins, so
 * CI and production can supply configuration without a file present.
 *
 * @param {string} [file]
 * @returns {void}
 */
export function loadDotEnv(file = path.resolve(process.cwd(), '.env')) {
  if (!existsSync(file)) return;
  const text = readFileSync(file, 'utf8');
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    if (!key || key in process.env) continue;
    let value = line.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"') && value.length > 1) ||
      (value.startsWith("'") && value.endsWith("'") && value.length > 1)
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}

/**
 * @param {string} name
 * @param {string} [fallback]
 * @returns {string}
 */
function str(name, fallback = '') {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

/**
 * @param {string} name
 * @param {number} fallback
 * @returns {number}
 */
function num(name, fallback) {
  const v = process.env[name];
  if (v === undefined || v === '') return fallback;
  const n = Number(v);
  if (!Number.isFinite(n))
    throw new Error(`env ${name} must be a number, got ${JSON.stringify(v)}`);
  return n;
}

/**
 * @param {string} name
 * @param {boolean} fallback
 * @returns {boolean}
 */
function bool(name, fallback) {
  const v = process.env[name];
  if (v === undefined || v === '') return fallback;
  return ['1', 'true', 'yes', 'on'].includes(v.toLowerCase());
}

/**
 * @typedef {object} EsConfig
 * @property {string} url
 * @property {string} username
 * @property {string} password
 * @property {string} caCert
 * @property {number} requestTimeoutMs
 * @property {number} pageSize
 */

/**
 * @typedef {object} SqlConfig
 * @property {string} host
 * @property {number} port
 * @property {string} user
 * @property {string} password
 * @property {string} database
 * @property {string} testDatabase
 * @property {boolean} encrypt
 * @property {boolean} trustServerCertificate
 * @property {number} requestTimeoutMs
 */

/**
 * @typedef {object} LlmConfig
 * @property {boolean} enabled
 * @property {string} baseUrl
 * @property {string} apiKey
 * @property {string} model
 * @property {number} timeoutMs
 * @property {number} maxBatchSize
 * @property {boolean} liveTest
 */

/**
 * @typedef {object} AppConfig
 * @property {EsConfig} es
 * @property {SqlConfig} sql
 * @property {LlmConfig} llm
 */

/** Hard ceiling from design section 5.1. Configuration may lower it, never raise it. */
export const MAX_BATCH_SIZE_CEILING = 200;

/**
 * Build the application configuration from the environment.
 * @returns {AppConfig}
 */
export function loadConfig() {
  loadDotEnv();

  const maxBatchSize = num('LLM_MAX_BATCH_SIZE', MAX_BATCH_SIZE_CEILING);
  if (!Number.isInteger(maxBatchSize) || maxBatchSize < 1) {
    throw new Error('LLM_MAX_BATCH_SIZE must be a positive integer');
  }
  if (maxBatchSize > MAX_BATCH_SIZE_CEILING) {
    // Section 5.1 fixes 200 as a hard count ceiling. 7.2 allows lowering it after
    // capacity measurement, never raising it.
    throw new Error(
      `LLM_MAX_BATCH_SIZE ${maxBatchSize} exceeds the design ceiling of ${MAX_BATCH_SIZE_CEILING}`,
    );
  }

  return {
    es: {
      url: str('ES_URL', 'http://localhost:9200').replace(/\/+$/, ''),
      username: str('ES_USERNAME'),
      password: str('ES_PASSWORD'),
      caCert: str('ES_CA_CERT'),
      requestTimeoutMs: num('ES_REQUEST_TIMEOUT_MS', 60000),
      pageSize: num('ES_PAGE_SIZE', 1000),
    },
    sql: {
      host: str('SQL_HOST', 'localhost'),
      port: num('SQL_PORT', 1433),
      user: str('SQL_USER', 'sa'),
      password: str('SQL_PASSWORD'),
      database: str('SQL_DATABASE', 'alerts_bi_dev'),
      testDatabase: str('SQL_TEST_DATABASE', 'alerts_bi_test'),
      encrypt: bool('SQL_ENCRYPT', false),
      trustServerCertificate: bool('SQL_TRUST_SERVER_CERTIFICATE', true),
      requestTimeoutMs: num('SQL_REQUEST_TIMEOUT_MS', 60000),
    },
    llm: {
      enabled: bool('LLM_ENABLED', false),
      baseUrl: str('LLM_BASE_URL'),
      apiKey: str('LLM_API_KEY'),
      model: str('LLM_MODEL'),
      timeoutMs: num('LLM_TIMEOUT_MS', 120000),
      maxBatchSize,
      liveTest: bool('LLM_LIVE_TEST', false),
    },
  };
}
