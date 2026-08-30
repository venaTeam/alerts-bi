import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { sql, openPool, request } from './pool.js';
import { sha256Text } from '../util/hash.js';
import { logger } from '../util/logger.js';

/**
 * Migration runner.
 *
 * Production uses these same files with an externally supplied connection string; tests
 * do not substitute SQLite or another engine, because a constraint that only exists in
 * one dialect is a constraint that was never tested.
 */

// The SQL migrations are a language-neutral asset and now live with the Python package
// during the port. This file is the superseded JavaScript reference implementation; it
// reads the same single canonical directory so both implementations stay in step and the
// parity comparison is against identical schema.
const MIGRATIONS_DIR = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  'alerts_bi',
  'db',
  'migrations',
);

/**
 * @typedef {object} MigrationFile
 * @property {string} version
 * @property {string} filename
 * @property {string} sqlText
 * @property {string} checksum
 */

/**
 * Read migrations from disk in version order.
 * @returns {MigrationFile[]}
 */
export function loadMigrations() {
  return readdirSync(MIGRATIONS_DIR)
    .filter((f) => f.endsWith('.sql'))
    .sort()
    .map((filename) => {
      const sqlText = readFileSync(path.join(MIGRATIONS_DIR, filename), 'utf8');
      return {
        version: filename.replace(/\.sql$/, ''),
        filename,
        sqlText,
        checksum: sha256Text(sqlText.replace(/\r\n/g, '\n')),
      };
    });
}

/**
 * @param {any} pool
 * @returns {Promise<void>}
 */
async function ensureMigrationsTable(pool) {
  await request(pool).query(`
    IF OBJECT_ID('schema_migrations', 'U') IS NULL
    CREATE TABLE schema_migrations (
      version    NVARCHAR(128) NOT NULL CONSTRAINT pk_schema_migrations PRIMARY KEY,
      checksum   NVARCHAR(64)  NOT NULL,
      applied_at DATETIME2(3)  NOT NULL
    );
  `);
}

/**
 * @param {any} pool
 * @returns {Promise<Map<string, string>>} version -> checksum
 */
export async function appliedMigrations(pool) {
  await ensureMigrationsTable(pool);
  const result = await request(pool).query('SELECT version, checksum FROM schema_migrations');
  return new Map(result.recordset.map((r) => [r.version, r.checksum]));
}

/**
 * Apply every migration not yet recorded.
 *
 * A migration whose file changed after being applied is a hard error: silently
 * re-applying or ignoring it would leave the database in a state no version describes.
 *
 * @param {any} pool
 * @returns {Promise<{applied: string[], alreadyApplied: string[]}>}
 */
export async function migrate(pool) {
  const applied = await appliedMigrations(pool);
  const files = loadMigrations();

  /** @type {string[]} */
  const newlyApplied = [];
  /** @type {string[]} */
  const alreadyApplied = [];

  for (const migration of files) {
    const existing = applied.get(migration.version);
    if (existing !== undefined) {
      if (existing !== migration.checksum) {
        throw new Error(
          `migration ${migration.filename} was already applied but its contents have changed ` +
            '(add a new migration instead of editing an applied one)',
        );
      }
      alreadyApplied.push(migration.version);
      continue;
    }

    // Each migration runs in its own transaction: a failure leaves the previous ones
    // applied and recorded, so a rerun resumes rather than starting over.
    const transaction = new sql.Transaction(pool);
    await transaction.begin();
    try {
      // GO is a batch separator understood by sqlcmd, not by the driver.
      for (const batch of migration.sqlText.split(/^\s*GO\s*$/im)) {
        if (batch.trim() === '') continue;
        await request(transaction).batch(batch);
      }
      await request(transaction, {
        version: { type: sql.NVarChar(128), value: migration.version },
        checksum: { type: sql.NVarChar(64), value: migration.checksum },
        appliedAt: { type: sql.DateTime2, value: new Date() },
      }).query(
        'INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (@version, @checksum, @appliedAt)',
      );
      await transaction.commit();
      newlyApplied.push(migration.version);
      logger.info('db.migration_applied', { version: migration.version });
    } catch (err) {
      await transaction.rollback();
      throw new Error(
        `migration ${migration.filename} failed: ${/** @type {any} */ (err).message}`,
      );
    }
  }

  return { applied: newlyApplied, alreadyApplied };
}

/**
 * Create the target database if it does not exist, then apply migrations to it.
 *
 * @param {import('../config/env.js').SqlConfig} config
 * @param {string} database
 * @returns {Promise<{applied: string[], alreadyApplied: string[]}>}
 */
export async function migrateDatabase(config, database) {
  const master = await openPool(config, 'master');
  try {
    const exists = await request(master, {
      name: { type: sql.NVarChar(128), value: database },
    }).query('SELECT 1 AS present FROM sys.databases WHERE name = @name');
    if (exists.recordset.length === 0) {
      // The database name is validated before reaching here; sys.databases cannot be
      // queried with a parameterized CREATE, so the identifier is quoted defensively.
      await request(master).batch(`CREATE DATABASE ${quoteIdentifier(database)}`);
      logger.info('db.database_created', { database });
    }
  } finally {
    await master.close();
  }

  const pool = await openPool(config, database);
  try {
    return await migrate(pool);
  } finally {
    await pool.close();
  }
}

/**
 * Quote a SQL Server identifier, rejecting anything that is not a plain name.
 *
 * Database names reach this from configuration, and the drop path below is destructive,
 * so the safe set is deliberately narrow.
 *
 * @param {string} name
 * @returns {string}
 */
export function quoteIdentifier(name) {
  if (!/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(name)) {
    throw new Error(`unsafe SQL identifier: ${JSON.stringify(name)}`);
  }
  return `[${name}]`;
}

/**
 * Drop and recreate ONLY the explicitly named disposable test database.
 *
 * Guarded on purpose: this is the one destructive operation in the codebase, and the
 * repository instructions require verifying the target before recreating anything. It
 * refuses any database whose name is not the configured test database, so a mistyped
 * environment variable cannot take out `alerts_bi_dev` or a production store.
 *
 * @param {import('../config/env.js').SqlConfig} config
 * @param {string} database
 * @returns {Promise<void>}
 */
export async function resetTestDatabase(config, database) {
  if (database !== config.testDatabase) {
    throw new Error(
      `refusing to reset ${JSON.stringify(database)}: only the configured test database ` +
        `(${JSON.stringify(config.testDatabase)}) may be recreated`,
    );
  }
  if (!/test/i.test(database)) {
    throw new Error(
      `refusing to reset ${JSON.stringify(database)}: a disposable test database name must contain "test"`,
    );
  }

  const master = await openPool(config, 'master');
  try {
    const quoted = quoteIdentifier(database);
    // Single-user mode rolls back open sessions so the drop cannot hang behind a
    // connection left over from an interrupted test run.
    await request(master).batch(`
      IF EXISTS (SELECT 1 FROM sys.databases WHERE name = N'${database}')
      BEGIN
        ALTER DATABASE ${quoted} SET SINGLE_USER WITH ROLLBACK IMMEDIATE;
        DROP DATABASE ${quoted};
      END
    `);
    await request(master).batch(`CREATE DATABASE ${quoted}`);
    logger.info('db.test_database_recreated', { database });
  } finally {
    await master.close();
  }

  const pool = await openPool(config, database);
  try {
    await migrate(pool);
  } finally {
    await pool.close();
  }
}
