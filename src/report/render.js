import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import {
  getRun,
  getDailyMetrics,
  getRuleCounts,
  getFindings,
  getRunPanels,
  getBatchAttempts,
} from '../db/repositories.js';
import { renderScorecard } from './html.js';
import { dailyMetricsCsv, ruleCountsCsv, alertWorklistCsv } from './csv.js';
import { logger } from '../util/logger.js';

/**
 * Report rendering (flow step 9).
 *
 * Reads ONLY from SQL Server. Nothing is recomputed from Elasticsearch, and nothing is
 * rendered from in-memory pipeline results: a report that could be produced without the
 * store would make the store optional, and the store is the one part of this design that
 * cannot be skipped.
 *
 * A rendering failure after persistence is recoverable - the analysis is committed, so
 * rendering can simply be retried against the same run id.
 */

/** The exact file contract. Nothing else is written. */
export const OUTPUT_FILES = Object.freeze([
  'scorecard.html',
  'daily_metrics.csv',
  'rule_counts.csv',
  'alert_worklist.csv',
]);

/**
 * @param {any} pool
 * @param {string} runId
 * @param {string} outDir
 * @returns {Promise<{files: string[], run: any}>}
 */
export async function renderRunReport(pool, runId, outDir) {
  const run = await getRun(pool, runId);
  if (!run) throw new Error(`run ${runId} is not in the store; nothing to render`);

  const [daily, ruleCounts, findings, panels, attempts] = await Promise.all([
    getDailyMetrics(pool, runId),
    getRuleCounts(pool, runId),
    getFindings(pool, runId),
    getRunPanels(pool, runId),
    getBatchAttempts(pool, runId),
  ]);

  mkdirSync(outDir, { recursive: true });

  /** @type {Array<[string, string]>} */
  const outputs = [
    ['scorecard.html', renderScorecard(run, daily, ruleCounts, findings, panels, attempts)],
    ['daily_metrics.csv', dailyMetricsCsv(daily)],
    ['rule_counts.csv', ruleCountsCsv(ruleCounts)],
    ['alert_worklist.csv', alertWorklistCsv(findings)],
  ];

  const files = outputs.map(([name, content]) => {
    const file = path.join(outDir, name);
    writeFileSync(file, content, 'utf8');
    return file;
  });

  logger.info('report.rendered', { run_id: runId, out_dir: outDir, files: files.length });
  return { files, run };
}
