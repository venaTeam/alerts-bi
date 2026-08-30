import path from 'node:path';
import { loadConfig } from '../config/env.js';
import { openPool } from '../db/pool.js';
import { persistRun, getLatestRun } from '../db/repositories.js';
import { executeRun, selectLlmClient } from './orchestrator.js';
import { renderRunReport } from '../report/render.js';
import { FakeLlmClient } from '../llm/client-fake.js';
import { logger, redactError } from '../util/logger.js';

/**
 * CLI command handlers for `run` and `report`.
 *
 * Failure behaviour follows the blueprint exactly: an invalid registry fails before any
 * alert query, a failed Elasticsearch query fails the run rather than publishing a partial
 * scorecard as complete, a failed persistence step fails the run rather than rendering
 * from memory, and a rendering failure after persistence leaves the analysis committed so
 * rendering can be retried.
 */

/**
 * @param {Record<string, string|boolean>} flags
 * @param {string} name
 * @returns {string|undefined}
 */
function stringFlag(flags, name) {
  const value = flags[name];
  return typeof value === 'string' ? value : undefined;
}

/**
 * @param {string} command
 * @param {Record<string, string|boolean>} flags
 * @returns {Promise<number>}
 */
export async function runCommand(command, flags) {
  const config = loadConfig();

  if (command === 'run') return commandRun(config, flags);
  if (command === 'report') return commandReport(config, flags);
  if (command === 'verify-acceptance') return commandVerify(flags);

  process.stderr.write(`unknown command: ${command}\n`);
  return 2;
}

/**
 * @param {import('../config/env.js').AppConfig} config
 * @param {Record<string, string|boolean>} flags
 * @returns {Promise<number>}
 */
async function commandRun(config, flags) {
  const teamId = stringFlag(flags, 'team');
  if (!teamId) {
    // Never defaults to all teams: an implicit fan-out would be a scope change hiding in
    // a convenience.
    process.stderr.write('--team is required; a run always names exactly one team\n');
    return 2;
  }

  const runAtFlag = stringFlag(flags, 'run-at');
  const runAt = runAtFlag ? new Date(runAtFlag) : new Date();
  if (Number.isNaN(runAt.getTime())) {
    process.stderr.write(`--run-at ${JSON.stringify(runAtFlag)} is not a valid ISO 8601 instant\n`);
    return 2;
  }

  // The deterministic fake is opt-in and explicit, so a mock run can never be mistaken
  // for a live one: it stamps its own model_version onto the run record.
  const useFake = flags['fake-llm'] === true;
  const { client, reason } = useFake
    ? { client: new FakeLlmClient(), reason: null }
    : selectLlmClient(config, { llm: flags['no-llm'] !== true });

  const pool = await openPool(config.sql, stringFlag(flags, 'database') ?? config.sql.database);
  try {
    const { payload, summary } = await executeRun({
      teamId,
      runAt,
      config,
      registryPath: stringFlag(flags, 'registry'),
      llmClient: client,
      llmDisabledReason: reason,
      pool,
    });

    await persistRun(pool, payload);
    logger.info('run.persisted', { run_id: summary.runId, team_id: summary.teamId });

    const outDir = stringFlag(flags, 'out') ?? path.join('out', summary.runId.slice(0, 16));
    const { files } = await renderRunReport(pool, summary.runId, outDir);

    process.stdout.write(
      [
        `run_id:        ${summary.runId}`,
        `team:          ${summary.teamId}`,
        `phase:         ${summary.phase}`,
        `readiness:     ${summary.readiness === null ? 'n/a (no v2 identities)' : `${summary.readiness.toFixed(1)}%`}`,
        `v1:            ${summary.v1Rows} rows / ${summary.v1Identities} distinct`,
        `v2:            ${summary.v2Rows} rows / ${summary.v2Identities} distinct`,
        `llm assessed:  ${summary.llmAssessed ? 'yes' : 'no'} (${summary.llmEligible} eligible identities)`,
        '',
        ...files.map((f) => `wrote ${f}`),
        '',
      ].join('\n'),
    );
    return 0;
  } finally {
    await pool.close();
  }
}

/**
 * Re-render a stored run. Useful precisely because rendering is a separate, retryable
 * step from persistence.
 *
 * @param {import('../config/env.js').AppConfig} config
 * @param {Record<string, string|boolean>} flags
 * @returns {Promise<number>}
 */
async function commandReport(config, flags) {
  const runId = stringFlag(flags, 'run-id');
  const teamId = stringFlag(flags, 'team');
  if (!runId && !teamId) {
    process.stderr.write('--run-id or --team is required\n');
    return 2;
  }

  const pool = await openPool(config.sql, stringFlag(flags, 'database') ?? config.sql.database);
  try {
    let targetRunId = runId;
    if (!targetRunId) {
      const latest = await getLatestRun(pool, /** @type {string} */ (teamId));
      if (!latest) {
        process.stderr.write(`no completed run stored for team ${teamId}\n`);
        return 1;
      }
      targetRunId = latest.run_id;
    }

    const outDir = stringFlag(flags, 'out') ?? path.join('out', targetRunId.slice(0, 16));
    const { files } = await renderRunReport(pool, targetRunId, outDir);
    process.stdout.write(`${files.map((f) => `wrote ${f}`).join('\n')}\n`);
    return 0;
  } catch (err) {
    logger.error('report.failed', { error: redactError(err) });
    throw err;
  } finally {
    await pool.close();
  }
}

/**
 * Compare persisted SQL rows and rendered CSV exports against the hand-reviewed
 * acceptance manifest.
 *
 * @param {Record<string, string|boolean>} flags
 * @returns {Promise<number>}
 */
async function commandVerify(flags) {
  const { verifyAcceptance } = await import('./verify.js');
  const result = await verifyAcceptance({
    manifestPath: stringFlag(flags, 'manifest'),
    outDir: stringFlag(flags, 'out'),
    database: stringFlag(flags, 'database'),
  });

  if (result.ok) {
    process.stdout.write(`acceptance verification passed: ${result.checks} checks
`);
    return 0;
  }

  process.stderr.write(
    `acceptance verification FAILED: ${result.failures.length} of ${result.checks} checks

`,
  );
  for (const failure of result.failures) {
    process.stderr.write(
      `  ${failure.where}
    expected: ${JSON.stringify(failure.expected)}
    actual:   ${JSON.stringify(failure.actual)}
`,
    );
  }
  return 1;
}
