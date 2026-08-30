import mssqlModule from 'mssql';
import { logger, redactError } from '../util/logger.js';

/** mssql is CommonJS; normalize the interop shape once. */
export const sql = /** @type {any} */ (mssqlModule).default ?? /** @type {any} */ (mssqlModule);

/**
 * SQL Server connection management.
 *
 * The store is the non-negotiable part of this design: Elasticsearch retains three months
 * and the pre-project period is expiring at a rate of one day per day, so a run that
 * cannot persist has not done its job. Reports are therefore rendered only from committed
 * rows, never from in-memory results.
 */

/**
 * @param {import('../config/env.js').SqlConfig} config
 * @param {string} [database] Overrides the configured database (used for `master` and the
 *   disposable test database).
 * @returns {Record<string, unknown>}
 */
export function buildConnectionConfig(config, database) {
  return {
    server: config.host,
    port: config.port,
    user: config.user,
    password: config.password,
    database: database ?? config.database,
    requestTimeout: config.requestTimeoutMs,
    connectionTimeout: config.requestTimeoutMs,
    options: {
      encrypt: config.encrypt,
      trustServerCertificate: config.trustServerCertificate,
      enableArithAbort: true,
    },
    pool: { max: 10, min: 0, idleTimeoutMillis: 30000 },
  };
}

/**
 * Open a connection pool.
 *
 * @param {import('../config/env.js').SqlConfig} config
 * @param {string} [database]
 * @returns {Promise<any>}
 */
export async function openPool(config, database) {
  const pool = new sql.ConnectionPool(buildConnectionConfig(config, database));
  pool.on('error', (err) => logger.error('sql.pool_error', { error: redactError(err) }));
  await pool.connect();
  return pool;
}

/**
 * Run a function inside a transaction, rolling back on any error.
 *
 * Persistence is all-or-nothing per run: a half-written run would be indistinguishable
 * from a complete one when the report is rendered from SQL.
 *
 * @template T
 * @param {any} pool
 * @param {(tx: any) => Promise<T>} fn
 * @returns {Promise<T>}
 */
export async function withTransaction(pool, fn) {
  const transaction = new sql.Transaction(pool);
  await transaction.begin();
  try {
    const result = await fn(transaction);
    await transaction.commit();
    return result;
  } catch (err) {
    try {
      await transaction.rollback();
    } catch (rollbackErr) {
      logger.error('sql.rollback_failed', { error: redactError(rollbackErr) });
    }
    throw err;
  }
}

/**
 * Build a parameterized request. Values are always bound, never interpolated.
 *
 * @param {any} target Pool or transaction.
 * @param {Record<string, {type: any, value: unknown}>} [params]
 * @returns {any}
 */
export function request(target, params = {}) {
  const req = new sql.Request(target);
  for (const [name, { type, value }] of Object.entries(params)) {
    req.input(name, type, value);
  }
  return req;
}
