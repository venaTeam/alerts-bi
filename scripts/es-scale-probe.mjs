// Measures the numbers that size the alerts BI, and reports the two approved
// data-quality diagnostics from design section 3.7 for one selected team.
//
// Read-only — issues aggregations only, never writes. Safe to point at production ECK.
//
//   node scripts/es-scale-probe.mjs --team checkout-api
//   node scripts/es-scale-probe.mjs --team acceptance-core --run-at 2026-08-25T18:00:00Z
//   ES_URL=https://eck.internal:9200 ES_AUTH=user:pass node scripts/es-scale-probe.mjs --team X
//   node scripts/es-scale-probe.mjs --all-teams        (sizing only, no ownership scope)
//
// SCOPE. A run of the BI itself never reads more than one team, so this probe defaults to
// the same discipline: pass --team and it filters by that team's registry operators
// exactly as the pipeline does. --all-teams is available for capacity sizing only, and
// its output is explicitly labelled as not being any team's numbers.
//
// PRECISION. Every count here is built from terms + cardinality, and cardinality is
// HyperLogLog++ and approximate above its precision threshold. These are SIZING figures.
// The pipeline never uses them: it pages every matching row and counts identities exactly,
// because a reported metric may not be approximate.
//
// Reported per schema, matching the approved definitions:
//   node_name_ratio      distinct (application, object/component, node_name)
//                        over distinct (application, object/component),
//                        using ONLY rows with a nonempty node_name on BOTH sides
//   key_inflation_ratio  distinct (application, key_field)
//                        over distinct (application, object/component), over ALL rows
// A zero denominator yields null, never zero.

import { readFileSync } from 'node:fs';

const ES = process.env.ES_URL || 'http://localhost:9200';
const DAYS = Number(process.env.DAYS || 7);
const AUTH = process.env.ES_AUTH;
const PRECISION = 40000;

const args = process.argv.slice(2);
const teamFlagIndex = args.indexOf('--team');
const TEAM = teamFlagIndex === -1 ? null : args[teamFlagIndex + 1];
const ALL_TEAMS = args.includes('--all-teams');

// --run-at freezes the window end, matching how a real run captures run_at once. Without
// it the probe uses a live clock, which finds nothing in a fixed-clock mock dataset.
const runAtIndex = args.indexOf('--run-at');
const RUN_AT = runAtIndex === -1 ? null : args[runAtIndex + 1];
if (RUN_AT && Number.isNaN(Date.parse(RUN_AT))) {
  console.error(`--run-at ${RUN_AT} is not a valid ISO 8601 instant`);
  process.exit(2);
}
const WINDOW = RUN_AT
  ? {
      gte: new Date(Date.parse(RUN_AT) - DAYS * 24 * 3600000).toISOString(),
      lt: new Date(Date.parse(RUN_AT)).toISOString(),
    }
  : { gte: `now-${DAYS}d`, lte: 'now' };

if (!TEAM && !ALL_TEAMS) {
  console.error('usage: node scripts/es-scale-probe.mjs --team <team_id> | --all-teams');
  console.error('  --team      scope to one registry team, exactly as a real run does');
  console.error('  --all-teams company-wide sizing only; not any team\'s reported numbers');
  process.exit(2);
}

/** @type {{v1: string[], v2: string[]}} */
let operators = { v1: [], v2: [] };
if (TEAM) {
  const registry = JSON.parse(readFileSync('config/teams.json', 'utf8'));
  const entry = registry.teams.find((t) => t.team_id === TEAM);
  if (!entry) {
    console.error(`team "${TEAM}" is not in config/teams.json`);
    process.exit(2);
  }
  operators = { v1: entry.v1_operators, v2: entry.v2_operator ? [entry.v2_operator] : [] };
}

