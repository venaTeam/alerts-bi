#!/usr/bin/env node
import { realpathSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { loadConfig } from './config/env.js';
import {
  migrateDatabase,
  appliedMigrations,
  loadMigrations,
  resetTestDatabase,
} from './db/migrate.js';
import { openPool } from './db/pool.js';
import { logger, redactError } from './util/logger.js';
import { APP_VERSION } from './versions.js';

/**
 * Alerts BI command line.
 *
 * A run always names one team. There is deliberately no "all teams" mode: the MVP reports
 * one selected team per run, and a default that fanned out would be a scope change hiding
 * in a convenience.
 */

const USAGE = `alerts-bi ${APP_VERSION}

Usage:
  alerts-bi db migrate [--database <name>]
  alerts-bi db status [--database <name>]
  alerts-bi db reset-test

Options:
  --database    Target database. Default: SQL_DATABASE from the environment.
`;

/**
 * Parse `--flag value` and `--flag` arguments.
 * @param {string[]} argv
 * @returns {{positional: string[], flags: Record<string, string|boolean>}}
 */
export function parseArgs(argv) {
  /** @type {string[]} */
  const positional = [];
  /** @type {Record<string, string|boolean>} */
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (!arg.startsWith('--')) {
      positional.push(arg);
      continue;
    }
    const name = arg.slice(2);
    const next = argv[i + 1];
    if (next === undefined || next.startsWith('--')) {
      flags[name] = true;
    } else {
      flags[name] = next;
      i += 1;
    }
  }
  return { positional, flags };
}

/**
 * @param {Record<string, string|boolean>} flags
 * @param {import('./config/env.js').SqlConfig} sqlConfig
 * @returns {string}
 */
function targetDatabase(flags, sqlConfig) {
  const database = flags.database;
  return typeof database === 'string' ? database : sqlConfig.database;
}

/**
 * @param {string[]} positional
 * @param {Record<string, string|boolean>} flags
 * @returns {Promise<number>}
 */
async function commandDb(positional, flags) {
  const config = loadConfig();
  const subcommand = positional[0];

  if (subcommand === 'migrate') {
    const database = targetDatabase(flags, config.sql);
    const result = await migrateDatabase(config.sql, database);
    logger.info('db.migrate_complete', {
      database,
      applied: result.applied.length,
      alreadyApplied: result.alreadyApplied.length,
    });
    for (const version of result.applied) process.stdout.write(`applied ${version}\n`);
    if (result.applied.length === 0) process.stdout.write('database is up to date\n');
    return 0;
  }

  if (subcommand === 'status') {
    const database = targetDatabase(flags, config.sql);
    const pool = await openPool(config.sql, database);
    try {
      const applied = await appliedMigrations(pool);
      const files = loadMigrations();
      process.stdout.write(`database: ${database}\n`);
      for (const migration of files) {
        const state = applied.has(migration.version)
          ? applied.get(migration.version) === migration.checksum
            ? 'applied'
            : 'APPLIED BUT FILE CHANGED'
          : 'pending';
        process.stdout.write(`  ${migration.version.padEnd(32)} ${state}\n`);
      }
    } finally {
      await pool.close();
    }
    return 0;
  }

  if (subcommand === 'reset-test') {
    // Only ever the configured disposable test database; resetTestDatabase refuses
    // anything else.
    await resetTestDatabase(config.sql, config.sql.testDatabase);
    process.stdout.write(`recreated ${config.sql.testDatabase}\n`);
    return 0;
  }

  process.stderr.write(`unknown db subcommand: ${subcommand ?? '(none)'}\n\n${USAGE}`);
  return 2;
}

/**
 * @param {string[]} argv
 * @returns {Promise<number>}
 */
export async function main(argv) {
  const { positional, flags } = parseArgs(argv);
  const command = positional[0];

  if (!command || flags.help || command === 'help') {
    process.stdout.write(USAGE);
    return command ? 0 : 2;
  }

  switch (command) {
    case 'db':
      return commandDb(positional.slice(1), flags);
    default:
      process.stderr.write(`unknown command: ${command}\n\n${USAGE}`);
      return 2;
  }
}

/** True when this file is the process entry point rather than an imported module. */
const isEntryPoint =
  process.argv[1] !== undefined &&
  import.meta.url === pathToFileURL(realpathSync(process.argv[1])).href;

if (isEntryPoint) {
  main(process.argv.slice(2))
    .then((code) => {
      process.exitCode = code;
    })
    .catch((err) => {
      logger.error('cli.failed', { error: redactError(err) });
      process.stderr.write(`${/** @type {any} */ (err).message}\n`);
      process.exitCode = 1;
    });
}
