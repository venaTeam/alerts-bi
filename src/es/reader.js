import { ElasticsearchError } from './client.js';
import { normalizeRow } from '../domain/normalize.js';
import { logger } from '../util/logger.js';

/**
 * Team-scoped Elasticsearch retrieval (design section 6; flow step 2).
 *
 * Two separate queries, each restricted to the selected team's configured operators and
 * to the exact run window. The queries do not restrict `application`, do not apply the
 * team's own panel filters, do not read the SQL hot table, and never sweep another team's
 * alerts.
 *
 * Elasticsearch row ids are read but never persisted as business identity: source
 * documents expire after three months, so an id is not a durable reference.
 */

export const V1_INDEX = 'appchi-v1';
export const V2_INDEX = 'appchi-v2';

/**
 * @typedef {import('./client.js').EsClient} EsClient
 * @typedef {import('../domain/normalize.js').AlertRecord} AlertRecord
 * @typedef {import('../domain/window.js').RunWindow} RunWindow
 */

/**
 * @typedef {object} ReadResult
 * @property {AlertRecord[]} rows
 * @property {number} pages
 * @property {number} reportedTotal Total hits Elasticsearch reported, for a paging check.
 */

/**
 * Build the query body for one schema.
 *
 * The window is the exact half-open range: `gte` window_start, `lt` window_end. Using
 * `lte` would double-count the boundary instant across two adjacent runs.
 *
 * `operator` is a keyword field, so `terms` matches the exact, case-sensitive values from
 * the registry — which is what makes `Checkout-API` and `checkout` two distinct entries a
 * team must list separately.
 *
 * @param {string[]} operators
 * @param {RunWindow} window
 * @returns {Record<string, unknown>}
 */
export function buildQuery(operators, window) {
  return {
    bool: {
      filter: [
        { terms: { operator: operators } },
        {
          range: {
            '@timestamp': {
              gte: window.windowStart.toISOString(),
              lt: window.windowEnd.toISOString(),
              format: 'strict_date_optional_time',
            },
          },
        },
      ],
    },
  };
}

/**
 * Read every matching row for one schema, paging with a point-in-time and `search_after`.
 *
 * Every row is returned rather than aggregated in the cluster: distinct counts must be
 * exact, and `cardinality` is HyperLogLog++ and approximate above its threshold. Counting
 * identities in Node over the complete paged result is exact by construction.
 *
 * @param {EsClient} client
 * @param {'v1'|'v2'} schema
 * @param {string[]} operators
 * @param {RunWindow} window
 * @param {{pageSize?: number}} [options]
 * @returns {Promise<ReadResult>}
 */
export async function readSchema(client, schema, operators, window, options = {}) {
  const index = schema === 'v1' ? V1_INDEX : V2_INDEX;
  const pageSize = options.pageSize ?? client.config.pageSize;

  if (operators.length === 0) {
    // Not an error: a migrated team has no v1 operators, and a pre-migration team has no
    // v2 operator. Querying with an empty terms list would match nothing anyway, but
    // skipping makes the intent explicit in the logs.
    logger.info('es.skip_schema', { schema, reason: 'no configured operators' });
    return { rows: [], pages: 0, reportedTotal: 0 };
  }

  const query = buildQuery(operators, window);
  const pitId = await client.openPit(index);
  /** @type {AlertRecord[]} */
  const rows = [];
  let pages = 0;
  let reportedTotal = 0;
  /** @type {unknown[]|undefined} */
  let searchAfter;

  try {
    for (;;) {
      /** @type {Record<string, unknown>} */
      const body = {
        size: pageSize,
        track_total_hits: true,
        query,
        // _shard_doc is the PIT tiebreaker: it guarantees a total order, so no document
        // is skipped or repeated across pages even when timestamps collide.
        sort: [{ '@timestamp': 'asc' }, { _shard_doc: 'asc' }],
        pit: { id: pitId, keep_alive: '2m' },
      };
      if (searchAfter) body.search_after = searchAfter;

      const res = await client.search(body);
      const hits = res?.hits?.hits ?? [];
      if (pages === 0) reportedTotal = res?.hits?.total?.value ?? 0;
      pages += 1;

      for (const hit of hits) {
        rows.push(normalizeRow(schema, hit._source));
      }

      if (hits.length < pageSize) break;
      searchAfter = hits[hits.length - 1].sort;
      if (!searchAfter) {
        throw new ElasticsearchError(
          `page ${pages} of ${index} returned no sort values, so paging cannot continue safely`,
        );
      }
    }
  } finally {
    await client.closePit(pitId);
  }

  // A mismatch means paging lost or duplicated rows, which would silently corrupt every
  // number downstream. Fail rather than publish a partial team scorecard as complete.
  if (rows.length !== reportedTotal) {
    throw new ElasticsearchError(
      `${index}: retrieved ${rows.length} rows but the cluster reported ${reportedTotal} matching`,
    );
  }

  logger.info('es.read_schema', {
    schema,
    index,
    operators: operators.length,
    rows: rows.length,
    pages,
  });
  return { rows, pages, reportedTotal };
}

/**
 * Read both schemas for one selected team.
 *
 * @param {EsClient} client
 * @param {import('../registry/registry.js').TeamEntry} team
 * @param {RunWindow} window
 * @param {{pageSize?: number}} [options]
 * @returns {Promise<{v1: ReadResult, v2: ReadResult}>}
 */
export async function readTeamAlerts(client, team, window, options = {}) {
  const v2Operators = team.v2_operator === null ? [] : [team.v2_operator];
  const v1 = await readSchema(client, 'v1', team.v1_operators, window, options);
  const v2 = await readSchema(client, 'v2', v2Operators, window, options);
  return { v1, v2 };
}