async function es(index, body) {
  const headers = { 'Content-Type': 'application/json' };
  if (AUTH) headers.Authorization = `Basic ${Buffer.from(AUTH).toString('base64')}`;
  const res = await fetch(`${ES}/${index}/_search`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${index}: ${res.status} ${await res.text()}`);
  return res.json();
}

/**
 * @param {string[]} teamOperators
 * @returns {object}
 */
function query(teamOperators) {
  /** @type {object[]} */
  const filter = [{ range: { '@timestamp': WINDOW } }];
  if (!ALL_TEAMS) filter.push({ terms: { operator: teamOperators } });
  return { bool: { filter } };
}

// Distinct (application, X) pairs without scripting: bucket by application, count distinct
// X inside each bucket, sum the buckets. Exact on the pairing, approximate only within
// each application's sketch.
function pairAgg(field, appBuckets) {
  return {
    by_app: {
      terms: { field: 'application', size: appBuckets },
      aggs: { d: { cardinality: { field, precision_threshold: PRECISION } } },
    },
  };
}
const sumPairs = (agg) => agg.buckets.reduce((a, b) => a + b.d.value, 0);
const truncated = (agg) => (agg.sum_other_doc_count ?? 0) > 0;

/**
 * A zero denominator is "no eligible rows", which is a different statement from a ratio
 * of zero and must never be printed as one.
 */
const ratio = (numerator, denominator) =>
  denominator === 0 ? null : numerator / denominator;
const show = (value, digits = 3) => (value === null ? 'null (no eligible rows)' : value.toFixed(digits));

async function probe(index, componentField, teamOperators) {
  const scope = ALL_TEAMS ? 'ALL TEAMS (sizing only)' : `team ${TEAM}`;
  const when = RUN_AT ? `${DAYS}d ending ${RUN_AT}` : `last ${DAYS} days, live clock`;
  console.log(`\n${'='.repeat(72)}\n${index}  (${when}, ${scope})\n${'='.repeat(72)}`);

  if (!ALL_TEAMS && teamOperators.length === 0) {
    console.log('  no configured operators for this schema — a real run skips the query entirely');
    return;
  }

  const q = query(teamOperators);

  const head = await es(index, {
    size: 0,
    track_total_hits: true,
    query: q,
    aggs: { apps: { cardinality: { field: 'application', precision_threshold: PRECISION } } },
  });
  const rows = head.hits.total.value;
  const apps = head.aggregations.apps.value;
  const appBuckets = Math.max(apps * 2, 100);

  if (rows === 0) {
    console.log('  no rows in this window');
    return;
  }

  // All-rows aggregates: the key-inflation numerator and denominator.
  const all = await es(index, {
    size: 0,
    query: q,
    aggs: {
      keys: pairAgg('key_field', appBuckets).by_app,
      scopes: pairAgg(componentField, appBuckets).by_app,
    },
  });
  const keyNumerator = sumPairs(all.aggregations.keys);
  const keyDenominator = sumPairs(all.aggregations.scopes);

  // Node-eligible aggregates: ONLY rows with a nonempty node_name, on BOTH sides. A
  // scope that never supplies a node name must not inflate the denominator.
  const nodeQuery = {
    bool: {
      filter: [...q.bool.filter, { exists: { field: 'node_name' } }],
      must_not: [{ term: { node_name: '' } }],
    },
  };
  const nodeAgg = await es(index, {
    size: 0,
    track_total_hits: true,
    query: nodeQuery,
    aggs: {
      // distinct (application, component, node_name): bucket by application, then by
      // component, then count node names.
      by_app: {
        terms: { field: 'application', size: appBuckets },
        aggs: {
          by_component: {
            terms: { field: componentField, size: 10000 },
            aggs: { nodes: { cardinality: { field: 'node_name', precision_threshold: PRECISION } } },
          },
        },
      },
      scopes: pairAgg(componentField, appBuckets).by_app,
    },
  });

  let nodeNumerator = 0;
  for (const appBucket of nodeAgg.aggregations.by_app.buckets) {
    for (const componentBucket of appBucket.by_component.buckets) {
      nodeNumerator += componentBucket.nodes.value;
    }
  }
  const nodeDenominator = sumPairs(nodeAgg.aggregations.scopes);
  const nodeEligibleRows = nodeAgg.hits.total.value;

  if (truncated(all.aggregations.keys)) {
    console.log(`  !! terms agg truncated — more than ${appBuckets} applications. Counts are LOW.`);
  }

  console.log(`  rows in window                     : ${rows.toLocaleString()}`);
  console.log(`  distinct applications              : ${apps.toLocaleString()}`);
  console.log(`  distinct alerts (application+key)  : ${keyNumerator.toLocaleString()}`);
  console.log(`  rows per distinct alert            : ${(rows / Math.max(keyNumerator, 1)).toFixed(1)}`);
  console.log('');
  console.log(`  node_name_ratio                    : ${show(ratio(nodeNumerator, nodeDenominator))}`);
  console.log(`     numerator  (app,${componentField},node) : ${nodeNumerator.toLocaleString()}`);
  console.log(`     denominator(app,${componentField})      : ${nodeDenominator.toLocaleString()}   [nonempty-node rows only: ${nodeEligibleRows.toLocaleString()}]`);
  console.log(`  key_inflation_ratio                : ${show(ratio(keyNumerator, keyDenominator))}`);
  console.log(`     numerator  (app,key_field)      : ${keyNumerator.toLocaleString()}`);
  console.log(`     denominator(app,${componentField})      : ${keyDenominator.toLocaleString()}   [all rows]`);
  console.log('');
  console.log('  Read together: both high suggests node names drive key inflation; high key');
  console.log('  inflation with a low node ratio points at other identity fields; a high node');
  console.log('  ratio with low key inflation means many nodes exist without equivalent key');
  console.log('  growth. Neither ratio contributes to flagged.');

  // Distinct per day, which is how every distinct figure is published.
  const daily = await es(index, {
    size: 0,
    query: q,
    aggs: {
      per_day: {
        date_histogram: { field: '@timestamp', calendar_interval: 'day', min_doc_count: 1 },
        aggs: pairAgg('key_field', appBuckets),
      },
    },
  });
  const buckets = daily.aggregations.per_day.buckets;
  if (buckets.length) {
    const perDay = buckets.map((b) => sumPairs(b.by_app));
    const sumDays = perDay.reduce((a, b) => a + b, 0);
    console.log('');
    console.log(`  sum of daily distincts             : ${sumDays.toLocaleString()} over ${buckets.length} day(s) with data`);
    console.log(`  distinct alerts per day            : ${(sumDays / 7).toFixed(2)}   [sum / 7, the published rate]`);
    console.log(`  distinct over the whole window     : ${keyNumerator.toLocaleString()}   [internal dedup only, never published]`);
    console.log(`  key reuse ratio                    : ${(sumDays / Math.max(keyNumerator, 1)).toFixed(1)}x`);
    console.log('     ~1x -> keys are nearly all new each day, so a cross-run verdict cache rarely hits.');
  }
}

await probe('appchi-v1', 'object', operators.v1);
await probe('appchi-v2', 'component', operators.v2);

console.log(`\nEvery figure above is APPROXIMATE: cardinality is HyperLogLog++ above ${PRECISION.toLocaleString()}`);
console.log('per bucket. This probe sizes the problem; it is not the measurement contract.');
console.log('The pipeline pages every matching row and counts identities exactly.');
